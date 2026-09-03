"""
Phase 1 — widened World Bank macro pull.

Replaces the automotive pilot's narrow scope (Lesotho vs. South Africa only,
5 indicators) with the full SACU comparator set and an indicator basket tied
explicitly to the Letsema Strategy's diagnosis (Ch.1 — balance-of-payments
constraint) and Winning Aspiration (Ch.2 — five outcomes).

Reads its country list and indicator list from config/sources.yaml (source
id "worldbank_macro"), so widening scope further is a config edit, not a
code change.

Does NOT touch data/worldbank_indicator_log.csv or
scripts/fetch_worldbank_indicators.py — that pipeline keeps running
untouched. Writes to data/macro/worldbank_indicator_log.csv.
"""
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(__file__))
from lib.csv_log import append_new_rows, utc_now_iso  # noqa: E402
from lib.registry import get_source  # noqa: E402

SOURCE = get_source("worldbank_macro")

COUNTRIES = ";".join(c["iso3"] for c in SOURCE["countries"])
INDICATORS = [(ind["code"], ind["name"], ind.get("outcome_link", "")) for ind in SOURCE["indicators"]]

BASE_URL = "https://api.worldbank.org/v2/country/{countries}/indicator/{code}"
DATE_RANGE = SOURCE.get("date_range", "2010:2026")

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", SOURCE["output"])
FIELDNAMES = [
    "pulled_at", "country", "country_code", "indicator_code",
    "indicator_name", "outcome_link", "year", "value",
]
KEY_FIELDS = ["country_code", "indicator_code", "year", "value"]

MAX_ATTEMPTS = 4
TIMEOUT_SECONDS = 60
RETRY_WAIT_SECONDS = 20


def fetch_indicator(code):
    url = BASE_URL.format(countries=COUNTRIES, code=code)
    params = {"format": "json", "date": DATE_RANGE, "per_page": 1000}
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            print(f"  Attempt {attempt} of {MAX_ATTEMPTS} for {code}...")
            response = requests.get(url, params=params, timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json()
            # World Bank returns [metadata, data] -- data can be None if the
            # indicator/country code is wrong, so check explicitly.
            if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
                print(f"  No data returned for {code} (check the indicator/country codes).")
                return []
            return payload[1]
        except (requests.exceptions.RequestException, ValueError) as err:
            last_error = err
            print(f"  Attempt {attempt} failed: {err}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_WAIT_SECONDS)
    print(f"  All attempts failed for {code}. Last error: {last_error}")
    return None


def to_rows(records, indicator_code, indicator_name, outcome_link, pulled_at):
    rows = []
    for rec in records:
        value = rec.get("value")
        if value is None:
            continue  # World Bank leaves recent years blank until finalised
        rows.append({
            "pulled_at": pulled_at,
            "country": (rec.get("country") or {}).get("value", ""),
            "country_code": rec.get("countryiso3code", ""),
            "indicator_code": indicator_code,
            "indicator_name": indicator_name,
            "outcome_link": outcome_link,
            "year": rec.get("date", ""),
            "value": value,
        })
    return rows


def main():
    pulled_at = utc_now_iso()
    total_new = 0
    any_failure = False
    for code, name, outcome_link in INDICATORS:
        print(f"Fetching {code} ({name}) for {COUNTRIES}...")
        records = fetch_indicator(code)
        if records is None:
            any_failure = True
            continue
        rows = to_rows(records, code, name, outcome_link, pulled_at)
        added = append_new_rows(LOG_PATH, FIELDNAMES, rows, KEY_FIELDS)
        print(f"  Added {added} new rows.")
        total_new += added
    n_countries = len(SOURCE["countries"])
    print(f"Done. Added {total_new} new rows total across {len(INDICATORS)} indicators x {n_countries} countries.")
    if any_failure:
        print("Note: at least one indicator failed to fetch this run -- it will be retried next run.")


if __name__ == "__main__":
    main()
