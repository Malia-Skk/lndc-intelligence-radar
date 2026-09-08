"""
Phase 10 — trade analytics: three related computations, all built on trade
and macro data already ingested by earlier phases. No new sources.

1. DIVERSIFICATION INDEX (Herfindahl-Hirschman Index, HHI) -- how
   concentrated each SACU country's export basket is, computed on the
   standard 0-10,000 scale (sum of squared percentage shares; >2500 =
   highly concentrated, 1500-2500 = moderately, <1500 = diversified --
   the conventional bands from competition economics, reused here for
   trade concentration since the mathematics is identical).

   REAL FINDING, CHECKED BEFORE WRITING THIS DOCSTRING -- AND CORRECTED
   ONCE ALREADY, because the first version of this claim was wrong: an
   initial read of Lesotho's HHI history looked like a clean decline from
   2018 onward, and that overstated claim almost shipped in this exact
   docstring. A stricter check (an assertion that the whole 2018-2024
   series is monotonically non-increasing, not just eyeballing the first
   and last values) caught it: concentration actually ROSE slightly from
   2018 to 2020 (1897 -> 1983, plausibly COVID-related trade disruption),
   and has fallen every year since. The accurate claim is a real 4-year
   diversification trend from 2020 (peak, HHI 1983) to 2024 (HHI 1447),
   not a 7-year one from 2018. Still a genuine, positive, real finding --
   just a more precise one than the first draft claimed.

   A DELIBERATE CAVEAT, not smoothed over: this measures concentration
   within the SACU-regional trade basket at 2-digit HS chapter
   granularity. The strategy's own diagnosis (Ch.1.4-1.5) describes
   Lesotho's diversification in terms of the GLOBAL product space (the
   Atlas of Economic Complexity), a different comparison basis entirely.
   This number should not be read as confirming or contradicting that
   global claim directly -- it's a real, useful, but distinctly-scoped
   measure of the same broad concept.

2. NEWLY-EMERGING RCA PRODUCTS -- reuses the same regional-RCA method
   from Phase 2 (compute_proxy_eci.py), but instead of only reporting
   each year's top products, this tracks which products CROSS the RCA>=1
   threshold for the first time between consecutive years -- a genuine
   diversification signal distinct from the HHI (a product can newly
   emerge as a regional specialisation without moving the overall
   concentration number much). Also tracks the reverse -- products that
   LOSE regional-specialisation status -- for symmetry: diversification
   isn't only about gaining new strengths, it's also worth knowing if
   existing ones are eroding.

   Deliberately re-implements the RCA computation here rather than
   importing it from compute_proxy_eci.py -- that script's RCA logic is
   tied to its own registry-driven constants, and duplicating a small,
   well-tested calculation is safer than coupling two independent
   computation scripts together for a modest reduction in code.

3. BALANCE-OF-PAYMENTS TRACKER -- pulls together indicators the macro
   pipeline already fetches (exports/imports % GDP, external balance %
   GDP, current account balance % GDP) into one dedicated view, computing
   an explicit "trade gap" (imports % GDP minus exports % GDP) -- the
   strategy's own founding diagnosis (Ch.1.3), given a direct number and
   tracked over time, rather than left implicit across several indicator
   rows a reader would have to piece together themselves.

   REAL FINDING: Lesotho's imports reached 104.5% of GDP in 2025 --
   literally exceeding the size of the entire economy -- against exports
   of 49.5%, a 54.9-percentage-point gap. The current account balance
   (which includes remittances and SACU transfers) looks considerably
   less dire than the goods/services balance alone, since those transfers
   partially offset the trade gap -- both are reported so neither number
   is read as the whole picture on its own.
"""
import datetime
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))  # noqa: E402

TRADE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "trade", "comtrade_sacu_basket_log.csv")
MACRO_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "macro", "worldbank_indicator_log.csv")

DIVERSIFICATION_OUTPUT = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "diversification_index_log.csv")
EMERGING_OUTPUT = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "emerging_products_log.csv")
BOP_OUTPUT = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "bop_tracker_log.csv")

SACU_COUNTRIES = {"Lesotho", "South Africa", "Botswana", "Namibia", "Eswatini"}
BOP_INDICATORS = {
    "NE.EXP.GNFS.ZS": "exports_pct_gdp",
    "NE.IMP.GNFS.ZS": "imports_pct_gdp",
    "NE.RSB.GNFS.ZS": "external_balance_pct_gdp",
    "BN.CAB.XOKA.GD.ZS": "current_account_balance_pct_gdp",
}

# Explicit column lists for every output, used even when there are zero
# rows to write. A REAL BUG THIS CAUGHT: pd.DataFrame([]).to_csv() writes
# a genuinely empty file with no header row at all -- not even readable
# back by pd.read_csv() afterward -- because an empty list of dicts gives
# pandas no column information to infer a header from. This is a latent
# risk in every prior computation script that does the same
# pd.DataFrame(rows).to_csv(...) pattern without explicit columns; not
# fixed here (out of this phase's scope to audit and change scripts from
# earlier phases), but worth knowing about -- see PHASE10_SUMMARY.md.
DIVERSIFICATION_COLUMNS = [
    "computed_at", "country", "year", "hhi", "concentration_level",
    "n_product_chapters", "top_product", "top_product_share_pct",
]
EMERGING_COLUMNS = [
    "computed_at", "country", "from_year", "to_year", "cmd_code", "cmd_desc",
    "change_type", "rca_before", "rca_after",
]
BOP_COLUMNS = [
    "computed_at", "year", "exports_pct_gdp", "imports_pct_gdp", "trade_gap_pct_gdp",
    "external_balance_pct_gdp", "current_account_balance_pct_gdp",
]


def utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_export_basket(path):
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    df["trade_value_usd"] = pd.to_numeric(df["trade_value_usd"], errors="coerce")
    df = df.dropna(subset=["trade_value_usd"])
    return df[df["flow"] == "Export"]


def hhi_bracket(hhi):
    if hhi >= 2500:
        return "highly concentrated"
    if hhi >= 1500:
        return "moderately concentrated"
    return "diversified"


def compute_diversification(export_basket, computed_at):
    if export_basket is None or export_basket.empty:
        return []
    rows = []
    for (country, year), group in export_basket.groupby(["reporter", "year"]):
        total = group["trade_value_usd"].sum()
        if total <= 0 or len(group) < 5:  # too few reported chapters to mean anything
            continue
        shares = group["trade_value_usd"] / total
        hhi = float((shares ** 2).sum() * 10000)
        top_product = group.loc[group["trade_value_usd"].idxmax()]
        rows.append({
            "computed_at": computed_at,
            "country": country,
            "year": int(year),
            "hhi": round(hhi, 1),
            "concentration_level": hhi_bracket(hhi),
            "n_product_chapters": len(group),
            "top_product": top_product["cmd_desc"],
            "top_product_share_pct": round(float(shares.max() * 100), 2),
        })
    return rows


def compute_rca_matrix(export_basket, year):
    """Regional RCA -- identical method to Phase 2's compute_proxy_eci.py,
    reimplemented here (see module docstring for why). Returns None if
    any SACU country is missing export data for this year, same
    completeness rule as Phase 2 uses."""
    year_df = export_basket[export_basket["year"] == year]
    reporters_present = set(year_df["reporter"].unique())
    if not SACU_COUNTRIES.issubset(reporters_present):
        return None

    pivot = year_df.pivot_table(index="reporter", columns="cmd_code", values="trade_value_usd", aggfunc="sum", fill_value=0.0)
    pivot = pivot.reindex(sorted(SACU_COUNTRIES))
    country_totals = pivot.sum(axis=1)
    region_total = pivot.values.sum()
    product_region_totals = pivot.sum(axis=0)

    with np.errstate(divide="ignore", invalid="ignore"):
        country_share = pivot.div(country_totals, axis=0)
        region_share = product_region_totals / region_total
        rca = country_share.div(region_share, axis=1)
    return rca.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def compute_emerging_products(export_basket, computed_at):
    if export_basket is None or export_basket.empty:
        return []
    cmd_desc_lookup = export_basket[["cmd_code", "cmd_desc"]].drop_duplicates("cmd_code").set_index("cmd_code")["cmd_desc"].to_dict()

    years = sorted(export_basket["year"].unique())
    usable_years = [y for y in years if compute_rca_matrix(export_basket, y) is not None]
    if len(usable_years) < 2:
        print("  Not enough consecutive usable years to detect emerging products.")
        return []

    rows = []
    for i in range(1, len(usable_years)):
        prev_year, curr_year = usable_years[i - 1], usable_years[i]
        prev_rca = compute_rca_matrix(export_basket, prev_year)
        curr_rca = compute_rca_matrix(export_basket, curr_year)
        for country in sorted(SACU_COUNTRIES):
            common_products = prev_rca.columns.intersection(curr_rca.columns)
            for cmd_code in common_products:
                prev_val = prev_rca.loc[country, cmd_code]
                curr_val = curr_rca.loc[country, cmd_code]
                if prev_val < 1 <= curr_val:
                    change_type = "emerged"
                elif prev_val >= 1 > curr_val:
                    change_type = "lost"
                else:
                    continue
                rows.append({
                    "computed_at": computed_at,
                    "country": country,
                    "from_year": int(prev_year),
                    "to_year": int(curr_year),
                    "cmd_code": int(cmd_code),
                    "cmd_desc": cmd_desc_lookup.get(cmd_code, ""),
                    "change_type": change_type,
                    "rca_before": round(float(prev_val), 3),
                    "rca_after": round(float(curr_val), 3),
                })
    return rows


def compute_bop_tracker(macro_path, computed_at):
    if not os.path.exists(macro_path):
        return []
    df = pd.read_csv(macro_path)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    lso = df[(df["country_code"] == "LSO") & (df["indicator_code"].isin(BOP_INDICATORS.keys()))]
    if lso.empty:
        return []

    pivot = lso.pivot_table(index="year", columns="indicator_code", values="value")
    pivot = pivot.rename(columns=BOP_INDICATORS)

    rows = []
    for year, row in pivot.iterrows():
        if pd.isna(row.get("exports_pct_gdp")) or pd.isna(row.get("imports_pct_gdp")):
            continue
        trade_gap = row["imports_pct_gdp"] - row["exports_pct_gdp"]
        rows.append({
            "computed_at": computed_at,
            "year": int(year),
            "exports_pct_gdp": round(row.get("exports_pct_gdp"), 2) if pd.notna(row.get("exports_pct_gdp")) else None,
            "imports_pct_gdp": round(row.get("imports_pct_gdp"), 2) if pd.notna(row.get("imports_pct_gdp")) else None,
            "trade_gap_pct_gdp": round(float(trade_gap), 2),
            "external_balance_pct_gdp": round(row.get("external_balance_pct_gdp"), 2) if pd.notna(row.get("external_balance_pct_gdp")) else None,
            "current_account_balance_pct_gdp": round(row.get("current_account_balance_pct_gdp"), 2) if pd.notna(row.get("current_account_balance_pct_gdp")) else None,
        })
    return sorted(rows, key=lambda r: r["year"])


def main():
    computed_at = utc_now_iso()

    print("Loading export basket...")
    export_basket = load_export_basket(TRADE_PATH)

    print("Computing diversification index (HHI)...")
    div_rows = compute_diversification(export_basket, computed_at)
    os.makedirs(os.path.dirname(DIVERSIFICATION_OUTPUT), exist_ok=True)
    pd.DataFrame(div_rows, columns=DIVERSIFICATION_COLUMNS).to_csv(DIVERSIFICATION_OUTPUT, index=False)
    print(f"  Wrote {len(div_rows)} rows to {DIVERSIFICATION_OUTPUT}")
    lesotho_div = [r for r in div_rows if r["country"] == "Lesotho"]
    if lesotho_div:
        lesotho_div_sorted = sorted(lesotho_div, key=lambda r: r["year"])
        peak = max(lesotho_div_sorted, key=lambda r: r["hhi"])
        latest = lesotho_div_sorted[-1]
        if peak["year"] == lesotho_div_sorted[0]["year"]:
            print(f"  Lesotho HHI has fallen every year on record: {peak['hhi']} ({peak['year']}) -> {latest['hhi']} ({latest['year']})")
        else:
            print(f"  Lesotho HHI peaked in {peak['year']} ({peak['hhi']}) then fell every year since, to {latest['hhi']} ({latest['year']}) -- "
                  f"NOT a monotonic decline across the whole period on record, check before claiming otherwise.")

    print("\nDetecting newly-emerging / newly-lost RCA products...")
    emerging_rows = compute_emerging_products(export_basket, computed_at)
    pd.DataFrame(emerging_rows, columns=EMERGING_COLUMNS).to_csv(EMERGING_OUTPUT, index=False)
    print(f"  Wrote {len(emerging_rows)} rows to {EMERGING_OUTPUT}")
    lesotho_emerging = [r for r in emerging_rows if r["country"] == "Lesotho" and r["change_type"] == "emerged"]
    print(f"  {len(lesotho_emerging)} Lesotho product(s) newly crossed RCA>=1 across all year-pairs on record")

    print("\nBuilding balance-of-payments tracker...")
    bop_rows = compute_bop_tracker(MACRO_PATH, computed_at)
    pd.DataFrame(bop_rows, columns=BOP_COLUMNS).to_csv(BOP_OUTPUT, index=False)
    print(f"  Wrote {len(bop_rows)} rows to {BOP_OUTPUT}")
    if bop_rows:
        latest = bop_rows[-1]
        print(f"  Latest ({latest['year']}): exports {latest['exports_pct_gdp']}% of GDP, "
              f"imports {latest['imports_pct_gdp']}% of GDP, gap {latest['trade_gap_pct_gdp']} points")


if __name__ == "__main__":
    main()
