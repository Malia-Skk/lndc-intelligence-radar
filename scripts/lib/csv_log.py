"""
Shared idempotent-append helpers for the CSV-log-as-database pattern used
throughout this repo: every fetch script appends only the rows it hasn't
written before, identified by a caller-supplied tuple of "key" fields, so
re-running a script (scheduled or manual) never produces duplicate rows.

This is the same logic that already exists three times over, slightly
differently, in fetch_worldbank_indicators.py and fetch_comtrade_automotive.py.
Factored out here so Phase 1's new scripts share one tested implementation
instead of a fourth hand-copied version — the legacy scripts are left as-is
(not worth the risk of touching a working pilot to deduplicate code paths).
"""
import csv
import datetime
import os


def utc_now_iso():
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def load_existing_keys(path, key_fields):
    """Return the set of key_fields tuples already present in an existing CSV
    log. Returns an empty set if the file doesn't exist yet (first run)."""
    if not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {tuple(str(row.get(k, "")) for k in key_fields) for row in csv.DictReader(f)}


def append_new_rows(path, fieldnames, rows, key_fields):
    """Append `rows` (a list of dicts) to the CSV log at `path`, writing a
    header first if the file doesn't exist yet, and skipping any row whose
    key_fields tuple has already been seen — either already in the file, or
    earlier within this same batch of `rows`.

    Returns the number of rows actually written.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    is_new_file = not os.path.exists(path)
    existing_keys = load_existing_keys(path, key_fields)
    new_count = 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if is_new_file:
            writer.writeheader()
        for row in rows:
            key = tuple(str(row.get(k, "")) for k in key_fields)
            if key in existing_keys:
                continue
            writer.writerow(row)
            existing_keys.add(key)
            new_count += 1
    return new_count
