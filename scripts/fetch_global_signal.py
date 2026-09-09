"""
Phase A of the predictive-analytics expansion (per the LNDC Industrial
Intelligence Radar diagnostic report, Chapter 8.2) — global signal
monitoring, NOT scoped to "Lesotho" appearing in the article.

WHY THIS IS A SEPARATE SCRIPT, NOT AN EXTENSION OF fetch_gdelt_general.py:
that script's every query requires "Lesotho" in the text, which is
exactly right for tracking Lesotho's own situation but structurally
cannot catch a shift before it reaches Lesotho -- the report's own
illustrative Radar questions ("what global apparel-sourcing shifts could
create relocation opportunities for Lesotho?") are about signal that
hasn't mentioned Lesotho yet. This script tracks the global trend
instead, then flags (via lib/peer_relevance.py) whether a directly
comparable country is named, for a human reviewer to weigh.

QUERY SIZING -- reused, not re-derived: fetch_gdelt_general.py's own
docstring records a real, evidence-based lesson: a single big query with
~30 OR'd terms returned "Your query was too short or too long" (a real
GDELT complexity/length limit), fixed by splitting into several queries
each sized like the automotive pilot's proven ~12-terms-across-two-
clauses query. Every bucket below is deliberately sized to 8-10 terms
total, safely inside that proven-working range, not a fresh guess at a
different, unverified size.

HONEST LIMITATION, STATED PLAINLY: this build environment has no network
access to api.gdeltproject.org (confirmed directly for the existing
GDELT scripts earlier in this project), so while the underlying fetch
mechanism below is copied verbatim from fetch_gdelt_general.py's
already-proven, already-working retry/backoff logic, these SPECIFIC new
query strings have never received a real response. Treat the first real
run's Actions log as the actual test of whether each bucket returns
genuinely relevant results, the same discipline every other new source
in this project has gone through -- and be ready to revise a bucket's
wording if it returns mostly noise, the same way the general query was
revised after its first real failure.

Writes to data/news/global_signal_log.csv -- a new, separate file from
general_signal_log.csv, since these serve genuinely different purposes
(global foresight vs. Lesotho-specific monitoring) and mixing them would
make both harder to reason about.
"""
import csv
import datetime
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(__file__))
from lib.peer_relevance import tag_relevance  # noqa: E402

# Each bucket kept to 8-10 terms total across its two ANDed clauses,
# matching the proven-safe scale documented above. No wildcard (*)
# syntax used -- fetch_gdelt_general.py's own proven queries don't rely
# on it either, so this doesn't introduce an untested GDELT feature on
# top of an already-untested set of query strings.
QUERY_BUCKETS = {
    "apparel_sourcing_shifts": (
        'sourcelang:english '
        '("apparel sourcing" OR "garment manufacturing" OR "textile factory" OR "clothing manufacturer") '
        '(relocating OR relocation OR shifting OR "moving production" OR nearshoring OR reshoring)'
    ),
    "data_centre_investment": (
        'sourcelang:english '
        '("data center" OR "data centre" OR hyperscale) '
        '(investment OR "site selection" OR construction OR announces OR expansion)'
    ),
    "critical_minerals_peers": (
        'sourcelang:english '
        '(Botswana OR Namibia OR Eswatini OR "South Africa") '
        '("critical minerals" OR "rare earth" OR lithium OR cobalt OR smelter OR refinery)'
    ),
    "trade_preference_policy": (
        'sourcelang:english '
        '(AGOA OR AfCFTA OR "trade preference" OR "market access") '
        '(renewal OR expire OR expiry OR suspended OR terminated OR extended)'
    ),
}

API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

BASE_PARAMS = {
    "mode": "artlist",
    "format": "json",
    "maxrecords": 250,
    "timespan": "1week",
    "sort": "datedesc",
}

INTER_BUCKET_DELAY_SECONDS = 5

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "news", "global_signal_log.csv")
FIELDNAMES = [
    "pulled_at", "seendate", "title", "url", "domain", "sourcecountry", "language",
    "query_bucket", "mentioned_countries", "relevance_note",
]

MAX_ATTEMPTS = 4
TIMEOUT_SECONDS = 60
RETRY_WAIT_SECONDS = 30
RATE_LIMIT_WAIT_SECONDS = 90
MAXRECORDS_CAP = BASE_PARAMS["maxrecords"]


def fetch_bucket(bucket_name, query, max_attempts=None):
    """Identical retry/backoff logic to fetch_gdelt_general.py's own
    fetch_bucket() -- copied deliberately, not reimplemented, since this
    exact sequence (429 handling, empty-body check, response-snippet
    logging on failure) is already proven against real GDELT failure
    modes seen in production. max_attempts is overridable so the second
    pass in fetch_all_signals() (see below) can use a smaller retry
    budget than the first."""
    max_attempts = max_attempts or MAX_ATTEMPTS
    last_error = None
    already_throttled_this_run = False
    params = dict(BASE_PARAMS, query=query)

    for attempt in range(1, max_attempts + 1):
        response = None
        try:
            print(f"  [{bucket_name}] Attempt {attempt} of {max_attempts}: calling GDELT...")
            response = requests.get(API_URL, params=params, timeout=TIMEOUT_SECONDS)

            if response.status_code == 429:
                wait = int(response.headers.get("Retry-After", RATE_LIMIT_WAIT_SECONDS))
                last_error = "429 Too Many Requests"
                already_throttled_this_run = True
                print(f"  [{bucket_name}] Attempt {attempt} was rate-limited (429).")
                if attempt < max_attempts:
                    print(f"  [{bucket_name}] Waiting {wait}s before retrying (rate-limit backoff)...")
                    time.sleep(wait)
                continue

            response.raise_for_status()
            body = response.text
            if not body.strip():
                raise ValueError("GDELT returned an empty response body")
            payload = json.loads(body)
            articles = payload.get("articles", [])
            if len(articles) == MAXRECORDS_CAP:
                print(f"  [{bucket_name}] NOTE: hit the {MAXRECORDS_CAP}-record cap -- there may be more matches than fit in one response this week.")
            return articles

        except (requests.exceptions.RequestException, ValueError) as err:
            last_error = err
            print(f"  [{bucket_name}] Attempt {attempt} failed: {err}")
            if response is not None:
                print(f"    Response status: {response.status_code}")
                print(f"    Response snippet (first 300 chars): {response.text[:300]!r}")
            wait = RATE_LIMIT_WAIT_SECONDS if already_throttled_this_run else RETRY_WAIT_SECONDS
            if attempt < max_attempts:
                print(f"  [{bucket_name}] Waiting {wait}s before retrying...")
                time.sleep(wait)

    print(f"  [{bucket_name}] All {max_attempts} attempts failed. Last error: {last_error}")
    return None


# How many attempts the second pass gets, per bucket that failed its
# first full cycle. REAL EVIDENCE FROM A LIVE RUN, not a guess: the
# first bucket queried failed all 4 attempts (rate-limited every time),
# while later buckets increasingly succeeded on later attempts -- the
# second bucket succeeded on attempt 4, the last bucket succeeded on
# just attempt 2. That pattern is consistent with a rate-limit window
# that gradually clears as more wall-clock time passes during the run,
# which means whichever bucket happens to go first is structurally
# disadvantaged, regardless of its own wording. A second pass, run only
# after every bucket has had its first turn (so the maximum possible
# time has elapsed since the run started), gives an early, unlucky
# bucket a real chance to succeed once that window has had more time to
# clear -- rather than being permanently given up on just because it
# happened to be tried first.
SECOND_PASS_MAX_ATTEMPTS = 2


def fetch_all_signals():
    merged = {}
    failed_buckets = []
    bucket_names = list(QUERY_BUCKETS.keys())

    for i, (bucket_name, query) in enumerate(QUERY_BUCKETS.items()):
        articles = fetch_bucket(bucket_name, query)
        if articles is None:
            failed_buckets.append((bucket_name, query))
            continue
        print(f"  [{bucket_name}] Received {len(articles)} articles.")
        for article in articles:
            url = article.get("url", "")
            if not url:
                continue
            if url not in merged:
                article = dict(article)
                article["_query_bucket"] = bucket_name
                merged[url] = article
        if i < len(bucket_names) - 1:
            time.sleep(INTER_BUCKET_DELAY_SECONDS)

    still_failed = []
    if failed_buckets:
        print(f"\n{len(failed_buckets)} bucket(s) failed their first pass -- retrying now that more time has elapsed since the run started...")
        for bucket_name, query in failed_buckets:
            time.sleep(INTER_BUCKET_DELAY_SECONDS)
            articles = fetch_bucket(bucket_name, query, max_attempts=SECOND_PASS_MAX_ATTEMPTS)
            if articles is None:
                still_failed.append(bucket_name)
                continue
            print(f"  [{bucket_name}] Succeeded on the second pass, received {len(articles)} articles.")
            for article in articles:
                url = article.get("url", "")
                if not url:
                    continue
                if url not in merged:
                    article = dict(article)
                    article["_query_bucket"] = bucket_name
                    merged[url] = article

    any_failed = len(still_failed) > 0
    if still_failed:
        print(f"  Still failed after the second pass: {', '.join(still_failed)}")
    return list(merged.values()), any_failed


def load_existing_urls(path):
    if not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {row["url"] for row in csv.DictReader(f)}


def append_new_rows(path, articles, existing_urls, pulled_at):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    is_new_file = not os.path.exists(path)
    new_count = 0
    relevant_count = 0
    bucket_counts = {}

    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if is_new_file:
            writer.writeheader()
        for article in articles:
            url = article.get("url", "")
            if not url or url in existing_urls:
                continue

            title = article.get("title", "")
            bucket = article.get("_query_bucket", "")
            countries, note = tag_relevance(title, bucket, sourcecountry=article.get("sourcecountry", ""))
            if countries:
                relevant_count += 1
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

            writer.writerow({
                "pulled_at": pulled_at,
                "seendate": article.get("seendate", ""),
                "title": title,
                "url": url,
                "domain": article.get("domain", ""),
                "sourcecountry": article.get("sourcecountry", ""),
                "language": article.get("language", ""),
                "query_bucket": bucket,
                "mentioned_countries": ";".join(countries),
                "relevance_note": note,
            })
            existing_urls.add(url)
            new_count += 1

    return new_count, relevant_count, bucket_counts


def main():
    pulled_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    articles, any_bucket_failed = fetch_all_signals()

    if not articles and any_bucket_failed:
        print("No data fetched from any bucket this run. Will try again on the next scheduled run.")
        return
    if any_bucket_failed:
        print("NOTE: at least one query bucket failed this run -- proceeding with the buckets that did succeed.")

    existing = load_existing_urls(LOG_PATH)
    new_count, relevant_count, bucket_counts = append_new_rows(LOG_PATH, articles, existing, pulled_at)

    print(f"Merged {len(articles)} unique articles across all buckets, added {new_count} new rows to the log.")
    if new_count:
        print(f"  Flagged as mentioning a directly comparable country: {relevant_count} of {new_count}.")
        for bucket, count in sorted(bucket_counts.items(), key=lambda kv: -kv[1]):
            print(f"  {bucket}: {count}")


if __name__ == "__main__":
    main()
