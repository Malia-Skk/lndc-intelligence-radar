"""
One-time cleanup, not a recurring computation: removes the 189 false-
positive rows that were committed to agoa_federal_register_log.csv
before is_genuinely_about_agoa() was added to fetch_agoa_federal_register.py.
That fix prevents new false positives from being added going forward,
but its idempotent-append dedup (append_new_rows, keyed on
document_number) only ever ADDS rows -- it has no mechanism to remove
rows already written, so the 189 already-committed noise rows (an H-1B
visa fee rule, a public-health quarantine order, and 187 others unrelated
to AGOA) would otherwise sit in the log forever, feeding noise into
compute_agoa_eligibility_flags.py on every future run.

Run this ONCE, manually (python scripts/clean_agoa_federal_register.py),
then commit the result. Not wired into any scheduled workflow, matching
the precedent of clean_pipeline_snapshot.py -- a one-off correction, not
an ongoing job.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from fetch_agoa_federal_register import is_genuinely_about_agoa  # noqa: E402 -- reusing the exact same filter, not a second copy that could drift

LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "trade_policy", "agoa_federal_register_log.csv")


def main():
    if not os.path.exists(LOG_PATH):
        print(f"{LOG_PATH} doesn't exist -- nothing to clean.")
        return

    df = pd.read_csv(LOG_PATH, keep_default_na=False)
    before = len(df)
    kept = df[df.apply(lambda r: is_genuinely_about_agoa(r.get("title", ""), r.get("abstract", "")), axis=1)]
    removed = before - len(kept)

    if removed == 0:
        print(f"All {before} rows already genuinely mention AGOA -- nothing to remove.")
        return

    kept.to_csv(LOG_PATH, index=False)
    print(f"Removed {removed} false-positive row(s) (kept {len(kept)} of {before}). "
          f"Review the diff before committing -- this rewrites the whole file.")


if __name__ == "__main__":
    main()
