"""
Phase 13 — forex feed: USD/ZAR exchange rate via Frankfurter.app.

WHY ZAR, NOT A SEPARATE LSL RATE: the Lesotho Loti has been pegged 1:1 to
the South African Rand since 1974, under the Common Monetary Area
agreement -- a fixed, permanent arrangement, not a managed float that
drifts. There is no independent LSL/USD rate to fetch; ZAR/USD IS the
Loti's dollar rate, exactly. Fetching ZAR directly is the correct
approach, not a shortcut around a missing LSL feed.

WHY FRANKFURTER.APP: genuinely free, no API key or signup required,
backed by European Central Bank reference rates, been a stable public
utility for years. Chosen over the alternatives specifically to avoid
the "unofficial scraper" risk category already ruled out elsewhere in
this project (Google Trends) -- this is a real, documented, versioned
REST API, not a page-scrape.

HONEST LIMITATION, STATED PLAINLY: this project's build environment has
no network access to frankfurter.app (confirmed directly -- the request
was rejected by an egress allowlist, not just assumed to fail), so this
script's actual behaviour against the live API has NOT been verified the
way every other fetch script in this repo has been. The defensive
parsing below (checking response structure before assuming keys exist,
clear error messages on anything unexpected) is written from documented
API knowledge, not confirmed against a live response. Treat the first
real run's Actions log as the actual test, not a formality -- check it
before trusting the output the way you would for every other new source
in earlier phases.

FUEL/ENERGY AND WOOL/MOHAIR/DIAMOND PRICES ARE DELIBERATELY NOT HERE --
see the fuel_energy_prices (deferred_no_free_api) and commodity_signal
(computation) registry entries for why: no free, reliable price API
exists for the former, and no public spot price exists at all for the
latter (auction-based and per-stone-graded goods, not standardized
fungible commodities).
"""
import datetime
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(__file__))
from lib.csv_log import append_new_rows, utc_now_iso  # noqa: E402
from lib.registry import get_source  # noqa: E402

SOURCE = get_source("forex_rates")

BASE_URL = "https://api.frankfurter.app/{date_range}"
DATE_RANGE = SOURCE.get("date_range", "2010-01-01..")  # open-ended end = "through today", per Frankfurter's documented range syntax
FROM_CURRENCY = "USD"
TO_CURRENCY = "ZAR"

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", SOURCE["output"])
FIELDNAMES = ["pulled_at", "date", "base_currency", "quote_currency", "rate", "note"]
KEY_FIELDS = ["date", "base_currency", "quote_currency"]

MAX_ATTEMPTS = 4
TIMEOUT_SECONDS = 30
RETRY_WAIT_SECONDS = 15

PEG_NOTE = "LSL is pegged 1:1 to ZAR under the Common Monetary Area -- this rate applies directly to the Loti."


def fetch_rate_series():
    url = BASE_URL.format(date_range=DATE_RANGE)
    params = {"from": FROM_CURRENCY, "to": TO_CURRENCY}
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            print(f"  Attempt {attempt} of {MAX_ATTEMPTS}: GET {url} params={params}")
            response = requests.get(url, params=params, timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json()
            # Defensive: don't assume the documented shape held -- check
            # explicitly before indexing into it, the same discipline
            # fetch_worldbank_macro.py uses for its own [metadata, data]
            # response shape.
            if not isinstance(payload, dict) or "rates" not in payload:
                print(f"  Unexpected response shape (no 'rates' key): {str(payload)[:300]}")
                return None
            rates_by_date = payload["rates"]
            if not isinstance(rates_by_date, dict) or not rates_by_date:
                print(f"  'rates' present but empty or malformed: {str(rates_by_date)[:300]}")
                return None
            return rates_by_date
        except (requests.exceptions.RequestException, ValueError) as err:
            last_error = err
            print(f"  Attempt {attempt} failed: {err}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_WAIT_SECONDS)
    print(f"  All attempts failed. Last error: {last_error}")
    return None


def main():
    pulled_at = utc_now_iso()
    print(f"Fetching {FROM_CURRENCY}/{TO_CURRENCY} exchange rate history from Frankfurter.app...")
    rates_by_date = fetch_rate_series()

    if rates_by_date is None:
        print("No data fetched this run -- leaving the existing log untouched. Check the error above; if this "
              "persists, the response shape may not match what this script expects (see module docstring: this "
              "was never verified against a live response before shipping).")
        return

    rows = []
    for date_str, currencies in rates_by_date.items():
        if not isinstance(currencies, dict) or TO_CURRENCY not in currencies:
            print(f"  Skipping {date_str}: expected '{TO_CURRENCY}' key not present ({currencies})")
            continue
        rows.append({
            "pulled_at": pulled_at,
            "date": date_str,
            "base_currency": FROM_CURRENCY,
            "quote_currency": TO_CURRENCY,
            "rate": currencies[TO_CURRENCY],
            "note": PEG_NOTE,
        })

    if not rows:
        print("Fetched a response but extracted zero usable rows -- treat as a failed run, not a quiet zero-data day.")
        return

    written = append_new_rows(LOG_PATH, FIELDNAMES, rows, KEY_FIELDS)
    print(f"Fetched {len(rows)} rate(s) from the API, wrote {written} new row(s) to {LOG_PATH} "
          f"({len(rows) - written} already present).")


if __name__ == "__main__":
    main()
