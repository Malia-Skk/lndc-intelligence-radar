"""
Rule-based auto-tagging for news signals (Phase 3).

The automotive pilot's GDELT query was hand-written for one sector, which
meant anything outside "automotive" was invisible by construction. The
general query (fetch_gdelt_general.py) casts a much wider net -- but a wide
net returns a wide mix of topics, and without some way to sort them, "wide"
just becomes "noisy." This module does that sorting.

Deliberately rule-based, not LLM-based, for this phase: it's free, runs
instantly inside the same GitHub Actions job with no external call, is
fully deterministic (the same title always gets the same tags, which
matters for auditability), and is testable without touching a network.
An LLM-assisted fallback for genuinely ambiguous titles is a reasonable
future addition once the narrative layer exists -- Ask the Radar already
has a path to an LLM (via the Cloudflare Worker) that this could eventually
share -- but bolting a paid or rate-limited call onto a scheduled ingestion
script is a bigger decision than this phase needs to make.

Matching is on the ARTICLE TITLE ONLY, not full body text -- that's the
only text GDELT's DOC API artlist mode actually returns (see
fetch_gdelt_general.py). This means recall is inherently limited: an
article whose title doesn't happen to include a matched keyword is missed,
even if the body is squarely on-topic. That's a real limitation, not a bug,
and worth remembering when tuning categories -- title text is often
generic ("Lesotho signs deal with X"), so under-tagging is expected and
should be treated as normal rather than a sign the rules are broken.

An article can legitimately match more than one category (e.g. an AGOA
story about garment-sector layoffs is both trade_policy and labour). A
title matching none of the rule sets gets an empty tag list -- these are
the "discovery feed" items: not miscategorised, just not yet named as a
category worth tracking. A growing discovery feed of a particular flavour
is itself a signal that a new category (or registry source) is needed.
"""
import re

# category -> list of keywords/phrases. Checked as case-insensitive,
# word-boundary matches against the title (not naive substring matching --
# see _find_matches() below for why that distinction matters). Order doesn't affect the result
# (an article can match many categories), but is kept roughly aligned to
# the strategy's own domain map (wage-goods sectors, emerging sectors,
# then the broader external-dependency categories the strategy doesn't
# name but the economy runs on).
CATEGORY_KEYWORDS = {
    "textiles_apparel": [
        "apparel", "garment", "garments", "textile", "textiles", "clothing", "cut-make-trim", "cmt",
        "denim", "knitwear", "cotton mill", "yarn",
    ],
    "agriculture_agroprocessing": [
        "agriculture", "agro-processing", "agroprocessing", "farming", "grain", "grains",
        "poultry", "livestock", "crop", "crops", "herder", "irrigation",
    ],
    "wool_mohair_cashmere": [
        "wool", "mohair", "cashmere", "angora", "fleece",
    ],
    "mining_minerals": [
        "diamond", "diamonds", "mining", "mine", "mineral", "minerals", "quarry", "quarries", "gemstone", "kimberlite",
    ],
    "energy_water": [
        "electricity", "power station", "hydropower", "hydro-power",
        "highlands water", "lhwp", "lec", "wasco", "dam project", "power grid",
        "energy supply",
    ],
    "tourism_mice": [
        "tourism", "tourist", "tourists", "mice tourism", "conference centre", "convention centre",
        "hospitality sector", "hotel investment",
    ],
    "ict_data": [
        "data centre", "data center", "ict sector", "digital economy", "broadband",
        "fibre network", "tech hub",
    ],
    "manufacturing_industrial": [
        "factory", "factories", "manufacturing", "industrial estate", "industrial park",
        "industrialist", "production line",
    ],
    "fdi_investment": [
        "foreign direct investment", "fdi", "investor", "investors", "investment", "investments",
        "joint venture", "groundbreaking", "factory opening", "capital injection",
    ],
    "trade_policy": [
        "agoa", "tariff", "tariffs", "trade agreement", "export ban", "import quota",
        "customs union", "trade preference",
    ],
    "fiscal_sacu": [
        "sacu revenue", "national budget", "fiscal deficit", "treasury",
        "government spending", "tax revenue",
    ],
    "monetary_currency": [
        "loti", "rand peg", "central bank of lesotho", "cbl", "interest rate", "interest rates",
        "inflation rate", "currency peg",
    ],
    "labour_remittance": [
        "remittance", "remittances", "migrant worker", "mineworker", "unemployment",
        "labour union", "trade union", "strike action", "job losses", "layoffs",
        "minimum wage",
    ],
    "climate_food_security": [
        "drought", "droughts", "flood", "flooding", "flooded",
        "climate change", "food security", "crop failure", "crop failures",
        "famine", "food insecurity", "severe weather", "heavy rain",
    ],
    "governance_political": [
        "parliament", "election", "elections", "corruption", "dceo", "high court",
        "prime minister", "cabinet reshuffle", "coalition government",
    ],
    "transport_logistics": [
        "transport sector", "road project", "railway", "logistics hub",
        "border post", "freight",
    ],
    "retail_smme": [
        "retail sector", "smme", "small business", "informal trader", "wholesale market",
    ],
}

LNDC_MENTION_KEYWORDS = [
    "lndc", "lesotho national development corporation",
]


def _find_matches(title_lower, keywords):
    """Word-boundary matching, not naive substring matching. Naive substring
    checks have a real failure mode with short keywords: "mine " as a plain
    substring check matches inside "determine ", "cbl" matches inside
    "cable", etc. \\b in regex matches at a transition between a "word"
    character and a non-word one, which correctly rejects those false
    positives while still matching the keyword as a standalone word or
    exact phrase. See test_tagging.py for the specific "determine" case
    this guards against."""
    hits = []
    for kw in keywords:
        pattern = r"\b" + re.escape(kw.strip()) + r"\b"
        if re.search(pattern, title_lower):
            hits.append(kw.strip())
    return hits


def tag_article(title):
    """Returns (tags, matched_keywords_by_tag, mentions_lndc) for one
    article title.
      tags: sorted list of category names that matched (possibly empty)
      matched_keywords_by_tag: dict of category -> list of the specific
          keyword(s) that matched, kept for auditability (so a human can
          see WHY something got tagged a certain way, not just that it did)
      mentions_lndc: bool, checked separately from category tagging since
          it's relevant regardless of sector
    """
    if not title:
        return [], {}, False

    title_lower = title.lower()
    matched_keywords_by_tag = {}
    for category, keywords in CATEGORY_KEYWORDS.items():
        hits = _find_matches(title_lower, keywords)
        if hits:
            matched_keywords_by_tag[category] = hits

    tags = sorted(matched_keywords_by_tag.keys())
    mentions_lndc = bool(_find_matches(title_lower, LNDC_MENTION_KEYWORDS))
    return tags, matched_keywords_by_tag, mentions_lndc


def format_tags_for_csv(tags):
    return ";".join(tags)


def format_matched_keywords_for_csv(matched_keywords_by_tag):
    parts = [f"{cat}:{'/'.join(kws)}" for cat, kws in sorted(matched_keywords_by_tag.items())]
    return ";".join(parts)
