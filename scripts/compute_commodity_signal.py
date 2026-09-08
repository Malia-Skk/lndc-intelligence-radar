"""
Phase 13 — commodity signal: an honest substitute for wool/mohair and
diamond PRICE data, which does not exist as a public, free, or even paid
API in any form -- not a coverage gap to close with more searching, but
a structural fact about how these markets work. Wool and mohair are sold
at regional auctions (per-lot prices set at auction, reported by the
auction houses themselves, not published as a continuous daily/monthly
series); diamonds are graded and priced per-stone, with no fungible spot
market the way gold or oil have one.

What this computes instead, per the plan agreed before building: an
honest note stating plainly that no price data exists, PLUS the real
news-coverage signal already being tracked for these exact topics since
Phase 3 (the wool_mohair_cashmere and mining_minerals tags already exist
-- this is the first computation to actually surface them as a
commodity-adjacent view, not a new tagging category).

Fuel/energy prices are NOT covered here at all -- see the
fuel_energy_prices registry entry (status: deferred_no_free_api) for
why. Partial coverage exists via the existing energy_water news tag,
noted in the registry the same way AGOA/SACU-revenue's partial coverage
is noted elsewhere.
"""
import datetime
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

NEWS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "news", "general_signal_log.csv")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "commodity_signal_log.csv")

OUTPUT_COLUMNS = [
    "computed_at", "commodity", "has_price_data", "gap_note", "news_tag",
    "recent_article_count", "recent_headlines",
]

# commodity -> (honest gap note, the existing news tag that covers it)
COMMODITIES = {
    "Wool and mohair": (
        "No public price index exists. These are sold at regional auctions (South Africa/Lesotho wool and "
        "mohair growers' associations); prices are set per-lot at auction and reported by the auction houses "
        "themselves, not published as a continuous series a free API could expose.",
        "wool_mohair_cashmere",
    ),
    "Diamonds": (
        "No public spot price exists. Diamonds are graded and priced per-stone (cut, clarity, colour, carat all "
        "vary), not a fungible commodity traded on an exchange the way gold or oil are -- there is no equivalent "
        "of a 'diamond price per ounce' to track.",
        "mining_minerals",
    ),
}


def utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def compute_commodity_signal(computed_at):
    news = pd.read_csv(NEWS_PATH, keep_default_na=False) if os.path.exists(NEWS_PATH) else pd.DataFrame()
    rows = []
    for commodity, (gap_note, tag) in COMMODITIES.items():
        if not news.empty:
            matching = news[news["tags"].apply(lambda t: tag in str(t).split(";"))]
        else:
            matching = pd.DataFrame()
        rows.append({
            "computed_at": computed_at,
            "commodity": commodity,
            "has_price_data": False,
            "gap_note": gap_note,
            "news_tag": tag,
            "recent_article_count": len(matching),
            "recent_headlines": " | ".join(matching["title"].head(3).tolist()) if len(matching) else "",
        })
    return rows


def main():
    computed_at = utc_now_iso()
    print("Computing commodity signal (wool/mohair, diamonds -- no price API exists for either)...")
    rows = compute_commodity_signal(computed_at)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    pd.DataFrame(rows, columns=OUTPUT_COLUMNS).to_csv(OUTPUT_PATH, index=False)
    print(f"Wrote {len(rows)} row(s) to {OUTPUT_PATH}")
    for r in rows:
        print(f"  {r['commodity']}: {r['recent_article_count']} related article(s) tracked (tag: {r['news_tag']})")


if __name__ == "__main__":
    main()
