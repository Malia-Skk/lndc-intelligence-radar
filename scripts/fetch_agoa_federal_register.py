"""
Phase D of the predictive-analytics expansion -- dedicated AGOA/critical
trade-policy monitoring, the item your original diagnostic report
flagged as needing "immediate, standalone verification ahead of
everything else" (AGOA, Lesotho's preferential US market access,
reportedly lapsed 30 September 2025 with no confirmed renewal as of
your August 2026 writing).

WHY THIS WAS DEFERRED IN PHASE 5, AND WHY IT'S BEING BUILT NOW: the
original registry entry for this (agoa_status, status:
deferred_no_free_api) checked and found that AGOA eligibility
determinations are published via USTR/Federal Register notices, not a
structured API -- and explicitly declined to build a scraper against a
government NOTICE PAGE (agoa.info, USTR's own site), reasoning that's
exactly the fragile, high-maintenance dependency this project has
otherwise avoided: one page redesign silently breaks it with no error a
real API would surface. That entry's own closing line: "Revisit only if
USTR/AGOA.gov ever exposes a real feed."

THE ACTUAL FEED: not USTR/AGOA.gov itself, but the venue where AGOA
country-eligibility determinations are formally, legally published --
the Federal Register (federalregister.gov), which runs a genuine, free,
documented, structured JSON API at api.federalregister.gov, not an
HTML page to scrape. AGOA country eligibility is set by an annual
Presidential Proclamation ("Proclamation to Modify Duty-Free Treatment
Under the African Growth and Opportunity Act"), which is exactly the
kind of document type this API is built to serve, search, and filter by
-- a fundamentally different and more robust thing than parsing a
marketing website's HTML.

HONEST LIMITATION, STATED PLAINLY: this build environment has no network
access to www.federalregister.gov (confirmed directly, the same
restriction already hit for GDELT/Frankfurter/ReliefWeb), so while this
script is written from genuine, documented knowledge of a well-
established government API, it has never received a real response. The
defensive parsing below (checking for expected keys before accessing
them, printing the raw response shape on anything unexpected) is
written the same way ReliefWeb's first version was -- which turned out
to be correct on its first real run -- but that's a track record, not a
guarantee. Treat the first live run's Actions log as the actual test.

UPDATE after the first real run: the response parsing itself was
correct on the first try (same as ReliefWeb), but the API's own search
for conditions[term]="African Growth and Opportunity Act" turned out to
have very low precision -- 200 results, only 11 (5.5%) actually about
AGOA, the rest unrelated documents (an H-1B visa fee rule, a public-
health quarantine order, an unrelated executive-order rescission) that
matched the API's search for reasons not obvious from outside it. Rather
than guess at different query syntax that can't be verified live anyway,
fixed with a client-side filter (is_genuinely_about_agoa()) that only
keeps a document if ITS OWN title or abstract actually contains "AGOA"
or the full phrase -- a check this script controls completely and can
test directly, regardless of how the API's search actually behaves.

SCOPE: fetches ALL Federal Register documents mentioning AGOA, not just
ones naming Lesotho specifically. This is deliberate, matching this
project's existing peer/competitive-intelligence pattern (Phase A's
critical-minerals peer monitoring, Phase 12's peer insights): seeing
which OTHER countries gain or lose eligibility in the same proclamation
is itself real regional context, not noise -- the downstream computation
(compute_agoa_eligibility_flags.py) is what specifically calls out
whether a given document names Lesotho.
"""
import csv
import datetime
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(__file__))
from lib.csv_log import append_new_rows, utc_now_iso  # noqa: E402

API_URL = "https://www.federalregister.gov/api/v1/documents.json"

PARAMS = {
    "conditions[term]": "African Growth and Opportunity Act",
    "per_page": 200,
    "order": "newest",
}

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "trade_policy", "agoa_federal_register_log.csv")
FIELDNAMES = [
    "pulled_at", "document_number", "title", "document_type", "publication_date",
    "agency_names", "abstract", "html_url",
]
KEY_FIELDS = ["document_number"]

MAX_ATTEMPTS = 4
TIMEOUT_SECONDS = 30
RETRY_WAIT_SECONDS = 20


def fetch_documents():
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            print(f"  Attempt {attempt} of {MAX_ATTEMPTS}: GET {API_URL} params={PARAMS}")
            response = requests.get(API_URL, params=PARAMS, timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            body = response.text
            if not body.strip():
                raise ValueError("Federal Register API returned an empty response body")
            payload = json.loads(body)
            # Defensive: don't assume the documented shape held -- check
            # explicitly before indexing into it, the same discipline
            # every other new source in this project has used.
            if not isinstance(payload, dict) or "results" not in payload:
                print(f"  Unexpected response shape (no 'results' key): {str(payload)[:300]}")
                return None
            results = payload["results"]
            if not isinstance(results, list):
                print(f"  'results' present but not a list: {str(results)[:300]}")
                return None
            print(f"  Received {len(results)} document(s) (API reports {payload.get('count', 'unknown')} total matches).")
            return results
        except (requests.exceptions.RequestException, ValueError) as err:
            last_error = err
            print(f"  Attempt {attempt} failed: {err}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_WAIT_SECONDS)
    print(f"  All {MAX_ATTEMPTS} attempts failed. Last error: {last_error}")
    return None


def is_genuinely_about_agoa(title, abstract):
    """A real, observed problem, not a hypothetical one: the first live
    run showed the API's own search returning 200 results for
    conditions[term]="African Growth and Opportunity Act", but only 11
    of them (5.5%) actually mention AGOA in their own title or abstract
    -- the rest were unrelated documents (an H-1B visa fee rule, a
    public-health quarantine order, an unrelated executive-order
    rescission) that matched the API's own search for reasons that
    aren't clear from outside it, likely a looser relevance match than
    an exact-phrase search. Rather than guess at different query syntax
    that can't be tested live anyway, this filters client-side: a
    document only survives if ITS OWN title or abstract genuinely
    contains "AGOA" or "African Growth and Opportunity Act" as text,
    which is a check this script controls completely and can verify
    directly against real data, regardless of how the API's own search
    actually behaves."""
    combined = f"{title} {abstract}".lower()
    return "agoa" in combined or "african growth and opportunity act" in combined


def extract_row(doc, pulled_at):
    """Defensive field-by-field extraction -- a document missing an
    expected field gets an empty string for that field rather than
    crashing the whole run, since a single malformed record shouldn't
    take down everything else that parsed fine."""
    agencies = doc.get("agencies") or []
    agency_names = ";".join(a.get("name", "") for a in agencies if isinstance(a, dict))
    return {
        "pulled_at": pulled_at,
        "document_number": doc.get("document_number", ""),
        "title": doc.get("title", ""),
        "document_type": doc.get("type", ""),
        "publication_date": doc.get("publication_date", ""),
        "agency_names": agency_names,
        "abstract": doc.get("abstract") or "",
        "html_url": doc.get("html_url", ""),
    }


def main():
    pulled_at = utc_now_iso()
    print("Fetching Federal Register documents mentioning the African Growth and Opportunity Act...")
    results = fetch_documents()

    if results is None:
        print("No data fetched this run -- leaving the existing log untouched. If this persists, the "
              "response shape may not match what this script expects (see module docstring: this was "
              "never verified against a live response before shipping).")
        return

    rows = []
    filtered_out = 0
    for doc in results:
        if not doc.get("document_number"):
            print(f"  Skipping a result with no document_number (can't dedupe it safely): {str(doc)[:200]}")
            continue
        if not is_genuinely_about_agoa(doc.get("title", ""), doc.get("abstract") or ""):
            filtered_out += 1
            continue
        rows.append(extract_row(doc, pulled_at))

    print(f"  {len(results)} results from the API's own search; {filtered_out} filtered out as not genuinely "
          f"mentioning AGOA in their own title/abstract; {len(rows)} kept.")

    if not rows:
        print("Fetched a response but extracted zero usable rows -- treat as a failed run, not a quiet zero-data day.")
        return

    written = append_new_rows(LOG_PATH, FIELDNAMES, rows, KEY_FIELDS)
    print(f"Fetched {len(rows)} document(s), wrote {written} new row(s) to {LOG_PATH} ({len(rows) - written} already present).")


if __name__ == "__main__":
    main()
