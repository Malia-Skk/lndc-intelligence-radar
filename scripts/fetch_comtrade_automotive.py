import csv
import datetime
import os
import time
import requests

# Lesotho (426) and South Africa (710) -- UN M49 numeric codes, not ISO3.
REPORTER_CODES = "426,710"
PARTNER_CODE = "0"  # World
CMD_CODE = "8708"   # Parts and accessories of motor vehicles

CURRENT_YEAR = datetime.datetime.utcnow().year
PERIOD = ",".join(str(y) for y in range(CURRENT_YEAR - 7, CURRENT_YEAR))

BASE_URL = "https://comtradeapi.un.org/data/v1/get/C/A/HS"

API_KEY = os.environ.get("COMTRADE_API_KEY")

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "comtrade_automotive_log.csv")
FIELDNAMES = [
    "pulled_at", "reporter", "reporter_code", "flow", "partner",
    "cmd_code", "cmd_desc", "year", "trade_value_usd",
]

MAX_ATTEMPTS = 4
TIMEOUT_SECONDS = 60
RETRY_WAIT_SECONDS = 30
RATE_LIMIT_WAIT_SECONDS = 90


def fetch_records():
    if not API_KEY:
        raise SystemExit(
            "COMTRADE_API_KEY is not set. Add it as a GitHub repository secret "
            "(Settings -> Secrets and variables -> Actions) before running this."
        )

    params = {
        "reporterCode": REPORTER_CODES,
        "partnerCode": PARTNER_CODE,
        "cmdCode": CMD_CODE,
        "period": PERIOD,
        "includeDesc": "true",
    }
    headers = {"Ocp-Apim-Subscription-Key": API_KEY}

    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            print(f"Attempt {attempt} of {MAX_ATTEMPTS}: calling UN Comtrade...")
            response = requests.get(BASE_URL, params=params, headers=headers, timeout=TIMEOUT_SECONDS)

            if response.status_code == 429:
                wait = int(response.headers.get("Retry-After", RATE_LIMIT_WAIT_SECONDS))
                print(f"Attempt {attempt} was rate-limited (429).")
                last_error = "429 Too Many Requests"
                if attempt < MAX_ATTEMPTS:
                    print(f"Waiting {wait}s before retrying...")
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
                print("Unexpected response shape. Raw payload (first 1000 chars):")
                print(str(payload)[:1000])
                return []

            print(f"Received {len(records)} records.")
            if records:
                print("Sample record (first one, for verification):")
                print(records[0])
            return records

        except requests.exceptions.RequestException as err:
            last_error = err
            print(f"Attempt {attempt} failed: {err}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_WAIT_SECONDS)

    print(f"All {MAX_ATTEMPTS} attempts failed. Skipping this run cleanly. Last error: {last_error}")
    return None


def filter_to_totals(records):
    """Comtrade returns both clean grand-totals and finer breakdowns (by customs
    procedure, mode of transport, second partner) mixed together in one response.
    We only want the grand total per reporter/flow/year, so keep records where
    those breakdown dimensions are all set to their "TOTAL" sentinel values."""
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


def load_existing_keys(path):
    if not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {
            (r["reporter_code"], r["flow"], r["year"], r["trade_value_usd"])
            for r in csv.DictReader(f)
        }


def append_new_rows(path, records, existing_keys):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    is_new_file = not os.path.exists(path)
    pulled_at = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    new_count = 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if is_new_file:
            writer.writeheader()
        for rec in records:
            reporter = first_present(rec, "reporterDesc", "reporterISO")
            reporter_code = str(first_present(rec, "reporterCode"))
            flow = first_present(rec, "flowDesc", "flowCode")
            partner = first_present(rec, "partnerDesc", "partnerISO")
            cmd_code = first_present(rec, "cmdCode")
            cmd_desc = first_present(rec, "cmdDesc")
            year = str(first_present(rec, "refYear", "period"))
            value = first_present(rec, "primaryValue", "fobvalue", "cifvalue")

            key = (reporter_code, flow, year, str(value))
            if key in existing_keys:
                continue

            writer.writerow({
                "pulled_at": pulled_at,
                "reporter": reporter,
                "reporter_code": reporter_code,
                "flow": flow,
                "partner": partner,
                "cmd_code": cmd_code,
                "cmd_desc": cmd_desc,
                "year": year,
                "trade_value_usd": value,
            })
            existing_keys.add(key)
            new_count += 1
    return new_count


def main():
    records = fetch_records()
    if records is None:
        print("No data fetched this run. Will try again on the next scheduled run.")
        return
    total_records = filter_to_totals(records)
    print(f"Kept {len(total_records)} grand-total records out of {len(records)} raw records.")
    existing = load_existing_keys(LOG_PATH)
    new_count = append_new_rows(LOG_PATH, total_records, existing)
    print(f"Added {new_count} new rows to the log.")


if __name__ == "__main__":
    main()
