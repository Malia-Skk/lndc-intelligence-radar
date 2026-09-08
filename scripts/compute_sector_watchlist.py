"""
Phase 9 — sector watchlist: the strategy's named wage-goods (defensive)
and emerging (offensive) sectors, tracked against real trade and news
data where a real proxy exists, and honestly marked untracked where one
doesn't.

WHY THIS EXISTS: the dashboard's own Overview page has said plainly,
since the day it was built, that "5 new industrial sectors" has no
tracking source at all. This doesn't fully close that gap -- there's
still no single number for "sectors added" -- but it does give each
named sector in the strategy a real, checked status instead of silence,
which is most of what was actually missing.

METHODOLOGY, AND ITS LIMITS:
- HS 2-digit chapters are matched to each sector from the ACTUAL chapter
  descriptions in comtrade_sacu_basket_log.csv (checked directly against
  the real data before writing this mapping, not guessed from a generic
  HS code list) -- see SECTOR_DEFINITIONS below for exactly which chapter
  each sector maps to and why.
- For WAGE-GOODS sectors specifically, the strategy's own framing is
  import substitution -- so this computes an import-dependency ratio
  (imports / (imports + exports)) for each. A high ratio means Lesotho
  currently relies on imports for that category, which is exactly the
  thing the strategy names these sectors to fix.
- For EMERGING sectors, this reports export value and trend instead --
  the framing there is growth/diversification, not import substitution.
- SEVERAL SECTORS HAVE NO CLEAN HS MATCH AT ALL (MICE tourism, Basotho
  retail, public transport, the "mountain economy", data centres) because
  they're service sectors, not traded goods -- reported via news-tag
  signal only, or in two cases (music/arts/sports as a sector in its own
  right, and the "mountain economy") not tracked by ANY existing source,
  reported as such rather than forced into a proxy that doesn't fit.
- "Basic clothing" and "wool/mohair/cashmere processing" both sit inside
  HS chapters Lesotho already dominates as an EXPORT economy (CMT
  garments, chapter 61/62; raw wool, chapter 51) -- this cannot
  distinguish "basic clothing for domestic consumption" from "export-
  oriented garment manufacturing" at 2-digit HS granularity. Flagged
  explicitly per sector below, not smoothed over.
"""
import datetime
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))  # noqa: E402

TRADE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "trade", "comtrade_sacu_basket_log.csv")
NEWS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "news", "general_signal_log.csv")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "sector_watchlist_log.csv")

# Every sector named in the strategy's Ch.2.6 (wage-goods / defensive) and
# emerging-sector (offensive) lists. hs_chapters are HS 2-digit codes,
# checked against the real chapter descriptions in the live trade data
# before being assigned here -- see the module docstring.
SECTOR_DEFINITIONS = [
    # --- Wage-goods (defensive / import-substitution priority) ---
    {"id": "grains_poultry", "name": "Grains and poultry", "type": "wage_goods",
     "hs_chapters": [10, 2], "news_tags": ["agriculture_agroprocessing", "climate_food_security"],
     "coverage_note": "HS 10 (cereals) + HS 02 (meat, incl. poultry) as a reasonable trade proxy."},
    {"id": "energy_services", "name": "Electricity and energy services", "type": "wage_goods",
     "hs_chapters": [27], "news_tags": ["energy_water"],
     "coverage_note": "HS 27 is 'mineral fuels' broadly (oil, gas, coal) -- a loose proxy, since electricity "
                       "specifically (HS 2716) isn't separable at 2-digit chapter granularity."},
    {"id": "building_materials", "name": "Basic building materials", "type": "wage_goods",
     "hs_chapters": [68, 72], "news_tags": ["manufacturing_industrial"],
     "coverage_note": "HS 68 (stone/cement/plaster) + HS 72 (iron and steel) as a reasonable trade proxy."},
    {"id": "basic_clothing", "name": "Basic clothing", "type": "wage_goods",
     "hs_chapters": [61, 62], "news_tags": ["textiles_apparel"],
     "coverage_note": "IMPORTANT: HS 61/62 is the SAME chapter as Lesotho's large CMT export garment industry -- "
                       "this cannot distinguish domestic basic-clothing consumption from export-oriented "
                       "manufacturing at this data granularity. Treat the import-dependency figure with that "
                       "caveat in mind."},
    {"id": "pharmaceuticals", "name": "Basic pharmaceutical products", "type": "wage_goods",
     "hs_chapters": [30], "news_tags": [],
     "coverage_note": "HS 30 (pharmaceutical products) is an exact chapter match. No corresponding news tag "
                       "exists yet -- pharma-specific news would currently land in the discovery feed untagged."},

    # --- Emerging (offensive / growth & diversification) ---
    {"id": "critical_minerals", "name": "Critical minerals", "type": "emerging",
     "hs_chapters": [26, 71], "news_tags": ["mining_minerals"],
     "coverage_note": "HS 26 (ores) + HS 71 (precious stones/metals, incl. diamonds) -- 'critical minerals' "
                       "specifically (e.g. rare earths) isn't separable at 2-digit granularity, so this is "
                       "broader than the strategy's precise intent."},
    {"id": "mice_tourism", "name": "MICE tourism", "type": "emerging",
     "hs_chapters": [], "news_tags": ["tourism_mice"],
     "coverage_note": "A service sector -- no HS trade code applies. Tracked via news signal only."},
    {"id": "music_arts_sports", "name": "Music, arts and sports", "type": "emerging",
     "hs_chapters": [], "news_tags": [],
     "coverage_note": "NOT TRACKED by any existing source. HS 92 (musical instruments) and HS 95 (sports goods) "
                       "were considered but rejected -- they measure trade in equipment, not the creative/sports "
                       "economy the strategy actually means. No news tag category exists for this either."},
    {"id": "wool_mohair_cashmere", "name": "Wool, mohair and cashmere processing", "type": "emerging",
     "hs_chapters": [51], "news_tags": ["wool_mohair_cashmere"],
     "coverage_note": "HS 51 (wool, animal hair) is an exact chapter match. Note this measures the RAW/traded "
                       "commodity, not specifically the value-added 'processing' step the strategy names."},
    {"id": "basotho_retail", "name": "Basotho-owned retail sector", "type": "emerging",
     "hs_chapters": [], "news_tags": ["retail_smme"],
     "coverage_note": "A domestic service sector -- no HS trade code applies. Tracked via news signal only."},
    {"id": "public_transport", "name": "Public transport restructuring", "type": "emerging",
     "hs_chapters": [], "news_tags": ["transport_logistics"],
     "coverage_note": "A domestic service sector -- no HS trade code applies. Tracked via news signal only."},
    {"id": "mountain_economy", "name": "Mountain economy", "type": "emerging",
     "hs_chapters": [], "news_tags": [],
     "coverage_note": "NOT TRACKED by any existing source. No HS trade code applies, and no news tag category "
                       "exists for this either -- the strategy's own description of this sector is broad enough "
                       "(leveraging elevation and climate) that no single proxy would honestly capture it."},
    {"id": "data_centres", "name": "Data centres", "type": "emerging",
     "hs_chapters": [], "news_tags": ["ict_data"],
     "coverage_note": "Infrastructure/services -- no HS trade code applies. Tracked via news signal only."},
]


def utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_lesotho_trade(path):
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    df["trade_value_usd"] = pd.to_numeric(df["trade_value_usd"], errors="coerce")
    df = df.dropna(subset=["trade_value_usd"])
    return df[df["reporter"] == "Lesotho"]


def load_news_tag_counts(path):
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path, keep_default_na=False)
    counts = {}
    for tags_field in df["tags"]:
        for tag in str(tags_field).split(";"):
            tag = tag.strip()
            if tag:
                counts[tag] = counts.get(tag, 0) + 1
    return counts


def trade_value_for_chapters(lesotho_trade, chapters, flow, year):
    if lesotho_trade is None or not chapters:
        return None
    subset = lesotho_trade[
        (lesotho_trade["cmd_code"].isin(chapters))
        & (lesotho_trade["flow"] == flow)
        & (lesotho_trade["year"] == year)
    ]
    if subset.empty:
        return None
    return float(subset["trade_value_usd"].sum())


def compute_sector_row(sector, lesotho_trade, news_tag_counts, latest_year):
    row = {
        "sector_id": sector["id"],
        "sector_name": sector["name"],
        "sector_type": sector["type"],
        "hs_chapters": ";".join(str(c) for c in sector["hs_chapters"]) if sector["hs_chapters"] else "",
        "news_tags": ";".join(sector["news_tags"]) if sector["news_tags"] else "",
        "trade_data_available": bool(sector["hs_chapters"]),
        "news_data_available": bool(sector["news_tags"]),
        "latest_year": latest_year if sector["hs_chapters"] else None,
        "export_value_usd": None,
        "import_value_usd": None,
        "import_dependency_ratio": None,
        "recent_news_count": sum(news_tag_counts.get(t, 0) for t in sector["news_tags"]),
        "coverage_note": sector["coverage_note"],
    }

    if sector["hs_chapters"] and latest_year is not None:
        exp = trade_value_for_chapters(lesotho_trade, sector["hs_chapters"], "Export", latest_year)
        imp = trade_value_for_chapters(lesotho_trade, sector["hs_chapters"], "Import", latest_year)
        row["export_value_usd"] = exp
        row["import_value_usd"] = imp
        if sector["type"] == "wage_goods" and exp is not None and imp is not None and (exp + imp) > 0:
            row["import_dependency_ratio"] = round(imp / (exp + imp), 4)

    return row


def main():
    computed_at = utc_now_iso()
    lesotho_trade = load_lesotho_trade(TRADE_PATH)
    news_tag_counts = load_news_tag_counts(NEWS_PATH)

    latest_year = None
    if lesotho_trade is not None and not lesotho_trade.empty:
        latest_year = int(lesotho_trade["year"].max())
        print(f"Using {latest_year} as the latest year with Lesotho trade data.")
    else:
        print("WARNING: no trade data available -- all trade-based sector fields will be blank.")

    rows = []
    untracked = []
    for sector in SECTOR_DEFINITIONS:
        row = compute_sector_row(sector, lesotho_trade, news_tag_counts, latest_year)
        row["computed_at"] = computed_at
        rows.append(row)
        if not row["trade_data_available"] and not row["news_data_available"]:
            untracked.append(sector["name"])

    df = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    df.to_csv(OUTPUT_PATH, index=False)

    print(f"\nWrote {len(rows)} sector rows to {OUTPUT_PATH}")
    print(f"  {sum(r['trade_data_available'] for r in rows)} have a trade-data proxy")
    print(f"  {sum(r['news_data_available'] for r in rows)} have a matching news tag")
    if untracked:
        print(f"  NOT TRACKED by any existing source ({len(untracked)}): {', '.join(untracked)}")

    print("\nWage-goods import dependency (share of trade that's imports -- higher = more import-reliant):")
    for r in rows:
        if r["sector_type"] == "wage_goods" and r["import_dependency_ratio"] is not None:
            print(f"  {r['sector_name']}: {r['import_dependency_ratio']*100:.1f}% "
                  f"(exports ${r['export_value_usd']:,.0f}, imports ${r['import_value_usd']:,.0f})")


if __name__ == "__main__":
    main()
