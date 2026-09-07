"""
Phase 6 — signals layer: anomaly detection across everything already
ingested, plus the news discovery feed.

Two genuinely different things live in this one script because they serve
the same purpose (surface what's worth a human's attention) even though
they're built differently:

1. ANOMALY DETECTION (macro, trade, ECI) -- a real statistical screen
   (see lib/anomaly.py) applied to every long-running numeric series this
   project ingests. Fully computable and tested against real data right
   now, since macro/trade/ECI all have several years of history.

2. DISCOVERY FEED (news) -- NOT a statistical computation, just a
   surfacing of already-tagged data: the most recent untagged articles
   from gdelt_general. Genuinely useful (an untagged article is something
   the rule-based tagger didn't have a category for -- exactly where an
   unnamed risk would first show up) but there is no history yet to run
   volume-spike detection against (Phase 3 has run only a handful of
   times). The RIGHT thing to do with too little history is surface raw
   candidates for a human to look at, not fabricate a baseline from one
   data point -- so that's what this does for now. Once enough daily runs
   accumulate, a real volume-anomaly check (a sudden spike in a
   particular tag's daily article count) becomes a natural extension of
   the exact same lib/anomaly.py function already built and tested here.

Like Phase 2's proxy-ECI computation, this reads already-ingested data and
fully overwrites its output on every run -- no external API call, so
nothing here needed mocking; every number quoted in PHASE6_SUMMARY.md was
checked against this project's actual, real, live data.
"""
import datetime
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from lib.anomaly import evaluate_latest_anomaly  # noqa: E402

MACRO_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "macro", "worldbank_indicator_log.csv")
TRADE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "trade", "comtrade_sacu_basket_log.csv")
ECI_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "proxy_eci_log.csv")
NEWS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "news", "general_signal_log.csv")

SIGNALS_OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "anomaly_signals_log.csv")
DISCOVERY_OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "discovery_feed_log.csv")

# A trade series is only evaluated if it accounts for at least this share
# of that reporter's own total average trade (in that flow direction) --
# a RELATIVE threshold, not an absolute dollar amount, so Lesotho's
# meaningful products aren't crowded out by South Africa's much larger
# economy. Checked against real data before picking this number: 0.5%
# keeps ~272 of 968 series (56 of them Lesotho's), a focused set rather
# than either "everything" (968, mostly noise) or "almost nothing".
TRADE_SIGNIFICANCE_SHARE_THRESHOLD = 0.005

DISCOVERY_FEED_MAX_ITEMS = 20


def utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def evaluate_macro(computed_at):
    if not os.path.exists(MACRO_PATH):
        print("  No macro data file found -- skipping macro anomaly evaluation.")
        return []
    df = pd.read_csv(MACRO_PATH)
    rows = []
    for (country_code, indicator_code), group in df.groupby(["country_code", "indicator_code"]):
        group = group.sort_values("year")
        periods = group["year"].tolist()
        values = group["value"].tolist()
        result = evaluate_latest_anomaly(periods, values)
        if result is None:
            continue
        indicator_name = group["indicator_name"].iloc[-1]
        rows.append({
            "computed_at": computed_at,
            "domain": "macro",
            "series_id": f"{country_code}:{indicator_code}",
            "series_label": f"{country_code} — {indicator_name}",
            **result,
        })
    return rows


def evaluate_trade(computed_at):
    if not os.path.exists(TRADE_PATH):
        print("  No trade data file found -- skipping trade anomaly evaluation.")
        return []
    df = pd.read_csv(TRADE_PATH)
    df["trade_value_usd"] = pd.to_numeric(df["trade_value_usd"], errors="coerce")
    df = df.dropna(subset=["trade_value_usd"])

    # Relative significance filter -- see module-level constant docstring.
    avg_by_series = df.groupby(["reporter", "cmd_code", "flow"])["trade_value_usd"].mean().reset_index()
    avg_by_series.columns = ["reporter", "cmd_code", "flow", "avg_value"]
    totals = avg_by_series.groupby(["reporter", "flow"])["avg_value"].transform("sum")
    avg_by_series["share"] = avg_by_series["avg_value"] / totals
    significant = avg_by_series[avg_by_series["share"] >= TRADE_SIGNIFICANCE_SHARE_THRESHOLD]
    significant_keys = set(zip(significant["reporter"], significant["cmd_code"], significant["flow"]))

    print(f"  Evaluating {len(significant_keys)} of {len(avg_by_series)} trade series "
          f"(>= {TRADE_SIGNIFICANCE_SHARE_THRESHOLD*100:.1f}% of that reporter's own trade).")

    rows = []
    for (reporter, cmd_code, flow), group in df.groupby(["reporter", "cmd_code", "flow"]):
        if (reporter, cmd_code, flow) not in significant_keys:
            continue
        group = group.sort_values("year")
        periods = group["year"].tolist()
        values = group["trade_value_usd"].tolist()
        result = evaluate_latest_anomaly(periods, values)
        if result is None:
            continue
        cmd_desc = group["cmd_desc"].iloc[-1]
        rows.append({
            "computed_at": computed_at,
            "domain": "trade",
            "series_id": f"{reporter}:{cmd_code}:{flow}",
            "series_label": f"{reporter} — {cmd_desc} ({flow})",
            **result,
        })
    return rows


def evaluate_eci(computed_at):
    if not os.path.exists(ECI_PATH):
        print("  No proxy-ECI data file found -- skipping ECI anomaly evaluation.")
        return []
    df = pd.read_csv(ECI_PATH)
    rows = []
    for metric_col, metric_label in [("sacu_rank", "SACU rank"), ("log_fitness_score", "log-Fitness")]:
        for country, group in df.groupby("country"):
            group = group.sort_values("year")
            periods = group["year"].tolist()
            values = group[metric_col].tolist()
            result = evaluate_latest_anomaly(periods, values)
            if result is None:
                continue
            rows.append({
                "computed_at": computed_at,
                "domain": "eci",
                "series_id": f"{country}:{metric_col}",
                "series_label": f"{country} — {metric_label}",
                **result,
            })
    return rows


def build_discovery_feed(computed_at):
    if not os.path.exists(NEWS_PATH):
        print("  No news data file found -- skipping discovery feed.")
        return []
    df = pd.read_csv(NEWS_PATH, keep_default_na=False)
    untagged = df[df["tags"] == ""].copy()
    if untagged.empty:
        return []
    untagged = untagged.sort_values("seendate", ascending=False).head(DISCOVERY_FEED_MAX_ITEMS)
    rows = []
    for _, row in untagged.iterrows():
        rows.append({
            "computed_at": computed_at,
            "title": row["title"],
            "url": row["url"],
            "seendate": row["seendate"],
            "domain_source": row["domain"],
            "query_bucket": row.get("query_bucket", ""),
        })
    return rows


def main():
    computed_at = utc_now_iso()

    print("Evaluating macro indicator series...")
    macro_rows = evaluate_macro(computed_at)
    print(f"  {len(macro_rows)} series had enough history to evaluate; "
          f"{sum(r['is_anomaly'] for r in macro_rows)} flagged.")

    print("Evaluating trade series...")
    trade_rows = evaluate_trade(computed_at)
    print(f"  {len(trade_rows)} series had enough history to evaluate; "
          f"{sum(r['is_anomaly'] for r in trade_rows)} flagged.")

    print("Evaluating proxy-ECI series...")
    eci_rows = evaluate_eci(computed_at)
    print(f"  {len(eci_rows)} series had enough history to evaluate; "
          f"{sum(r['is_anomaly'] for r in eci_rows)} flagged.")

    all_signal_rows = macro_rows + trade_rows + eci_rows
    os.makedirs(os.path.dirname(SIGNALS_OUTPUT_PATH), exist_ok=True)
    pd.DataFrame(all_signal_rows).to_csv(SIGNALS_OUTPUT_PATH, index=False)
    print(f"Wrote {len(all_signal_rows)} total evaluated series to {SIGNALS_OUTPUT_PATH}")

    flagged = [r for r in all_signal_rows if r["is_anomaly"]]
    if flagged:
        print(f"\n{len(flagged)} FLAGGED (worth a look, not proof of anything -- see module docstring):")
        for r in sorted(flagged, key=lambda r: -abs(r["z_score"] or 0)):
            z_display = f"z={r['z_score']}" if r["z_score"] is not None else "z=undefined (flat history broken)"
            print(f"  [{r['domain']}] {r['series_label']} ({r['latest_period']}): "
                  f"{r['latest_value']} (change {r['latest_change']:+}, {z_display})")

    print("\nBuilding news discovery feed...")
    discovery_rows = build_discovery_feed(computed_at)
    pd.DataFrame(discovery_rows).to_csv(DISCOVERY_OUTPUT_PATH, index=False)
    print(f"Wrote {len(discovery_rows)} discovery-feed item(s) to {DISCOVERY_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
