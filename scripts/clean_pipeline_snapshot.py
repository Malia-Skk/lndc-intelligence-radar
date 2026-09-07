"""
Phase 4 — clean and normalize an LNDC investment/industrialist pipeline
snapshot into a canonical schema.

This is fundamentally different from every previous phase's scripts: there
is no external API here. The input is a manually-maintained spreadsheet
(the "Pipeline" tab is the canonical source, per an earlier decision --
other tabs in the same workbook were found to disagree with it and with
each other, e.g. the same company shown as "Completed" in one tab and
"Shelved" in another). This script's job is to take whatever version of
that spreadsheet LNDC has most recently produced and turn it into
something the radar can actually rely on, flagging rather than silently
fixing anything ambiguous.

WHERE THIS RUNS AND WHY:
This runs in the same public repo as every other phase, writing to
data/pipeline/. That's a deliberate choice, not a default: company names,
deal stages, and negotiation status are the kind of thing that would
normally warrant a separate, access-controlled repo (an earlier version of
this design used exactly that -- a private repo plus an authenticated
Worker-proxied read path for the frontend). That approach was set aside in
favour of treating this data as non-sensitive, per an explicit decision to
avoid the added infrastructure -- worth knowing if this project's scope or
audience ever changes, since the tradeoff being made here is real: this
data becomes as publicly fetchable as everything else in this repo.

REAL DATA-QUALITY ISSUES FOUND IN THE ACTUAL SOURCE FILE (fixed generically
below, not special-cased per column, since the same bug -- inconsistent
trailing whitespace from manual entry -- showed up independently in at
least five different columns: Origin (FDI/DI), Sector, Lead Type, Origin
(country), and Target Estate):
  - Whitespace inconsistency: "FDI" vs "FDI ", "Garments" vs "Garments ",
    "New" vs "New ", etc. -- fixed by stripping whitespace on every text
    column, not just the ones spotted by eye.
  - Country-name inconsistency: "South Africa", "South Africa ", "RSA",
    and "South- Africa " all refer to the same country -- fixed via an
    explicit alias map (COUNTRY_ALIASES below). Deliberately NOT extended
    to fuzzy-match arbitrary spellings -- an alias map you can read and
    audit beats a fuzzy matcher that silently "corrects" something wrong.
  - "Required/allocated factory size sqm" mixes numbers with the literal
    text "TBD" and blanks -- coerced to numeric with a separate status
    flag, not silently dropped or coerced to zero.
  - Two "Unnamed" columns in the source (no header at all) contain a
    handful of stray numbers that don't correlate with any other column
    for those rows -- almost certainly leftover manual-calculation
    artifacts. These are NOT included in canonical output, but every
    instance is written to the data-quality log by company name, so
    nothing is silently thrown away without a record of it.
  - "Sector" contains both "Garments" (11 rows) and "Apparel" (1 row) --
    plausibly the same category under two names, but this is a business
    categorisation call, not a whitespace bug, so it's flagged in the
    data-quality log rather than merged automatically.

WHY "ESTIMATED EMPLOYMENT AT FULL CAPACITY" IS NOT TREATED AS REALISED JOBS:
The Stage column's "Completed" value has no confirmed definition (deal
signed vs. fully operational vs. inconsistently used -- see the recorded
conversation this script's design came out of). Full-capacity employment
therefore always represents POTENTIAL, not confirmed, jobs -- weighted by
an explicitly-tagged assumption about how likely each stage is to convert,
never presented as a measured or confirmed figure.
"""
import argparse
import datetime
import os
import re
import sys

import pandas as pd

# ---------------------------------------------------------------------
# Canonical schema and cleaning rules
# ---------------------------------------------------------------------

EXPECTED_COLUMNS = {
    "no": "NO",
    "origin (fdi/di)": "Origin (FDI/DI)",
    "company name": "Company Name",
    "sector": "Sector",
    "lead type": "Lead Type",
    "stage": "Stage",
    "origin": "Origin",
    "required/allocated factory size sqm": "Required/allocated factory size sqm",
    "estimated employment at full capacity": "Estimated employment at full capacity",
    "target estate": "Target Estate",
}

COUNTRY_ALIASES = {
    "rsa": "South Africa",
    "south africa": "South Africa",
    "south- africa": "South Africa",
    "south-africa": "South Africa",
    "lesotho": "Lesotho",
    "china": "China",
    "england": "England",
    "zimbabwe": "Zimbabwe",
    "malawi": "Malawi",
}

# No transition history exists yet (confirmed -- this is a single snapshot
# with no prior versions to learn stage-conversion rates from). These are
# therefore explicit ASSUMPTIONS, not measured probabilities, and every
# row derived from them is tagged accordingly so nothing downstream
# mistakes a guess for data. Replace with empirically-derived rates the
# moment enough snapshot history exists to compute real transition rates.
STAGE_CONVERSION_WEIGHTS = {
    "Shelved": 0.0,
    "Appraisal": 0.15,
    "Negotiation": 0.35,
    "Implementation": 0.75,
    "Completed": 0.90,  # not 1.0 -- "Completed" meaning is unconfirmed, see module docstring
}
STAGE_PROBABILITY_SOURCE = "assumption_v1 -- not yet empirically derived, no snapshot history available"

# The strategy's Outcome 3 target: "100 new Basotho industrialists who
# employ at least 100 people each." A DI (domestic investment / Basotho-
# origin) lead only qualifies as a CANDIDATE toward this count if, even at
# full capacity, it would clear this threshold -- most current DI leads do
# not (see PHASE4_SUMMARY.md for the actual count in this snapshot).
INDUSTRIALIST_EMPLOYMENT_THRESHOLD = 100


def normalize_colname(col):
    return re.sub(r"\s+", " ", str(col)).strip().lower()


def find_expected_columns(df):
    """Maps the actual columns present in this snapshot to the canonical
    names, tolerant of case and whitespace drift between snapshots (this
    is a manually-maintained spreadsheet -- headers WILL drift over time).
    Raises with a clear message if a required column truly can't be found,
    rather than silently proceeding with misaligned data."""
    normalized_to_actual = {normalize_colname(c): c for c in df.columns}
    resolved = {}
    missing = []
    for normalized_expected, canonical_name in EXPECTED_COLUMNS.items():
        if normalized_expected in normalized_to_actual:
            resolved[canonical_name] = normalized_to_actual[normalized_expected]
        else:
            missing.append(canonical_name)
    if missing:
        raise SystemExit(
            f"Could not find these expected columns in the source file: {missing}. "
            f"Actual columns present: {list(df.columns)}. "
            "If the source spreadsheet's headers changed, update EXPECTED_COLUMNS."
        )
    return resolved


def strip_whitespace_everywhere(df, text_columns):
    quality_notes = []
    for col in text_columns:
        before = df[col].astype(str)
        after = before.str.strip()
        changed = (before != after) & df[col].notna()
        if changed.any():
            quality_notes.append(
                f"Stripped whitespace from {int(changed.sum())} value(s) in '{col}'."
            )
        df[col] = after
    return df, quality_notes


def normalize_country(value):
    if pd.isna(value):
        return value, False
    key = re.sub(r"\s+", " ", str(value)).strip().lower()
    if key in COUNTRY_ALIASES:
        canonical = COUNTRY_ALIASES[key]
        return canonical, canonical != str(value).strip()
    return str(value).strip(), False


def coerce_factory_size(value):
    """Returns (numeric_value_or_None, status) -- status is 'known',
    'tbd', or 'missing'. Never silently coerces "TBD" to 0 or NaN without
    a status flag explaining why the number is absent."""
    if pd.isna(value):
        return None, "missing"
    text = str(value).strip()
    if text.upper() == "TBD":
        return None, "tbd"
    try:
        return float(text), "known"
    except ValueError:
        return None, "unrecognised"


def find_stray_unnamed_values(raw_df, company_col):
    """Records any non-null values sitting in headerless "Unnamed: N"
    columns, by company name, so they're documented rather than silently
    dropped when those columns are excluded from canonical output."""
    notes = []
    unnamed_cols = [c for c in raw_df.columns if str(c).startswith("Unnamed:")]
    for col in unnamed_cols:
        rows_with_values = raw_df[raw_df[col].notna()]
        for _, row in rows_with_values.iterrows():
            company = str(row.get(company_col, "<unknown company>")).strip()
            notes.append(
                f"Stray value {row[col]!r} found in headerless column '{col}' for "
                f"'{company}' -- excluded from canonical output, not correlated with "
                f"any other column for this row, likely a leftover manual-calculation "
                f"artifact. Verify against the source file if this matters."
            )
    return notes


def parse_snapshot_date(filename, cli_override):
    if cli_override:
        return cli_override
    # Best-effort parse of filenames like "Pipeline2026Aug16th.xlsx" --
    # falls back to today's date with a clear warning if the pattern
    # doesn't match, rather than guessing silently.
    match = re.search(r"(\d{4})([A-Za-z]{3})(\d{1,2})", os.path.basename(filename))
    if match:
        year, month_abbr, day = match.groups()
        try:
            parsed = datetime.datetime.strptime(f"{year}-{month_abbr}-{day}", "%Y-%b-%d")
            return parsed.date().isoformat()
        except ValueError:
            pass
    today = datetime.date.today().isoformat()
    print(
        f"WARNING: could not parse a snapshot date from filename '{filename}'. "
        f"Using today's date ({today}) instead -- pass --snapshot-date explicitly "
        "to override this."
    )
    return today


def clean_snapshot(input_path, snapshot_date_override=None):
    raw_df = pd.read_excel(input_path, sheet_name="Pipeline")
    raw_df = raw_df.dropna(how="all")

    columns = find_expected_columns(raw_df)
    no_col = columns["NO"]
    df = raw_df[raw_df[no_col].notna()].copy()

    quality_notes = []
    quality_notes.extend(find_stray_unnamed_values(raw_df[raw_df[no_col].notna()], columns["Company Name"]))

    text_cols_to_strip = [
        columns["Origin (FDI/DI)"], columns["Sector"], columns["Lead Type"],
        columns["Stage"], columns["Origin"], columns["Target Estate"],
        columns["Company Name"],
    ]
    df, strip_notes = strip_whitespace_everywhere(df, text_cols_to_strip)
    quality_notes.extend(strip_notes)

    origin_fdi_di = df[columns["Origin (FDI/DI)"]]
    unexpected_fdi_di = set(origin_fdi_di.unique()) - {"FDI", "DI"}
    if unexpected_fdi_di:
        quality_notes.append(
            f"Unexpected values in Origin (FDI/DI) after whitespace cleanup: {unexpected_fdi_di}. "
            "Expected only 'FDI' or 'DI' -- check the source file."
        )

    sector_values = set(df[columns["Sector"]].unique())
    if "Garments" in sector_values and "Apparel" in sector_values:
        quality_notes.append(
            "Sector contains both 'Garments' and 'Apparel' -- these may be the same "
            "category under two names. Not merged automatically (a categorisation "
            "decision, not a whitespace bug) -- confirm with LNDC whether to unify them."
        )

    country_results = df[columns["Origin"]].apply(normalize_country)
    df["origin_country_clean"] = [r[0] for r in country_results]
    n_country_fixes = sum(1 for r in country_results if r[1])
    if n_country_fixes:
        quality_notes.append(f"Normalised {n_country_fixes} country-name value(s) via the alias map.")

    factory_results = df[columns["Required/allocated factory size sqm"]].apply(coerce_factory_size)
    df["factory_size_sqm_clean"] = [r[0] for r in factory_results]
    df["factory_size_status"] = [r[1] for r in factory_results]

    df["stage_clean"] = df[columns["Stage"]]
    unknown_stages = set(df["stage_clean"].unique()) - set(STAGE_CONVERSION_WEIGHTS.keys())
    if unknown_stages:
        quality_notes.append(
            f"Stage value(s) with no defined conversion weight: {unknown_stages} -- "
            "treated as 0.0 (most conservative assumption) until this is resolved."
        )
    df["stage_conversion_weight"] = df["stage_clean"].map(STAGE_CONVERSION_WEIGHTS).fillna(0.0)
    df["stage_probability_source"] = STAGE_PROBABILITY_SOURCE

    df["estimated_employment_full_capacity"] = df[columns["Estimated employment at full capacity"]]
    df["expected_jobs_weighted"] = (
        df["estimated_employment_full_capacity"] * df["stage_conversion_weight"]
    ).round(1)

    df["is_new_industrialist_candidate"] = (
        (df[columns["Origin (FDI/DI)"]] == "DI")
        & (df["estimated_employment_full_capacity"] >= INDUSTRIALIST_EMPLOYMENT_THRESHOLD)
    )

    canonical = pd.DataFrame({
        "company_name": df[columns["Company Name"]],
        "origin_fdi_di": df[columns["Origin (FDI/DI)"]],
        "sector": df[columns["Sector"]],
        "lead_type": df[columns["Lead Type"]],
        "stage": df["stage_clean"],
        "stage_conversion_weight": df["stage_conversion_weight"],
        "stage_probability_source": df["stage_probability_source"],
        "origin_country": df["origin_country_clean"],
        "factory_size_sqm": df["factory_size_sqm_clean"],
        "factory_size_status": df["factory_size_status"],
        "estimated_employment_full_capacity": df["estimated_employment_full_capacity"],
        "expected_jobs_weighted": df["expected_jobs_weighted"],
        "is_new_industrialist_candidate": df["is_new_industrialist_candidate"],
        "target_estate": df[columns["Target Estate"]],
    })

    return canonical, quality_notes


def build_snapshot_summary(canonical, snapshot_date, source_filename):
    confirmed_new_industrialists = 0  # see module docstring -- baseline confirmed at 0 by LNDC
    return {
        "snapshot_date": snapshot_date,
        "source_filename": source_filename,
        "processed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "total_leads": len(canonical),
        "total_potential_jobs_full_capacity": int(canonical["estimated_employment_full_capacity"].sum()),
        "total_expected_jobs_weighted": round(canonical["expected_jobs_weighted"].sum(), 1),
        "leads_by_stage": canonical["stage"].value_counts().to_dict(),
        "di_leads_count": int((canonical["origin_fdi_di"] == "DI").sum()),
        "di_industrialist_candidates_count": int(canonical["is_new_industrialist_candidate"].sum()),
        "confirmed_new_industrialists_count": confirmed_new_industrialists,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_path", help="Path to the raw pipeline .xlsx snapshot")
    parser.add_argument("--snapshot-date", default=None, help="Override the auto-detected snapshot date (YYYY-MM-DD)")
    parser.add_argument("--output-dir", default="data/pipeline", help="Directory to write canonical/archive/quality-log CSVs into")
    args = parser.parse_args()

    snapshot_date = parse_snapshot_date(args.input_path, args.snapshot_date)
    canonical, quality_notes = clean_snapshot(args.input_path, args.snapshot_date)
    summary = build_snapshot_summary(canonical, snapshot_date, os.path.basename(args.input_path))

    os.makedirs(args.output_dir, exist_ok=True)

    canonical_path = os.path.join(args.output_dir, f"pipeline_canonical_{snapshot_date}.csv")
    canonical.to_csv(canonical_path, index=False)
    print(f"Wrote {len(canonical)} canonical rows to {canonical_path}")

    # A stable filename alongside the dated archive copy -- so a consumer
    # (the frontend, "Ask the Radar", anything else) always has one place
    # to read "the current state" from, without needing to know or guess
    # the latest snapshot date. The dated file above is what makes this
    # auditable over time; this one is what makes it usable right now.
    latest_path = os.path.join(args.output_dir, "pipeline_canonical_latest.csv")
    canonical.to_csv(latest_path, index=False)
    print(f"Updated {latest_path} (always reflects the most recently processed snapshot)")

    archive_path = os.path.join(args.output_dir, "pipeline_snapshot_archive.csv")
    archive_row = pd.DataFrame([summary])
    archive_row["leads_by_stage"] = archive_row["leads_by_stage"].apply(str)  # flatten dict for CSV
    if os.path.exists(archive_path):
        existing = pd.read_csv(archive_path)
        existing = existing[existing["snapshot_date"] != snapshot_date]  # replace, don't duplicate, if re-run for the same date
        combined = pd.concat([existing, archive_row], ignore_index=True)
    else:
        combined = archive_row
    combined.to_csv(archive_path, index=False)
    print(f"Updated snapshot archive at {archive_path} ({len(combined)} snapshot(s) total)")

    quality_log_path = os.path.join(args.output_dir, "pipeline_data_quality_log.csv")
    quality_df = pd.DataFrame({
        "snapshot_date": snapshot_date,
        "note": quality_notes if quality_notes else ["No data-quality issues flagged for this snapshot."],
    })
    if os.path.exists(quality_log_path):
        existing_q = pd.read_csv(quality_log_path)
        existing_q = existing_q[existing_q["snapshot_date"] != snapshot_date]
        combined_q = pd.concat([existing_q, quality_df], ignore_index=True)
    else:
        combined_q = quality_df
    combined_q.to_csv(quality_log_path, index=False)
    print(f"Wrote {len(quality_notes)} data-quality note(s) to {quality_log_path}")

    print()
    print(f"Snapshot summary ({snapshot_date}):")
    for key, value in summary.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
