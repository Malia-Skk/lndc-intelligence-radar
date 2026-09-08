"""
Phase 11 — extended forecasting: more series projected forward, the
continuous complexity score (not just the discrete rank), and a
"required pace" gap analysis. Reuses lib/forecast.py exactly as Phase 7
built it (a proper shared library, safe to import) -- does NOT touch
compute_forecasts.py itself, matching the same "don't couple into a
working script" choice Phase 10 made with the RCA computation.

WHY DUAL-WINDOW FOR EVERY SERIES HERE, NOT JUST GDP GROWTH: checked
before writing any forecasting code, not assumed. Every single new
series considered here -- manufacturing value-added, FDI inflows,
unemployment, manufactured exports, the continuous complexity score, and
the BOP trade gap -- shows a SIGN FLIP between its full-history trend and
its most-recent-5-year trend, the same pattern Phase 7 found for GDP
growth alone. This isn't a one-off quirk of one series; it's a systemic
feature of this dataset, plausibly the same COVID-era structural break
rippling through nearly everything. Every forecast below uses both
windows for that reason, not by default policy alone.

REAL FINDING WORTH NAMING: the continuous complexity score
(log_fitness_score) shows the same sign flip -- full-history reads as
slight improvement, the recent 5 years read as decline. Lesotho's
discrete SACU rank has shown ZERO variance (always 4th) across all 6
years on record, which completely hides this more nuanced, and slightly
concerning, recent wobble in the underlying continuous score. This is
the entire reason this phase forecasts the continuous score at all,
not just the rank Phase 7 already covers.

REQUIRED PACE: for the two metrics with a genuinely well-defined,
continuous "target gap" (GDP growth, which already has stated targets in
Phase 7's forecast_log.csv; and the complexity score, given an IMPLICIT
numeric target here -- the current #1-ranked SACU country's own score,
since "#1 in SACU" is what the strategy actually asks for), this computes
the annual rate of change that would be needed from the latest actual
value to reach the target by the target year, and contrasts it against
the recent-5yr trend's actual rate of change. Deliberately NOT computed
for the SACU rank itself (an ordinal 1-5, where "pace" isn't a
well-defined continuous concept the way a rate of change is), nor for
metrics with no stated numeric target at all (manufacturing/FDI/
unemployment/manufactured-exports/HHI/trade-gap) -- reported without a
required-pace figure rather than inventing a target that was never set.
"""
import datetime
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from lib.forecast import fit_linear_trend, predict_with_interval  # noqa: E402

MACRO_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "macro", "worldbank_indicator_log.csv")
ECI_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "proxy_eci_log.csv")
DIVERSIFICATION_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "diversification_index_log.csv")
BOP_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "bop_tracker_log.csv")
EXISTING_FORECAST_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "forecast_log.csv")  # read-only

FORECAST_OUTPUT = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "extended_forecast_log.csv")
PACE_OUTPUT = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "required_pace_log.csv")

CONFIDENCE = 0.95
TARGET_YEARS = [2027, 2030]  # near and far checkpoints; no target_value unless one genuinely exists

FORECAST_COLUMNS = [
    "computed_at", "metric_id", "metric_label", "window", "n_historical_points",
    "historical_year_range", "latest_actual_year", "latest_actual_value", "target_year",
    "target_value", "projected_value", "projected_lower", "projected_upper",
    "confidence_level", "years_beyond_observed_data", "status_vs_target", "notes",
]
PACE_COLUMNS = [
    "computed_at", "metric_id", "metric_label", "latest_actual_year", "latest_actual_value",
    "target_year", "target_value", "required_annual_change", "recent_trend_annual_change",
    "pace_gap", "interpretation",
]

# metric_id -> (indicator_code, label)
MACRO_SERIES = {
    "manufacturing_value_added_pct_gdp": ("NV.IND.MANF.ZS", "Manufacturing, value added (% of GDP)"),
    "fdi_net_inflows_pct_gdp": ("BX.KLT.DINV.WD.GD.ZS", "FDI, net inflows (% of GDP)"),
    "unemployment_pct": ("SL.UEM.TOTL.ZS", "Unemployment, total (% of labour force)"),
    "manufactured_exports_pct": ("TX.VAL.MANF.ZS.UN", "Manufactured exports (% of merchandise exports)"),
}


def utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def classify_status(lower, upper, target):
    if target is None:
        return "no target set"
    if lower >= target:
        return "ahead of target"
    if upper < target:
        return "behind target"
    return "within uncertainty range of target"


def dual_window_forecast(periods, values, metric_id, metric_label, computed_at, target_value=None, extra_note=""):
    """Fits both a full-history and a recent-5-year window and forecasts
    to each of TARGET_YEARS, mirroring Phase 7's GDP-growth treatment."""
    rows = []
    windows = [("full_history", periods, values)]
    if len(periods) > 5:
        windows.append(("recent_5yr", periods[-5:], values[-5:]))

    for window_label, w_periods, w_values in windows:
        fit = fit_linear_trend(w_periods, w_values)
        if fit is None:
            print(f"    {metric_label} ({window_label}): only {len(w_periods)} point(s) -- need >=4, skipping.")
            continue
        for target_year in TARGET_YEARS:
            point, lower, upper, years_beyond = predict_with_interval(fit, target_year, confidence=CONFIDENCE)
            rows.append({
                "computed_at": computed_at,
                "metric_id": metric_id,
                "metric_label": metric_label,
                "window": window_label,
                "n_historical_points": fit["n"],
                "historical_year_range": f"{int(fit['x_min'])}-{int(fit['x_max'])}",
                "latest_actual_year": int(fit["x_max"]),
                "latest_actual_value": round(w_values[w_periods.index(fit["x_max"])], 4) if fit["x_max"] in w_periods else None,
                "target_year": target_year,
                "target_value": target_value,
                "projected_value": round(point, 4),
                "projected_lower": round(lower, 4),
                "projected_upper": round(upper, 4),
                "confidence_level": CONFIDENCE,
                "years_beyond_observed_data": round(years_beyond, 1),
                "status_vs_target": classify_status(lower, upper, target_value),
                "notes": extra_note,
            })
    return rows


def forecast_macro_series(computed_at):
    if not os.path.exists(MACRO_PATH):
        print("  No macro data file -- skipping macro series forecasts.")
        return []
    df = pd.read_csv(MACRO_PATH)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    lso = df[df["country_code"] == "LSO"]

    all_rows = []
    for metric_id, (code, label) in MACRO_SERIES.items():
        sub = lso[lso["indicator_code"] == code].sort_values("year").dropna(subset=["value"])
        if len(sub) < 4:
            print(f"  {label}: only {len(sub)} point(s), skipping.")
            continue
        periods, values = sub["year"].tolist(), sub["value"].tolist()
        note = ("No explicit numeric target is stated in the strategy for this indicator -- "
                "forecast shown for monitoring purposes, not compared against a target.")
        rows = dual_window_forecast(periods, values, metric_id, label, computed_at, target_value=None, extra_note=note)
        all_rows += rows
        print(f"  {label}: {len(rows)} forecast rows")
    return all_rows


def forecast_complexity_score(computed_at):
    if not os.path.exists(ECI_PATH):
        print("  No proxy-ECI data file -- skipping complexity score forecast.")
        return [], None
    df = pd.read_csv(ECI_PATH)
    lso = df[df["country"] == "Lesotho"].sort_values("year")
    periods, values = lso["year"].tolist(), lso["log_fitness_score"].tolist()

    latest_year = int(lso["year"].max())
    rank1_score = df[(df["year"] == latest_year) & (df["sacu_rank"] == 1)]
    implicit_target = float(rank1_score["log_fitness_score"].iloc[0]) if not rank1_score.empty else None
    rank1_country = rank1_score["country"].iloc[0] if not rank1_score.empty else "unknown"

    note = (
        f"IMPLICIT target, not stated directly in the strategy: the current #1-ranked SACU "
        f"country's own log-Fitness score ({rank1_country}, {latest_year}), since '#1 in SACU' is "
        f"what the strategy's actual target means in continuous terms. This is a derived proxy "
        f"target, not a value the strategy states directly -- treat accordingly. The discrete SACU "
        f"rank (see forecast_log.csv) has shown ZERO variance across all years on record, which "
        f"hides the more nuanced movement in this continuous score -- that's the reason this "
        f"forecast exists at all."
    )
    rows = dual_window_forecast(periods, values, "complexity_log_fitness_score",
                                 "Lesotho log-Fitness score (continuous complexity, not the discrete rank)",
                                 computed_at, target_value=implicit_target, extra_note=note)
    return rows, implicit_target


def forecast_diversification_and_bop(computed_at):
    all_rows = []
    if os.path.exists(DIVERSIFICATION_PATH):
        div = pd.read_csv(DIVERSIFICATION_PATH)
        lso = div[div["country"] == "Lesotho"].sort_values("year")
        if len(lso) >= 4:
            note = ("No explicit numeric HHI target is stated in the strategy -- forecast shown for "
                     "monitoring purposes. Unlike most other series in this phase, both windows here "
                     "agree in DIRECTION (both negative, i.e. continued diversification) -- they differ "
                     "in MAGNITUDE (recent trend diversifying faster than the full-history average), "
                     "which is still worth showing both for.")
            rows = dual_window_forecast(lso["year"].tolist(), lso["hhi"].tolist(), "export_diversification_hhi",
                                         "Lesotho export-basket concentration (HHI, lower = more diversified)",
                                         computed_at, target_value=None, extra_note=note)
            all_rows += rows
            print(f"  Diversification index (HHI): {len(rows)} forecast rows")

    if os.path.exists(BOP_PATH):
        bop = pd.read_csv(BOP_PATH).sort_values("year")
        if len(bop) >= 4:
            note = ("No explicit numeric trade-gap target is stated in the strategy -- forecast shown "
                     "for monitoring purposes. The two windows DISAGREE IN DIRECTION here: the full-"
                     "history trend reads as a very slowly narrowing gap, while the recent 5 years read "
                     "as a WIDENING gap -- worth watching given this is the strategy's own founding "
                     "diagnosis (Ch.1.3).")
            rows = dual_window_forecast(bop["year"].tolist(), bop["trade_gap_pct_gdp"].tolist(), "bop_trade_gap_pct_gdp",
                                         "Balance-of-payments trade gap (imports % GDP minus exports % GDP)",
                                         computed_at, target_value=None, extra_note=note)
            all_rows += rows
            print(f"  BOP trade gap: {len(rows)} forecast rows")
    return all_rows


def compute_required_pace(computed_at, complexity_target):
    """Required annual pace to close the gap to a stated (or implicit)
    target, contrasted against the recent-5yr trend's actual pace.
    Only computed where a real numeric target exists and the concept of
    'pace' (a continuous rate of change) is well-defined -- see module
    docstring for what's deliberately excluded and why."""
    rows = []

    # GDP growth: read Phase 7's own forecast_log.csv, read-only -- does
    # not modify or depend on internals of compute_forecasts.py itself.
    if os.path.exists(EXISTING_FORECAST_PATH):
        existing = pd.read_csv(EXISTING_FORECAST_PATH)
        gdp_recent = existing[(existing["metric_id"] == "gdp_per_capita_growth") & (existing["window"] == "recent_5yr")]
        if not gdp_recent.empty:
            row = gdp_recent.sort_values("target_year").iloc[-1]  # farthest target year available
            latest_year, latest_value = row["latest_actual_year"], row["latest_actual_value"]
            target_year, target_value = row["target_year"], row["target_value"]
            years_remaining = target_year - latest_year
            if years_remaining > 0:
                required_pace = (target_value - latest_value) / years_remaining
                recent_pace = (row["projected_value"] - latest_value) / (target_year - latest_year)
                gap = required_pace - recent_pace
                interpretation = (
                    f"Current recent-5yr trend implies ~{recent_pace:.2f} pts/yr average change; hitting the "
                    f"{target_value}% target by {int(target_year)} needs ~{required_pace:.2f} pts/yr -- "
                    + (f"a further {gap:.2f} pts/yr of acceleration beyond the current trend." if gap > 0
                       else "the current trend already implies faster improvement than strictly required.")
                )
                rows.append({
                    "computed_at": computed_at, "metric_id": "gdp_per_capita_growth",
                    "metric_label": "Lesotho GDP per capita growth (annual %)",
                    "latest_actual_year": int(latest_year), "latest_actual_value": round(latest_value, 4),
                    "target_year": int(target_year), "target_value": target_value,
                    "required_annual_change": round(required_pace, 4),
                    "recent_trend_annual_change": round(recent_pace, 4),
                    "pace_gap": round(gap, 4), "interpretation": interpretation,
                })

    # Complexity score: uses the implicit rank-1 target computed above.
    if os.path.exists(ECI_PATH) and complexity_target is not None:
        eci = pd.read_csv(ECI_PATH)
        lso = eci[eci["country"] == "Lesotho"].sort_values("year")
        latest_year = int(lso["year"].max())
        latest_value = float(lso[lso["year"] == latest_year]["log_fitness_score"].iloc[0])
        target_year = max(TARGET_YEARS)
        years_remaining = target_year - latest_year
        recent = lso.tail(5)
        fit = fit_linear_trend(recent["year"].tolist(), recent["log_fitness_score"].tolist())
        if fit is not None and years_remaining > 0:
            required_pace = (complexity_target - latest_value) / years_remaining
            recent_pace = fit["slope"]
            gap = required_pace - recent_pace
            interpretation = (
                f"Current recent-5yr trend implies ~{recent_pace:.3f} pts/yr; closing the gap to the current "
                f"#1-ranked country's score by {target_year} needs ~{required_pace:.3f} pts/yr -- "
                + (f"a further {gap:.3f} pts/yr beyond the current trend, which is currently moving the WRONG "
                   f"direction relative to this goal." if recent_pace < 0 else
                   (f"a further {gap:.3f} pts/yr of acceleration." if gap > 0 else
                    "the current trend already implies faster convergence than strictly required."))
            )
            rows.append({
                "computed_at": computed_at, "metric_id": "complexity_log_fitness_score",
                "metric_label": "Lesotho log-Fitness score (continuous complexity)",
                "latest_actual_year": latest_year, "latest_actual_value": round(latest_value, 4),
                "target_year": target_year, "target_value": round(complexity_target, 4),
                "required_annual_change": round(required_pace, 4),
                "recent_trend_annual_change": round(recent_pace, 4),
                "pace_gap": round(gap, 4), "interpretation": interpretation,
            })
    return rows


def main():
    computed_at = utc_now_iso()

    print("Forecasting additional macro series (dual-window)...")
    macro_rows = forecast_macro_series(computed_at)

    print("\nForecasting continuous complexity score (dual-window)...")
    complexity_rows, complexity_target = forecast_complexity_score(computed_at)
    print(f"  {len(complexity_rows)} forecast rows; implicit target = {complexity_target}")

    print("\nForecasting diversification index and BOP trade gap (dual-window)...")
    trade_rows = forecast_diversification_and_bop(computed_at)

    all_forecast_rows = macro_rows + complexity_rows + trade_rows
    os.makedirs(os.path.dirname(FORECAST_OUTPUT), exist_ok=True)
    pd.DataFrame(all_forecast_rows, columns=FORECAST_COLUMNS).to_csv(FORECAST_OUTPUT, index=False)
    print(f"\nWrote {len(all_forecast_rows)} extended-forecast rows to {FORECAST_OUTPUT}")

    print("\nComputing required-pace gap analysis...")
    pace_rows = compute_required_pace(computed_at, complexity_target)
    pd.DataFrame(pace_rows, columns=PACE_COLUMNS).to_csv(PACE_OUTPUT, index=False)
    print(f"Wrote {len(pace_rows)} required-pace rows to {PACE_OUTPUT}")
    for r in pace_rows:
        print(f"  [{r['metric_id']}] {r['interpretation']}")


if __name__ == "__main__":
    main()
