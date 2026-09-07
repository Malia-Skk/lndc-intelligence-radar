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

Reuses the exact retry/backoff handling already proven in
fetch_gdelt_automotive.py (same endpoint, same params shape, same 429
handling) -- only the query and the post-processing (tagging) change.

A note on the query: it requires "Lesotho" AND at least one broad
economic/development term, rather than either alone. "Lesotho" by itself
would flood the feed with unrelated news (sport, weather, general politics
with no economic content); the broad term list alone (without requiring
Lesotho) would pull in irrelevant stories about other countries entirely.
Requiring both is a deliberate precision/recall tradeoff, not an oversight
-- see PHASE3_SUMMARY.md for what to do if real coverage gaps show up.
"""
import csv
import datetime
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

QUERY = (
    'sourcelang:english Lesotho '
    '(economy OR economic OR industry OR industrial OR investment OR investor OR '
    'export OR manufacturing OR factory OR jobs OR employment OR trade OR '
    '"foreign direct investment" OR mining OR diamond OR textile OR garment OR '
    'apparel OR agriculture OR farming OR tourism OR SACU OR AGOA OR '
    '"Highlands Water" OR remittance OR drought OR "central bank" OR parliament OR '
    'budget OR loti OR currency OR infrastructure OR electricity OR water OR '
    '"data centre" OR "data center" OR wool OR mohair OR cashmere OR retail OR '
    'transport OR logistics)'
)

API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

PARAMS = {
    "query": QUERY,
    "mode": "artlist",
    "format": "json",
    "maxrecords": 250,
    "timespan": "1week",
    "sort": "datedesc",
}

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "news", "general_signal_log.csv")
FIELDNAMES = [
    "pulled_at", "seendate", "title", "url", "domain", "sourcecountry", "language",
    "tags", "matched_keywords", "mentions_lndc",
]

MAX_ATTEMPTS = 4
TIMEOUT_SECONDS = 60
RETRY_WAIT_SECONDS = 30
RATE_LIMIT_WAIT_SECONDS = 90

# If a single pull comes back with exactly this many records, the query
# likely has MORE matches than fit in one response -- Comtrade-style
# pagination doesn't exist here, so this is just a signal to look at
# narrowing the timespan or splitting the query, not something the script
# can fix on its own.
MAXRECORDS_CAP = PARAMS["maxrecords"]


def fetch_signals():
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            print(f"Attempt {attempt} of {MAX_ATTEMPTS}: calling GDELT...")
            response = requests.get(API_URL, params=PARAMS, timeout=TIMEOUT_SECONDS)

            if response.status_code == 429:
                wait = int(response.headers.get("Retry-After", RATE_LIMIT_WAIT_SECONDS))
                last_error = "429 Too Many Requests"
                print(f"Attempt {attempt} was rate-limited (429).")
                if attempt < MAX_ATTEMPTS:
                    print(f"Waiting {wait}s before retrying (rate-limit backoff)...")
                    time.sleep(wait)
                continue

            response.raise_for_status()
            if not response.text.strip():
                raise ValueError("GDELT returned an empty response body")
            payload = response.json()
            return payload.get("articles", [])

        except (requests.exceptions.RequestException, ValueError) as err:
            last_error = err
            print(f"Attempt {attempt} failed: {err}")
            if attempt < MAX_ATTEMPTS:
                print(f"Waiting {RETRY_WAIT_SECONDS}s before retrying...")
                time.sleep(RETRY_WAIT_SECONDS)

    print(f"All {MAX_ATTEMPTS} attempts failed. Skipping this run cleanly. Last error: {last_error}")
    return None


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
                "tags": format_tags_for_csv(tags),
                "matched_keywords": format_matched_keywords_for_csv(matched_keywords),
                "mentions_lndc": mentions_lndc,
            })
            existing_urls.add(url)
            new_count += 1

    return new_count, tag_counts, untagged_count


def main():
    pulled_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    articles = fetch_signals()
    if articles is None:
        print("No data fetched this run. Will try again on the next scheduled run.")
        return

    if len(articles) == MAXRECORDS_CAP:
        print(
            f"NOTE: this pull returned exactly the {MAXRECORDS_CAP}-record cap -- "
            "there may be more matching articles than fit in one response this week. "
            "Not an error, but worth knowing if this keeps happening (see PHASE3_SUMMARY.md)."
        )

    existing = load_existing_urls(LOG_PATH)
    new_count, tag_counts, untagged_count = append_new_rows(LOG_PATH, articles, existing, pulled_at)

    print(f"Fetched {len(articles)} articles from GDELT, added {new_count} new rows to the log.")
    if new_count:
        print(f"  Untagged (discovery feed): {untagged_count} of {new_count} new rows.")
        for tag, count in sorted(tag_counts.items(), key=lambda kv: -kv[1]):
            print(f"  {tag}: {count}")


if __name__ == "__main__":
    main()
