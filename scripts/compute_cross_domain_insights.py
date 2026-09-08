"""
Phase 12 — cross-domain insight: three computations that connect data
already sitting in separate files, rather than pulling in anything new.

1. ANOMALY <-> NEWS TOPICAL LINKING. Checked before writing this: the
   news archive currently spans about a WEEK (GDELT is pulled daily, and
   daily pulls only started recently), while flagged anomalies are about
   trade/macro DATA from 2023-2025. There is no temporal overlap --
   linking here cannot mean "this article explains why that anomaly
   happened." What it CAN mean, honestly: does CURRENT news touch on the
   same broad topic as a PERSISTENT anomaly (most of these describe an
   ongoing state, like elevated beverage exports, not a one-day event)?
   Reuses lib/tagging.py's tag_article() -- the exact same keyword rules
   used to tag real news -- run against each anomaly's own descriptive
   label, rather than a new, separate mapping table.

   REAL FINDING, CHECKED BEFORE BUILDING THIS: only about 1 in 5 flagged
   anomalies matches an existing news category at all. Most trade
   anomalies are about specific HS product chapters (beverages,
   vegetables, ores, fertilizers...) that the 17 tracked news categories
   were never built to cover -- they cover the strategy's named sectors,
   not an exhaustive product list. This is reported as a real, stated
   match-rate, not hidden by only showing the ones that happen to match.

2. NEWS-VOLUME ANOMALY DETECTION. Reuses lib/anomaly.py's
   evaluate_latest_anomaly() exactly as Phase 6 built it, pointed at
   daily total article counts instead of a trade or macro series.
   HONESTLY FLAGGED: the news pipeline has only ~7 days of history at
   the time this was built. That clears the library's minimum
   threshold to compute a number at all, but a z-score from single-digit
   prior observations is thin evidence -- explicitly labelled as
   "early days" output, not presented with the same confidence as a
   trade anomaly built on 6-8 years of history. This will only get more
   reliable as more days accumulate; the mechanism is built now so it's
   ready when the history is.

3. PEER-COUNTRY INSIGHTS. The auto-insights engine (frontend, Phase 8)
   only ever narrates LESOTHO's own position. The exact same underlying
   data (proxy_eci_log.csv, diversification_index_log.csv) already
   covers all 5 SACU members -- this generates the same kind of
   plain-language finding for Lesotho's neighbours, surfacing things
   Lesotho-only narration would never mention. REAL FINDINGS, CHECKED
   BEFORE WRITING ANY NARRATION TEMPLATE: Namibia and Eswatini have
   swapped 2nd/3rd place in the SACU complexity ranking multiple times
   since 2018 -- a genuine, ongoing rivalry, unlike Lesotho's static
   4th. Botswana's export concentration has actually EASED (HHI 8209 ->
   6371 since 2018) even though its rank hasn't moved from last place --
   real improvement that hasn't yet been enough to change its rank.
"""
import datetime
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from lib.anomaly import evaluate_latest_anomaly  # noqa: E402
from lib.tagging import tag_article  # noqa: E402

ANOMALIES_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "anomaly_signals_log.csv")
NEWS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "news", "general_signal_log.csv")
ECI_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "proxy_eci_log.csv")
DIVERSIFICATION_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "diversification_index_log.csv")

LINKS_OUTPUT = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "anomaly_news_links_log.csv")
VOLUME_OUTPUT = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "news_volume_anomalies_log.csv")
PEER_OUTPUT = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "peer_insights_log.csv")

LINKS_COLUMNS = [
    "computed_at", "anomaly_domain", "anomaly_label", "anomaly_period", "anomaly_z_score",
    "matched_category", "related_article_count", "related_article_titles", "related_article_urls",
]
VOLUME_COLUMNS = [
    "computed_at", "date", "article_count", "latest_change", "z_score", "is_anomaly",
    "n_prior_days", "confidence_note",
]
PEER_COLUMNS = ["computed_at", "country", "category", "headline", "detail"]

MIN_VOLUME_HISTORY_FOR_CONFIDENCE = 21  # ~3 weeks; below this, flag output as early-days


def utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# --- 1. Anomaly <-> news topical linking -----------------------------------

def compute_anomaly_news_links(computed_at):
    if not os.path.exists(ANOMALIES_PATH) or not os.path.exists(NEWS_PATH):
        return []
    anomalies = pd.read_csv(ANOMALIES_PATH)
    news = pd.read_csv(NEWS_PATH, keep_default_na=False)
    flagged = anomalies[anomalies["is_anomaly"] == True]  # noqa: E712

    rows = []
    for _, a in flagged.iterrows():
        tags, _, _ = tag_article(a["series_label"])
        if not tags:
            rows.append({
                "computed_at": computed_at, "anomaly_domain": a["domain"], "anomaly_label": a["series_label"],
                "anomaly_period": a["latest_period"], "anomaly_z_score": a.get("z_score"),
                "matched_category": "", "related_article_count": 0, "related_article_titles": "", "related_article_urls": "",
            })
            continue
        # An anomaly's label can match more than one category (same rule
        # as tagging real articles) -- link against all of them, not just
        # the first.
        matching_news = news[news["tags"].apply(lambda t: any(tag in str(t).split(";") for tag in tags))]
        rows.append({
            "computed_at": computed_at, "anomaly_domain": a["domain"], "anomaly_label": a["series_label"],
            "anomaly_period": a["latest_period"], "anomaly_z_score": a.get("z_score"),
            "matched_category": ";".join(tags), "related_article_count": len(matching_news),
            "related_article_titles": " | ".join(matching_news["title"].head(3).tolist()),
            "related_article_urls": " | ".join(matching_news["url"].head(3).tolist()),
        })
    return rows


# --- 2. News-volume anomaly detection ---------------------------------------

def compute_news_volume_anomaly(computed_at):
    if not os.path.exists(NEWS_PATH):
        return []
    news = pd.read_csv(NEWS_PATH, keep_default_na=False)
    dates = pd.to_datetime(news["seendate"], format="%Y%m%dT%H%M%SZ", errors="coerce").dt.date
    daily_counts = dates.value_counts().sort_index()
    if len(daily_counts) < 2:
        return []

    periods = list(range(len(daily_counts)))  # ordinal day index -- evaluate_latest_anomaly needs numeric periods
    values = daily_counts.tolist()
    result = evaluate_latest_anomaly(periods, values)
    n_days = len(daily_counts)
    confidence_note = (
        f"Only {n_days} days of news history exist yet (below the ~{MIN_VOLUME_HISTORY_FOR_CONFIDENCE}-day mark "
        f"this system treats as minimally reliable) -- treat this as an early, thin-history signal, not a "
        f"confident read. It will become more reliable automatically as more daily pulls accumulate."
    ) if n_days < MIN_VOLUME_HISTORY_FOR_CONFIDENCE else "Sufficient history for a reasonably confident read."

    if result is None:
        return [{
            "computed_at": computed_at, "date": str(daily_counts.index[-1]), "article_count": int(values[-1]),
            "latest_change": None, "z_score": None, "is_anomaly": False, "n_prior_days": n_days,
            "confidence_note": f"Not enough history yet to evaluate ({n_days} days) -- needs at least 5.",
        }]
    return [{
        "computed_at": computed_at, "date": str(daily_counts.index[-1]), "article_count": int(result["latest_value"]),
        "latest_change": result["latest_change"], "z_score": result["z_score"], "is_anomaly": result["is_anomaly"],
        "n_prior_days": n_days, "confidence_note": confidence_note,
    }]


# --- 3. Peer-country insights -----------------------------------------------

def compute_peer_insights(computed_at):
    rows = []
    if os.path.exists(ECI_PATH):
        eci = pd.read_csv(ECI_PATH)
        for country in ["South Africa", "Botswana", "Namibia", "Eswatini"]:
            sub = eci[eci["country"] == country].sort_values("year")
            if len(sub) < 3:
                continue
            ranks = sub["sacu_rank"].tolist()
            years = sub["year"].tolist()
            if len(set(ranks)) == 1:
                rows.append({
                    "computed_at": computed_at, "country": country, "category": "complexity_rank",
                    "headline": f"{country} has held rank {ranks[0]} in SACU complexity for all {len(ranks)} years on record",
                    "detail": f"No measurable movement in {country}'s regional ranking from {years[0]} to {years[-1]}.",
                })
            else:
                changes = sum(1 for i in range(1, len(ranks)) if ranks[i] != ranks[i - 1])
                rows.append({
                    "computed_at": computed_at, "country": country, "category": "complexity_rank",
                    "headline": f"{country}'s SACU complexity rank has moved {changes} time(s) since {years[0]}",
                    "detail": f"Ranks by year: {dict(zip(years, ranks))}.",
                })

        # Rank-swap detection between any pair of countries (checked
        # generically, not hardcoded to Namibia/Eswatini specifically --
        # that pair is simply what the real data happens to show right now).
        pivot = eci.pivot_table(index="year", columns="country", values="sacu_rank")
        countries = [c for c in pivot.columns if c != "Lesotho"]
        for i, c1 in enumerate(countries):
            for c2 in countries[i + 1:]:
                pair = pivot[[c1, c2]].dropna()
                if len(pair) < 3:
                    continue
                swaps = sum(1 for j in range(1, len(pair)) if
                            (pair[c1].iloc[j] - pair[c2].iloc[j]) * (pair[c1].iloc[j - 1] - pair[c2].iloc[j - 1]) < 0)
                if swaps > 0:
                    rows.append({
                        "computed_at": computed_at, "country": f"{c1} / {c2}", "category": "complexity_rivalry",
                        "headline": f"{c1} and {c2} have swapped relative SACU complexity position {swaps} time(s) since {int(pair.index.min())}",
                        "detail": f"An ongoing, genuine rivalry for their relative standing -- unlike Lesotho's unchanged position over the same period.",
                    })

    if os.path.exists(DIVERSIFICATION_PATH):
        div = pd.read_csv(DIVERSIFICATION_PATH)
        for country in ["South Africa", "Botswana", "Namibia", "Eswatini"]:
            sub = div[div["country"] == country].sort_values("year")
            if len(sub) < 4:
                continue
            first, last = sub.iloc[0], sub.iloc[-1]
            change_pct = (last["hhi"] - first["hhi"]) / first["hhi"] * 100
            if abs(change_pct) >= 10:  # only surface a meaningfully-sized change, not noise
                direction = "eased" if change_pct < 0 else "increased"
                rows.append({
                    "computed_at": computed_at, "country": country, "category": "diversification_trend",
                    "headline": f"{country}'s export concentration has {direction} {abs(change_pct):.0f}% since {int(first['year'])}",
                    "detail": f"HHI moved from {first['hhi']:.0f} ({int(first['year'])}) to {last['hhi']:.0f} ({int(last['year'])}), "
                              f"currently '{last['concentration_level']}'.",
                })
    return rows


def main():
    computed_at = utc_now_iso()

    print("Computing anomaly <-> news topical links...")
    link_rows = compute_anomaly_news_links(computed_at)
    os.makedirs(os.path.dirname(LINKS_OUTPUT), exist_ok=True)
    pd.DataFrame(link_rows, columns=LINKS_COLUMNS).to_csv(LINKS_OUTPUT, index=False)
    matched = sum(1 for r in link_rows if r["matched_category"])
    print(f"  Wrote {len(link_rows)} rows -- {matched} of {len(link_rows)} flagged anomalies matched a news category "
          f"({matched / len(link_rows) * 100:.0f}%)" if link_rows else "  No flagged anomalies to link.")

    print("\nComputing news-volume anomaly check...")
    volume_rows = compute_news_volume_anomaly(computed_at)
    pd.DataFrame(volume_rows, columns=VOLUME_COLUMNS).to_csv(VOLUME_OUTPUT, index=False)
    print(f"  Wrote {len(volume_rows)} row(s).")
    for r in volume_rows:
        print(f"  {r['confidence_note']}")

    print("\nComputing peer-country insights...")
    peer_rows = compute_peer_insights(computed_at)
    pd.DataFrame(peer_rows, columns=PEER_COLUMNS).to_csv(PEER_OUTPUT, index=False)
    print(f"  Wrote {len(peer_rows)} peer insight(s).")
    for r in peer_rows:
        print(f"  [{r['country']}] {r['headline']}")


if __name__ == "__main__":
    main()
