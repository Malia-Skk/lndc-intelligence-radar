"""
Phase 3 — general Lesotho/LNDC economic-signal pull, with auto-tagging.

Widens the automotive pilot's single-sector query into a general Lesotho
economic-development query, then classifies each article using the
rule-based tagger in lib/tagging.py so the widened net stays sortable
rather than becoming an undifferentiated firehose.

Does NOT touch data/automotive_signal_log.csv or
scripts/fetch_gdelt_automotive.py -- that pipeline keeps running
untouched, same additive pattern as every other phase so far. Writes to
data/news/general_signal_log.csv.

QUERY STRUCTURE -- this took one real, evidence-based revision to get
right. The first version was a single query with ~30 OR'd terms in one
clause. A live run returned "Your query was too short or too long" (HTTP
200, not an error status -- see fetch_gdelt_general's backoff comments for
why that's dangerous to mistake for a generic failure), confirming GDELT's
DOC API has a real complexity/length limit that one big query exceeded.
The fix: split into several smaller queries -- each sized like the
automotive pilot's proven, already-working query (roughly 6-9 OR'd terms
plus the Lesotho/sourcelang qualifiers) -- run sequentially, merged and
deduped by URL before tagging. This is deliberately modelled on a scale
already known to work, not a fresh guess at a different, equally
unverified size.

Each query still requires "Lesotho" AND at least one broad term, for the
same reason as before: "Lesotho" alone floods the feed with unrelated
sport/weather/politics stories, and the broad terms alone (without
requiring Lesotho) would pull in irrelevant stories about other countries.
"""
import csv
import datetime
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(__file__))
from lib.tagging import (  # noqa: E402
    format_matched_keywords_for_csv,
    format_tags_for_csv,
    tag_article,
)

# Split into buckets roughly matching the tagging categories, each kept to
# a scale already proven to work (the automotive pilot's query has ~12
# terms total across its two clauses). Splitting this way is also a nice
# bonus, not just a workaround: each bucket already tells us roughly what
# theme a hit belongs to before the title-based tagger even runs.
QUERY_BUCKETS = {
    "trade_investment": (
        'sourcelang:english Lesotho '
        '(economy OR economic OR investment OR "foreign direct investment" OR '
        'export OR trade OR jobs OR employment)'
    ),
    "industry_sectors": (
        'sourcelang:english Lesotho '
        '(manufacturing OR factory OR mining OR diamond OR textile OR garment OR '
        'agriculture OR tourism)'
    ),
    "policy_finance": (
        'sourcelang:english Lesotho '
        '(SACU OR AGOA OR "central bank" OR budget OR currency OR loti)'
    ),
    "infrastructure_risk": (
        'sourcelang:english Lesotho '
        '("Highlands Water" OR electricity OR water OR drought OR remittance OR '
        'infrastructure)'
    ),
    "emerging_niche": (
        'sourcelang:english Lesotho '
        '(wool OR mohair OR cashmere OR "data centre" OR "data center" OR retail OR '
        'transport OR logistics OR parliament)'
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

# Pause between successive bucket queries within the same run -- not
# because any single call is expected to be rate-limited on its own, but
# because firing 5 calls back-to-back is more likely to trip a burst
# threshold than 5 calls spread out a few seconds apart.
INTER_BUCKET_DELAY_SECONDS = 5

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "news", "general_signal_log.csv")
FIELDNAMES = [
    "pulled_at", "seendate", "title", "url", "domain", "sourcecountry", "language",
    "query_bucket", "tags", "matched_keywords", "mentions_lndc",
]

MAX_ATTEMPTS = 4
TIMEOUT_SECONDS = 60
RETRY_WAIT_SECONDS = 30
RATE_LIMIT_WAIT_SECONDS = 90

MAXRECORDS_CAP = BASE_PARAMS["maxrecords"]


def fetch_bucket(bucket_name, query):
    """Same retry/backoff logic as before (proven against the real failure
    sequence seen in production), now scoped to fetch ONE query bucket."""
    last_error = None
    already_throttled_this_run = False
    params = dict(BASE_PARAMS, query=query)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = None
        try:
            print(f"  [{bucket_name}] Attempt {attempt} of {MAX_ATTEMPTS}: calling GDELT...")
            response = requests.get(API_URL, params=params, timeout=TIMEOUT_SECONDS)

            if response.status_code == 429:
                wait = int(response.headers.get("Retry-After", RATE_LIMIT_WAIT_SECONDS))
                last_error = "429 Too Many Requests"
                already_throttled_this_run = True
                print(f"  [{bucket_name}] Attempt {attempt} was rate-limited (429).")
                if attempt < MAX_ATTEMPTS:
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
                print(
                    f"  [{bucket_name}] NOTE: hit the {MAXRECORDS_CAP}-record cap -- "
                    "there may be more matches than fit in one response this week."
                )
            return articles

        except (requests.exceptions.RequestException, ValueError) as err:
            last_error = err
            print(f"  [{bucket_name}] Attempt {attempt} failed: {err}")
            if response is not None:
                print(f"    Response status: {response.status_code}")
                print(f"    Response snippet (first 300 chars): {response.text[:300]!r}")

            wait = RATE_LIMIT_WAIT_SECONDS if already_throttled_this_run else RETRY_WAIT_SECONDS
            if attempt < MAX_ATTEMPTS:
                print(f"  [{bucket_name}] Waiting {wait}s before retrying...")
                time.sleep(wait)

    print(f"  [{bucket_name}] All {MAX_ATTEMPTS} attempts failed. Last error: {last_error}")
    return None


def fetch_all_signals():
    """Runs every query bucket, merges the results, and dedupes by URL
    across buckets (a single article can legitimately match more than one
    bucket's terms). Returns (merged_articles, any_bucket_failed)."""
    merged = {}
    any_failed = False
    bucket_names = list(QUERY_BUCKETS.keys())

    for i, (bucket_name, query) in enumerate(QUERY_BUCKETS.items()):
        articles = fetch_bucket(bucket_name, query)
        if articles is None:
            any_failed = True
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
    tag_counts = {}
    untagged_count = 0

    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if is_new_file:
            writer.writeheader()
        for article in articles:
            url = article.get("url", "")
            if not url or url in existing_urls:
                continue

            title = article.get("title", "")
            tags, matched_keywords, mentions_lndc = tag_article(title)
            if not tags:
                untagged_count += 1
            for tag in tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

            writer.writerow({
                "pulled_at": pulled_at,
                "seendate": article.get("seendate", ""),
                "title": title,
                "url": url,
                "domain": article.get("domain", ""),
                "sourcecountry": article.get("sourcecountry", ""),
                "language": article.get("language", ""),
                "query_bucket": article.get("_query_bucket", ""),
                "tags": format_tags_for_csv(tags),
                "matched_keywords": format_matched_keywords_for_csv(matched_keywords),
                "mentions_lndc": mentions_lndc,
            })
            existing_urls.add(url)
            new_count += 1

    return new_count, tag_counts, untagged_count


def main():
    pulled_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    articles, any_bucket_failed = fetch_all_signals()

    if not articles and any_bucket_failed:
        print("No data fetched from any bucket this run. Will try again on the next scheduled run.")
        return
    if any_bucket_failed:
        print("NOTE: at least one query bucket failed this run -- proceeding with the buckets that did succeed.")

    existing = load_existing_urls(LOG_PATH)
    new_count, tag_counts, untagged_count = append_new_rows(LOG_PATH, articles, existing, pulled_at)

    print(f"Merged {len(articles)} unique articles across all buckets, added {new_count} new rows to the log.")
    if new_count:
        print(f"  Untagged (discovery feed): {untagged_count} of {new_count} new rows.")
        for tag, count in sorted(tag_counts.items(), key=lambda kv: -kv[1]):
            print(f"  {tag}: {count}")


if __name__ == "__main__":
    main()
