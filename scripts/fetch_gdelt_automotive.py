import csv
import datetime
import os
import time
import requests

QUERY = ('(automotive OR "auto parts" OR "vehicle components" OR "automotive supplier") '
         'AND (Africa OR "South Africa" OR SADC OR nearshoring OR "supply chain")')

API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

PARAMS = {
    "query": QUERY,
    "mode": "artlist",
    "format": "json",
    "maxrecords": 250,
    "timespan": "1week",
    "sort": "datedesc",
}

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "automotive_signal_log.csv")
FIELDNAMES = ["pulled_at", "seendate", "title", "url", "domain", "sourcecountry", "language"]

MAX_ATTEMPTS = 4
TIMEOUT_SECONDS = 60
RETRY_WAIT_SECONDS = 20


def fetch_signals():
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            print(f"Attempt {attempt} of {MAX_ATTEMPTS}: calling GDELT...")
            response = requests.get(API_URL, params=PARAMS, timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
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


def append_new_rows(path, articles, existing_urls):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    is_new_file = not os.path.exists(path)
    pulled_at = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    new_count = 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if is_new_file:
            writer.writeheader()
        for article in articles:
            url = article.get("url", "")
            if not url or url in existing_urls:
                continue
            writer.writerow({
                "pulled_at": pulled_at,
                "seendate": article.get("seendate", ""),
                "title": article.get("title", ""),
                "url": url,
                "domain": article.get("domain", ""),
                "sourcecountry": article.get("sourcecountry", ""),
                "language": article.get("language", ""),
            })
            existing_urls.add(url)
            new_count += 1
    return new_count


def main():
    articles = fetch_signals()
    if articles is None:
        # GDELT was unreachable after retries. Exit successfully (not a failure) so the
        # workflow doesn't show a false "broken pipeline" alarm for a transient outage --
        # tomorrow's scheduled run will simply try again.
        print("No data fetched this run. Will try again on the next scheduled run.")
        return
    existing = load_existing_urls(LOG_PATH)
    new_count = append_new_rows(LOG_PATH, articles, existing)
    print(f"Fetched {len(articles)} articles from GDELT, added {new_count} new rows to the log.")


if __name__ == "__main__":
    main()
