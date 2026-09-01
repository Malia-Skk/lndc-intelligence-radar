import csv
import datetime
import os
import time
import requests

# Lesotho, compared against South Africa (the dominant regional assembler
# our automotive-component pilot would be selling into).
COUNTRIES = "LSO;ZAF"

INDICATORS = {
    "NV.IND.MANF.ZS": "Manufacturing, value added (% of GDP)",
    "NV.IND.TOTL.ZS": "Industry (incl. construction), value added (% of GDP)",
    "TX.VAL.MANF.ZS.UN": "Manufactured exports (% of merchandise exports)",
    "BX.KLT.DINV.WD.GD.ZS": "FDI, net inflows (% of GDP)",
    "NE.EXP.GNFS.ZS": "Exports of goods and services (% of GDP)",
}

BASE_URL = "https://api.worldbank.org/v2/country/{countries}/indicator/{code}"
DATE_RANGE = "2010:2025"

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "worldbank_indicator_log.csv")
FIELDNAMES = ["pulled_at", "country", "country_code", "indicator_code", "indicator_name", "year", "value"]

MAX_ATTEMPTS = 4
TIMEOUT_SECONDS = 60
RETRY_WAIT_SECONDS = 20


def fetch_indicator(code):
    url = BASE_URL.format(countries=COUNTRIES, code=code)
    params = {"format": "json", "date": DATE_RANGE, "per_page": 200}
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            print(f"  Attempt {attempt} of {MAX_ATTEMPTS} for {code}...")
            response = requests.get(url, params=params, timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json()
            # World Bank returns [metadata, data] -- data can be None if the
            # indicator code or country code is wrong, so check explicitly.
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


def load_existing_keys(path):
    if not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {
            (row["country_code"], row["indicator_code"], row["year"], row["value"])
            for row in csv.DictReader(f)
        }


def append_new_rows(path, records, indicator_code, indicator_name, existing_keys):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    is_new_file = not os.path.exists(path)
    pulled_at = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    new_count = 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if is_new_file:
            writer.writeheader()
        for rec in records:
            value = rec.get("value")
            if value is None:
                continue  # World Bank leaves recent years blank until data is finalised
            country_code = rec.get("countryiso3code", "")
            country_name = (rec.get("country") or {}).get("value", "")
            year = rec.get("date", "")
            key = (country_code, indicator_code, year, str(value))
            if key in existing_keys:
                continue
            writer.writerow({
                "pulled_at": pulled_at,
                "country": country_name,
                "country_code": country_code,
                "indicator_code": indicator_code,
                "indicator_name": indicator_name,
                "year": year,
                "value": value,
            })
            existing_keys.add(key)
            new_count += 1
    return new_count


def main():
    existing = load_existing_keys(LOG_PATH)
    total_new = 0
    any_failure = False
    for code, name in INDICATORS.items():
        print(f"Fetching {code} ({name})...")
        records = fetch_indicator(code)
        if records is None:
            any_failure = True
            continue
        added = append_new_rows(LOG_PATH, records, code, name, existing)
        print(f"  Added {added} new rows.")
        total_new += added
    print(f"Done. Added {total_new} new rows total across {len(INDICATORS)} indicators.")
    if any_failure:
        print("Note: at least one indicator failed to fetch this run -- it will be retried next run.")


if __name__ == "__main__":
    main()
