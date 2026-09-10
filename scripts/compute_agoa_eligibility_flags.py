"""
Analyzes the raw AGOA Federal Register feed (fetch_agoa_federal_register.py)
for two things a human reviewer needs to see immediately, not buried in
a raw document list: (1) which documents use language suggesting an
actual eligibility CHANGE (termination, suspension, reinstatement) as
opposed to a routine, expected annual continuation, and (2) which
documents specifically name Lesotho.

WHY THIS IS SEPARATE FROM THE FETCH SCRIPT: matches the fetch/compute
separation used throughout this project -- the fetch script's job is
getting the raw official record; this script's job is flagging what in
that record actually needs a human's attention right now, and that
flagging logic should be able to evolve (better keywords, a corrected
threshold) without touching the already-tested fetch mechanism.

EXTREME CAUTION, STATED DIRECTLY GIVEN THE STAKES: this is simple
keyword matching on a document's TITLE and ABSTRACT, not a reading of
the actual legal text. A title like "Proclamation to Modify Duty-Free
Treatment Under AGOA" is the ROUTINE, EXPECTED annual mechanism -- it
appears most years regardless of whether any actual status change
happens -- so "mentions a proclamation" alone is not a meaningful
signal. What IS checked for is language describing an actual outcome
(terminate, withdraw, suspend, reinstate, graduate, ineligible) within
that routine record. This still cannot substitute for a human actually
reading the linked document. Given your own report named this the most
urgent single finding, every output row links directly to the official
document -- verify there, not from this summary alone.
"""
import datetime
import os
import re
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

INPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "trade_policy", "agoa_federal_register_log.csv")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "agoa_eligibility_flags_log.csv")

OUTPUT_COLUMNS = [
    "computed_at", "document_number", "title", "document_type", "publication_date",
    "agency_names", "mentions_lesotho", "eligibility_language_found", "eligibility_keywords_matched",
    "html_url",
]

# Words suggesting an actual eligibility OUTCOME, not just the routine
# existence of the annual review mechanism itself.
ELIGIBILITY_KEYWORDS = [
    "terminat", "withdraw", "suspend", "reinstat", "graduat", "ineligib",
    "remov", "revoke", "lapse", "expir",
]


def utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def find_keywords(text, keywords):
    text_lower = str(text).lower()
    return [kw for kw in keywords if kw in text_lower]


def mentions_lesotho(title, abstract):
    combined = f"{title} {abstract}".lower()
    return bool(re.search(r"\blesotho\b", combined))


def main():
    computed_at = utc_now_iso()

    if not os.path.exists(INPUT_PATH):
        print("No agoa_federal_register_log.csv available yet -- nothing to analyze. Will try again once that source has run.")
        pd.DataFrame([], columns=OUTPUT_COLUMNS).to_csv(OUTPUT_PATH, index=False)
        return

    df = pd.read_csv(INPUT_PATH, keep_default_na=False)
    if df.empty:
        print("agoa_federal_register_log.csv exists but is empty -- nothing to analyze yet.")
        pd.DataFrame([], columns=OUTPUT_COLUMNS).to_csv(OUTPUT_PATH, index=False)
        return

    rows = []
    for _, doc in df.iterrows():
        combined_text = f"{doc.get('title', '')} {doc.get('abstract', '')}"
        keywords_found = find_keywords(combined_text, ELIGIBILITY_KEYWORDS)
        rows.append({
            "computed_at": computed_at,
            "document_number": doc.get("document_number", ""),
            "title": doc.get("title", ""),
            "document_type": doc.get("document_type", ""),
            "publication_date": doc.get("publication_date", ""),
            "agency_names": doc.get("agency_names", ""),
            "mentions_lesotho": mentions_lesotho(doc.get("title", ""), doc.get("abstract", "")),
            "eligibility_language_found": bool(keywords_found),
            "eligibility_keywords_matched": ";".join(keywords_found),
            "html_url": doc.get("html_url", ""),
        })

    # Most recent first -- the current status is what matters most, though
    # every row stays in the output, not just the latest, since a human
    # reviewer may need the history to understand a recent change in context.
    rows.sort(key=lambda r: r["publication_date"], reverse=True)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    pd.DataFrame(rows, columns=OUTPUT_COLUMNS).to_csv(OUTPUT_PATH, index=False)
    print(f"Wrote {len(rows)} row(s) to {OUTPUT_PATH}.")

    lesotho_rows = [r for r in rows if r["mentions_lesotho"]]
    flagged_rows = [r for r in rows if r["eligibility_language_found"]]
    print(f"  {len(lesotho_rows)} of {len(rows)} documents specifically name Lesotho.")
    print(f"  {len(flagged_rows)} of {len(rows)} documents contain eligibility-outcome language (terminate/withdraw/suspend/etc.).")
    if rows:
        latest = rows[0]
        print(f"  MOST RECENT: [{latest['publication_date']}] {latest['title']}")
        print(f"    Lesotho named: {latest['mentions_lesotho']} | Eligibility language: {latest['eligibility_language_found']} ({latest['eligibility_keywords_matched']})")
        print(f"    VERIFY DIRECTLY, DO NOT RELY ON THIS SUMMARY ALONE: {latest['html_url']}")


if __name__ == "__main__":
    main()
