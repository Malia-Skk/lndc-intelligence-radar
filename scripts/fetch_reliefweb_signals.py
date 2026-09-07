"""
Phase 5 — ReliefWeb humanitarian/climate/food-security signal for Lesotho.

UNVERIFIED AGAINST LIVE DATA -- api.reliefweb.int is not reachable from the
environment this was built in (network access restricted to package
registries and github.com). The request shape and response parsing below
were written from documented/general knowledge of the ReliefWeb API
(https://reliefweb.int/help/api), not confirmed against a real response.
This is a genuinely different situation from every previous phase's first
build: GDELT, Comtrade, and the World Bank API are all sources this
project had already made at least one successful live call against before
this was written. This one hasn't. Trigger via workflow_dispatch and read
the run log's diagnostic output BEFORE trusting this on a schedule -- see
PHASE5_SUMMARY.md for exactly what to check.

WHY RELIEFWEB AND NOT FEWS NET DIRECTLY: FEWS NET's own site doesn't
appear to expose a comparably documented, free, structured API. ReliefWeb
aggregates situation reports, appeals, and disaster updates from many
humanitarian sources (including FEWS NET-adjacent content) behind one
well-documented API, so it's used here as the practical mechanism for the
same underlying need (drought/food-security signal relevant to
grains/poultry and agro-processing).

Follows the same idempotent-append pattern as every other fetch script:
dedup by URL, safe to re-run.
"""
import csv
import datetime
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(__file__))
from lib.csv_log import append_new_rows, utc_now_iso  # noqa: E402

API_URL = "https://api.reliefweb.int/v1/reports"

PARAMS = {
    "appname": "lndc-intelligence-radar",
    "query[value]": "Lesotho",
    "query[operator]": "AND",
    "sort[]": "date:desc",
    "limit": 50,
    "fields[include][]": [
        "title", "date.created", "url", "url_alias",
        "source.name", "source.shortname",
        "country.name", "disaster_type.name",
    ],
}

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "climate", "reliefweb_signal_log.csv")
FIELDNAMES = [
    "pulled_at", "reliefweb_id", "title", "url", "date_created",
    "source", "countries", "disaster_types",
]
KEY_FIELDS = ["url"]

MAX_ATTEMPTS = 4
TIMEOUT_SECONDS = 60
RETRY_WAIT_SECONDS = 30
RATE_LIMIT_WAIT_SECONDS = 90


def join_names(field_value):
    """ReliefWeb returns list-of-object fields like source/country/
    disaster_type as [{"name": "..."}, ...] -- flatten to a semicolon
    string. Defensive about the exact key names since this is unverified;
    falls back to str(item) if "name"/"shortname" aren't present, so a
    schema surprise degrades to something readable rather than crashing."""
    if not field_value:
        return ""
    if not isinstance(field_value, list):
        field_value = [field_value]
    names = []
    for item in field_value:
        if isinstance(item, dict):
            names.append(item.get("name") or item.get("shortname") or str(item))
        else:
            names.append(str(item))
    return ";".join(names)


def fetch_reports():
    last_error = None
    already_throttled_this_run = False

    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = None
        try:
            print(f"Attempt {attempt} of {MAX_ATTEMPTS}: calling ReliefWeb...")
            response = requests.get(API_URL, params=PARAMS, timeout=TIMEOUT_SECONDS)

            if response.status_code == 429:
                wait = int(response.headers.get("Retry-After", RATE_LIMIT_WAIT_SECONDS))
                last_error = "429 Too Many Requests"
                already_throttled_this_run = True
                print(f"Attempt {attempt} was rate-limited (429).")
                if attempt < MAX_ATTEMPTS:
                    print(f"Waiting {wait}s before retrying...")
                    time.sleep(wait)
                continue

            response.raise_for_status()
            payload = response.json()

            if "data" not in payload:
                print("  NOTE: response JSON has no 'data' key -- printing the top-level keys and a snippet")
                print(f"  Top-level keys: {list(payload.keys())}")
                print(f"  Raw payload (first 500 chars): {str(payload)[:500]!r}")
                return []

            return payload["data"]

        except (requests.exceptions.RequestException, ValueError) as err:
            last_error = err
            print(f"Attempt {attempt} failed: {err}")
            if response is not None:
                print(f"  Response status: {response.status_code}")
                print(f"  Response snippet (first 500 chars): {response.text[:500]!r}")
            wait = RATE_LIMIT_WAIT_SECONDS if already_throttled_this_run else RETRY_WAIT_SECONDS
            if attempt < MAX_ATTEMPTS:
                print(f"Waiting {wait}s before retrying...")
                time.sleep(wait)

    print(f"All {MAX_ATTEMPTS} attempts failed. Skipping this run cleanly. Last error: {last_error}")
    return None


def to_rows(reports, pulled_at):
    rows = []
    skipped_missing_fields = 0
    for report in reports:
        fields = report.get("fields", {})
        if not fields:
            skipped_missing_fields += 1
            continue

        url = fields.get("url") or fields.get("url_alias") or ""
        if not url:
            skipped_missing_fields += 1
            continue

        date_created = ""
        date_field = fields.get("date", {})
        if isinstance(date_field, dict):
            date_created = date_field.get("created", "")

        rows.append({
            "pulled_at": pulled_at,
            "reliefweb_id": report.get("id", ""),
            "title": fields.get("title", ""),
            "url": url,
            "date_created": date_created,
            "source": join_names(fields.get("source")),
            "countries": join_names(fields.get("country")),
            "disaster_types": join_names(fields.get("disaster_type")),
        })

    if skipped_missing_fields:
        print(f"  NOTE: skipped {skipped_missing_fields} report(s) with missing fields/url -- "
              "worth checking if this is more than a handful, may indicate a field-name mismatch.")
    return rows


def main():
    pulled_at = utc_now_iso()
    reports = fetch_reports()
    if reports is None:
        print("No data fetched this run. Will try again on the next scheduled run.")
        return

    print(f"Received {len(reports)} raw report(s) from ReliefWeb.")
    if reports:
        print(f"  Sample raw report structure (first item, for verifying the field-name assumptions above): "
              f"{str(reports[0])[:800]!r}")

    existing = set()
    if os.path.exists(LOG_PATH):
        with open(LOG_PATH, newline="", encoding="utf-8") as f:
            existing = {row["url"] for row in csv.DictReader(f)}

    rows = to_rows(reports, pulled_at)
    added = append_new_rows(LOG_PATH, FIELDNAMES, rows, KEY_FIELDS)
    print(f"Added {added} new rows to the log.")


if __name__ == "__main__":
    main()
