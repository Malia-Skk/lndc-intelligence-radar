"""
Phase 1 — SACU export/import basket pull (HS 2-digit chapters, "AG2").

Widens the automotive pilot's single-HS-code pull into a full trade-basket
pull for every SACU member, at the 2-digit chapter level (cmdCode="AG2"
returns all ~97 chapters in one call, rather than one call per product).
This is the country-side input Phase 2's proxy Economic Complexity /
Fitness-Complexity computation needs — it requires each country's *whole*
export basket, not one product.

Reuses the exact auth/retry/response-shape handling already proven in
scripts/fetch_comtrade_automotive.py (same endpoint, same header, same
"keep only the grand total across customs procedure / mode of transport /
second partner" filter) — only the scope parameters change.

One deliberate fix vs. the original script: the dedup key here includes
cmd_code. The automotive pilot's key omits it, which is safe only because
that script ever fetches a single product; every reporter/flow/year
combination here legitimately has ~97 different cmd_code rows, so cmd_code
must be part of the key or genuinely distinct rows could be mishandled.

Does NOT touch data/comtrade_automotive_log.csv or
scripts/fetch_comtrade_automotive.py — that pipeline keeps running
untouched. Writes to data/trade/comtrade_sacu_basket_log.csv.
"""
import datetime
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(__file__))
from lib.csv_log import append_new_rows, utc_now_iso  # noqa: E402
from lib.registry import get_source  # noqa: E402

SOURCE = get_source("comtrade_sacu_basket")

REPORTER_CODES = ",".join(c["comtrade_code"] for c in SOURCE["countries"])
PARTNER_CODE = "0"  # World -- aggregated across all trading partners
CMD_CODE = SOURCE.get("cmd_level", "AG2")
FLOWS = SOURCE.get("flows", ["M", "X"])

CURRENT_YEAR = datetime.datetime.utcnow().year
YEARS_LOOKBACK = SOURCE.get("years_lookback", 8)
PERIOD = ",".join(str(y) for y in range(CURRENT_YEAR - YEARS_LOOKBACK, CURRENT_YEAR))

BASE_URL = "https://comtradeapi.un.org/data/v1/get/C/A/HS"
API_KEY = os.environ.get("COMTRADE_API_KEY")

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", SOURCE["output"])
FIELDNAMES = [
    "pulled_at", "reporter", "reporter_code", "flow", "partner",
    "cmd_code", "cmd_desc", "year", "trade_value_usd",
]
KEY_FIELDS = ["reporter_code", "flow", "cmd_code", "year", "trade_value_usd"]

MAX_ATTEMPTS = 4
TIMEOUT_SECONDS = 90
RETRY_WAIT_SECONDS = 30
RATE_LIMIT_WAIT_SECONDS = 90
INTER_CALL_DELAY_SECONDS = 5  # be a good citizen of the free-tier quota across multiple flow calls


def fetch_flow(flow_code):
    if not API_KEY:
        raise SystemExit(
            "COMTRADE_API_KEY is not set. Add it as a GitHub repository secret "
            "(Settings -> Secrets and variables -> Actions) before running this."
        )
    params = {
        "reporterCode": REPORTER_CODES,
        "partnerCode": PARTNER_CODE,
        "cmdCode": CMD_CODE,
        "flowCode": flow_code,
        "period": PERIOD,
        "includeDesc": "true",
    }
    headers = {"Ocp-Apim-Subscription-Key": API_KEY}

    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            print(f"  Attempt {attempt} of {MAX_ATTEMPTS}: flow={flow_code}...")
            response = requests.get(BASE_URL, params=params, headers=headers, timeout=TIMEOUT_SECONDS)

            if response.status_code == 429:
                wait = int(response.headers.get("Retry-After", RATE_LIMIT_WAIT_SECONDS))
                print(f"  Rate-limited (429). Waiting {wait}s...")
                last_error = "429 Too Many Requests"
                if attempt < MAX_ATTEMPTS:
                    time.sleep(wait)
                continue

            if response.status_code == 401:
                raise SystemExit(
                    "401 Unauthorized -- the COMTRADE_API_KEY secret is missing, wrong, "
                    "or the subscription hasn't finished activating yet."
                )

            response.raise_for_status()
            payload = response.json()

            if isinstance(payload, dict) and "data" in payload:
                records = payload["data"]
            elif isinstance(payload, list):
                records = payload
            else:
                print("  Unexpected response shape. Raw payload (first 1000 chars):")
                print(str(payload)[:1000])
                return []

            print(f"  Received {len(records)} raw records for flow={flow_code}.")
            return records

        except requests.exceptions.RequestException as err:
            last_error = err
            print(f"  Attempt {attempt} failed: {err}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_WAIT_SECONDS)

    print(f"  All {MAX_ATTEMPTS} attempts failed for flow={flow_code}. Last error: {last_error}")
    return None


def filter_to_totals(records):
    """Keep only the grand total per reporter/flow/product/year -- drop rows
    where customs procedure, mode of transport, or second partner are broken
    out separately, which Comtrade mixes into the same response."""
    kept = []
    for rec in records:
        customs_is_total = str(rec.get("customsCode", "")) == "C00"
        mot_is_total = str(rec.get("motCode", "")) in ("0", "0.0")
        partner2_is_total = str(rec.get("partner2Code", "")) in ("0", "0.0")
        if customs_is_total and mot_is_total and partner2_is_total:
            kept.append(rec)
    return kept


def first_present(record, *keys):
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return ""


def to_rows(records, pulled_at):
    rows = []
    for rec in records:
        rows.append({
            "pulled_at": pulled_at,
            "reporter": first_present(rec, "reporterDesc", "reporterISO"),
            "reporter_code": str(first_present(rec, "reporterCode")),
            "flow": first_present(rec, "flowDesc", "flowCode"),
            "partner": first_present(rec, "partnerDesc", "partnerISO"),
            "cmd_code": first_present(rec, "cmdCode"),
            "cmd_desc": first_present(rec, "cmdDesc"),
            "year": str(first_present(rec, "refYear", "period")),
            "trade_value_usd": first_present(rec, "primaryValue", "fobvalue", "cifvalue"),
        })
    return rows


def main():
    pulled_at = utc_now_iso()
    total_new = 0
    any_failure = False
    for i, flow in enumerate(FLOWS):
        records = fetch_flow(flow)
        if records is None:
            any_failure = True
            continue
        total_records = filter_to_totals(records)
        print(f"  Kept {len(total_records)} grand-total rows out of {len(records)} raw rows for flow={flow}.")
        rows = to_rows(total_records, pulled_at)
        added = append_new_rows(LOG_PATH, FIELDNAMES, rows, KEY_FIELDS)
        print(f"  Added {added} new rows for flow={flow}.")
        total_new += added
        if i < len(FLOWS) - 1:
            time.sleep(INTER_CALL_DELAY_SECONDS)
    n_reporters = len(SOURCE["countries"])
    print(f"Done. Added {total_new} new rows total across {len(FLOWS)} flow(s) x {n_reporters} reporters.")
    if any_failure:
        print("Note: at least one flow failed to fetch this run -- it will be retried next run.")


if __name__ == "__main__":
    main()
