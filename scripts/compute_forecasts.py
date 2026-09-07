"""
Phase 7 — forecasting: project GDP growth and proxy-ECI/SACU rank forward
and compare against the strategy's own stated target trajectory.

This is what makes "predictive analytics" honest rather than aspirational
for this system. Everything through Phase 6 monitors or ranks; nothing
projected forward. This does, using the OLS trend + prediction-interval
method in lib/forecast.py (see that module's docstring for the full
methodological reasoning).

FISCAL-YEAR APPROXIMATION, STATED EXPLICITLY (not hidden in a comment):
The strategy's targets are stated by fiscal year (Lesotho's runs
April-March; "Year 5" = FY2030/31). World Bank data is calendar-year
indexed. This maps Year N to calendar year (2025 + N) as an approximation
-- Year 1 = FY2026/27 -> 2026, ..., Year 5 = FY2030/31 -> 2030. This is a
real approximation, not an exact mapping, and every forecast row below
says so rather than presenting a false-precision year match.

WHAT THIS DOES NOT DO: forecast jobs or industrialist counts as a trend.
Only one pipeline snapshot exists (Phase 4) -- there is no history to fit
a trend to. Fabricating a trend from a single data point would be exactly
the kind of overclaiming this project has avoided everywhere else,
starting with the assumption-tagged stage-conversion weights in Phase 4
itself. Jobs/industrialists get a plain status-vs-target comparison
instead, clearly labelled as such, not a projection.
"""
import datetime
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from lib.forecast import fit_linear_trend, predict_with_interval  # noqa: E402

MACRO_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "macro", "worldbank_indicator_log.csv")
ECI_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "proxy_eci_log.csv")
PIPELINE_ARCHIVE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "pipeline", "pipeline_snapshot_archive.csv")

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "forecast_log.csv")
STATUS_OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "target_status_log.csv")

CONFIDENCE = 0.95

# Strategy's own stepped targets (Ch.2 Winning Aspiration), calendar-year-
# approximated as described in the module docstring.
GDP_GROWTH_TARGET_TRAJECTORY = {2026: 2.0, 2027: 3.0, 2028: 5.0, 2029: 6.0, 2030: 7.0}
FINAL_TARGET_YEAR = 2030  # approximates FY2030/31, "Year 5"

# Only an endpoint target is stated for SACU ranking ("#1 in SACU" by
# Year 5) -- no intermediate stepped values are given in the strategy the
# way GDP growth has, so only the endpoint is used here.
SACU_RANK_TARGET = {FINAL_TARGET_YEAR: 1}


def utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def classify_on_track(lower, upper, target, higher_is_better):
    if higher_is_better:
        if lower >= target:
            return "ahead of target"
        if upper < target:
            return "behind target"
        return "within uncertainty range of target"
    else:
        if upper <= target:
            return "ahead of target"
        if lower > target:
            return "behind target"
        return "within uncertainty range of target"


def forecast_series(periods, values, target_years, higher_is_better, metric_id, metric_label,
                     window_label="full_history", notes=""):
    fit = fit_linear_trend(periods, values)
    rows = []
    if fit is None:
        print(f"  {metric_label} ({window_label}): only {len(periods)} historical point(s) -- "
              f"need at least 4 for a defensible trend. Skipping.")
        return rows

    for target_year, target_value in sorted(target_years.items()):
        result = predict_with_interval(fit, target_year, confidence=CONFIDENCE)
        point, lower, upper, years_beyond = result
        status = classify_on_track(lower, upper, target_value, higher_is_better)
        rows.append({
            "computed_at": None,  # filled in by caller
            "metric_id": metric_id,
            "metric_label": metric_label,
            "window": window_label,
            "n_historical_points": fit["n"],
            "historical_year_range": f"{int(fit['x_min'])}-{int(fit['x_max'])}",
            "latest_actual_year": int(fit["x_max"]),
            "latest_actual_value": round(values[periods.index(fit["x_max"])], 4) if fit["x_max"] in periods else None,
            "target_year": target_year,
            "target_value": target_value,
            "projected_value": round(point, 4),
            "projected_lower": round(lower, 4),
            "projected_upper": round(upper, 4),
            "confidence_level": CONFIDENCE,
            "years_beyond_observed_data": round(years_beyond, 1),
            "status_vs_target": status,
            "notes": notes,
        })
    return rows


def forecast_gdp_growth(computed_at):
    if not os.path.exists(MACRO_PATH):
        print("  No macro data file found -- skipping GDP growth forecast.")
        return []
    df = pd.read_csv(MACRO_PATH)
    lso = df[(df.country_code == "LSO") & (df.indicator_code == "NY.GDP.PCAP.KD.ZG")].sort_values("year")
    periods = lso["year"].tolist()
    values = lso["value"].tolist()

    # TWO windows, deliberately, not one -- checked against the actual data
    # before deciding this: the full 16-year history's slope is -0.24%/yr,
    # while the last 5 years alone show +0.23%/yr -- almost a sign flip.
    # The full-history fit is dominated by the 2017-2020 structural decline
    # (including the COVID shock); it is NOT a good read of current
    # dynamics on its own. Showing only the full-history number would be
    # technically-not-wrong but genuinely misleading as "the forecast".
    # Showing both, clearly labelled, is the honest choice -- the same
    # instinct that kept raw fitness AND log-fitness both visible in
    # Phase 2, rather than picking one and hiding the other.
    full_notes = (
        "LONG-RUN trend across all 16 available years (2010-2025). Includes the 2017-2020 "
        "structural decline and the 2020 COVID shock (-9.24%) -- not excluded as an 'outlier', "
        "since doing so would overclaim confidence the underlying volatility doesn't support. "
        "This trend is dominated by that decline: the full-history slope is NEGATIVE "
        "(~-0.24%/year) even though the most recent 5 years alone show a POSITIVE slope "
        "(~+0.23%/year, see the recent_5yr window). Read this as 'the whole volatile picture', "
        "not 'where things are heading now' -- use the recent_5yr window for that instead, "
        "with the caveat that a 5-point window has much less statistical power (dof=3)."
    )
    recent_notes = (
        "SHORT-WINDOW trend using only the most recent 5 years (2021-2025), the post-COVID "
        "recovery period -- arguably more representative of current dynamics than the full-history "
        "window, but built on only 5 points (dof=3), so its own uncertainty band is wide and "
        "sensitive to any single year's noise. Shown alongside, not instead of, the full_history "
        "window -- neither is 'the' answer on its own."
    )

    rows = forecast_series(
        periods, values, GDP_GROWTH_TARGET_TRAJECTORY, higher_is_better=True,
        metric_id="gdp_per_capita_growth", metric_label="Lesotho GDP per capita growth (annual %)",
        window_label="full_history", notes=full_notes,
    )
    recent_periods, recent_values = periods[-5:], values[-5:]
    rows += forecast_series(
        recent_periods, recent_values, GDP_GROWTH_TARGET_TRAJECTORY, higher_is_better=True,
        metric_id="gdp_per_capita_growth", metric_label="Lesotho GDP per capita growth (annual %)",
        window_label="recent_5yr", notes=recent_notes,
    )
    for r in rows:
        r["computed_at"] = computed_at
    return rows


def forecast_sacu_rank(computed_at):
    if not os.path.exists(ECI_PATH):
        print("  No proxy-ECI data file found -- skipping SACU rank forecast.")
        return []
    df = pd.read_csv(ECI_PATH)
    lso = df[df.country == "Lesotho"].sort_values("year")
    periods = lso["year"].tolist()
    values = lso["sacu_rank"].tolist()

    if len(set(values)) == 1:
        note_extra = (
            f"Rank has been exactly {values[0]} in every one of the {len(values)} years of "
            "available history -- zero variance. A trend fit on a constant series projects "
            "'stays constant', which is the honest reading: absent a structural change, this "
            "extrapolates to continuing at the same rank, not improving toward it. IMPORTANT: "
            "this also means the model produces a mathematically zero-width prediction interval "
            "(no historical deviation to build a band from) -- read that as 'the model has no "
            "basis in past data to expect a change', NOT as 'guaranteed to never change'. A model "
            "cannot anticipate a future structural break it has never seen; zero statistical "
            "uncertainty is not the same claim as zero real-world uncertainty."
        )
    else:
        note_extra = ""

    notes = ("SACU rank is an integer 1-5; treating it as continuous for trend-fitting purposes "
              "is a simplification worth remembering when reading the projected value. " + note_extra)
    rows = forecast_series(
        periods, values, SACU_RANK_TARGET, higher_is_better=False,
        metric_id="sacu_rank", metric_label="Lesotho SACU-regional complexity rank (1=best)",
        notes=notes,
    )
    for r in rows:
        r["computed_at"] = computed_at
    return rows


def build_target_status_snapshot(computed_at):
    """Plain status-vs-target comparisons for metrics with too little
    history to forecast as a trend -- NOT a projection, explicitly."""
    rows = []
    if os.path.exists(PIPELINE_ARCHIVE_PATH):
        archive = pd.read_csv(PIPELINE_ARCHIVE_PATH)
        latest = archive.sort_values("snapshot_date").iloc[-1]
        rows.append({
            "computed_at": computed_at,
            "metric_id": "confirmed_new_industrialists",
            "metric_label": "Confirmed new Basotho industrialists",
            "as_of_date": latest["snapshot_date"],
            "current_value": int(latest["confirmed_new_industrialists_count"]),
            "year1_target": 0,
            "final_target": 100,
            "final_target_year": "FY2030/31",
            "note": (
                "Status comparison only, NOT a trend forecast -- only one pipeline snapshot "
                "exists (no history to fit a trend to). Currently matches the Year 1 target of 0 "
                "exactly. The strategy document's own year-by-year trajectory graphic (0/5/15/35/45) "
                "does not appear to sum to its stated 100 endpoint by Year 5 -- flagged here as a "
                "source-document inconsistency rather than silently picking one figure as ground truth."
            ),
        })
        rows.append({
            "computed_at": computed_at,
            "metric_id": "industrialist_candidates_in_pipeline",
            "metric_label": "DI-origin pipeline leads clearing the >=100-employee candidate bar",
            "as_of_date": latest["snapshot_date"],
            "current_value": int(latest["di_industrialist_candidates_count"]),
            "year1_target": None,
            "final_target": None,
            "final_target_year": None,
            "note": "Pipeline potential, not a confirmed count or a target -- see Phase 4 for the distinction.",
        })
    return rows


def main():
    computed_at = utc_now_iso()

    print("Forecasting GDP per capita growth...")
    gdp_rows = forecast_gdp_growth(computed_at)

    print("Forecasting SACU-regional complexity rank...")
    rank_rows = forecast_sacu_rank(computed_at)

    all_forecast_rows = gdp_rows + rank_rows
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    pd.DataFrame(all_forecast_rows).to_csv(OUTPUT_PATH, index=False)
    print(f"\nWrote {len(all_forecast_rows)} forecast row(s) to {OUTPUT_PATH}")

    for r in all_forecast_rows:
        print(f"  [{r['metric_id']}] {r['target_year']}: projected {r['projected_value']} "
              f"(90% band via t-dist: {r['projected_lower']} to {r['projected_upper']}), "
              f"target={r['target_value']}, {r['status_vs_target']} "
              f"({r['years_beyond_observed_data']} yrs beyond observed data)")

    print("\nBuilding target-status snapshot (jobs/industrialists -- not forecastable yet)...")
    status_rows = build_target_status_snapshot(computed_at)
    pd.DataFrame(status_rows).to_csv(STATUS_OUTPUT_PATH, index=False)
    print(f"Wrote {len(status_rows)} status row(s) to {STATUS_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
