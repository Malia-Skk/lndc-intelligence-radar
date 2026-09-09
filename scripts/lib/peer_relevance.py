"""
Peer/relevance country matching for the global signal monitor (Phase A of
the predictive-analytics expansion, per the Radar diagnostic report).

WHY THIS EXISTS: the general news pull (fetch_gdelt_general.py) requires
"Lesotho" in every query, which is exactly right for tracking Lesotho's
own situation but structurally blind to a shift that hasn't reached
Lesotho yet -- the diagnostic report's own illustrative Radar questions
("what global apparel-sourcing shifts could create relocation
opportunities for Lesotho?") need signal that ISN'T Lesotho-scoped. This
module's job is narrow and honest: given a global article's title, note
whether it mentions a country worth treating as directly comparable to
Lesotho's situation, so a human reviewer can tell "this happened
somewhere directly comparable" from "this is a generic global trend
story" at a glance -- not to score or rank opportunities, which is a
separate, later phase (Phase B: the discovery/corroboration engine).

DELIBERATELY NOT NLP: this is the same rule-based, word-boundary string
matching as lib/tagging.py, reusing its exact safe-matching helper rather
than a second, subtly-different implementation. It shares that module's
honest limitation too -- matching on title text only, which GDELT's DOC
API artlist mode is what actually returns, so recall is inherently
limited. A country that's the actual subject of an article but named
only in the body, not the title, will be missed.

THESE LISTS ARE CURATED, NOT AUTHORITATIVE: "apparel-sourcing peer" and
"notable emerging data-centre market" are judgement calls about which
countries are genuinely comparable to Lesotho's situation, not a formal
economic classification. They will go stale and need periodic review as
global sourcing patterns and data-centre investment shift -- flagged
here explicitly rather than presented as a settled, permanent list.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from tagging import _find_matches  # noqa: E402 -- reusing the exact safe word-boundary matcher, not a second copy of the same regex-safety logic

# Countries with a real, documented apparel/garment export sector
# comparable to Lesotho's -- several (Kenya, Ethiopia, Eswatini,
# Madagascar) share Lesotho's own AGOA-dependency, which is precisely
# the kind of comparable situation worth flagging directly.
APPAREL_SOURCING_PEERS = [
    "Bangladesh", "Vietnam", "Cambodia", "Ethiopia", "Kenya", "Madagascar",
    "Eswatini", "Nicaragua", "Haiti", "Myanmar", "Indonesia", "Sri Lanka",
    "Honduras", "Egypt", "Morocco", "Jordan", "Tanzania",
]

# SACU members -- the same peer group already used for Phase 12's
# peer-country insights, kept consistent rather than inventing a
# different regional grouping for this module specifically.
SACU_PEERS = ["South Africa", "Botswana", "Namibia", "Eswatini"]

# A curated (not exhaustive) list of emerging/frontier markets where data
# -centre investment is genuinely being reported, so a mention of one of
# these reads as "a directly comparable case worth a closer look" rather
# than a generic global trend story about a mature market.
EMERGING_DATACENTRE_MARKETS = [
    "Kenya", "Nigeria", "Ghana", "Rwanda", "South Africa", "Egypt",
    "Morocco", "Tanzania", "Uganda", "Senegal", "Ivory Coast", "Zambia",
]


def find_country_mentions(title, country_list):
    """Word-boundary match against a supplied country list -- returns the
    subset of country_list (in their original casing) actually found in
    title. Reuses tagging.py's exact matching discipline (case-
    insensitive, word-boundary, so "Chad" doesn't false-match inside an
    unrelated word)."""
    if not title:
        return []
    lowered_to_original = {c.lower(): c for c in country_list}
    matches = _find_matches(title.lower(), list(lowered_to_original.keys()))
    return [lowered_to_original[m] for m in matches]


def tag_relevance(title, bucket):
    """Given an article title and which query bucket it came from, returns
    (mentioned_countries, relevance_note) -- a short, honest description
    of why this is or isn't a directly comparable signal, for a human
    reviewer to weigh, not an automated relevance score."""
    if bucket == "apparel_sourcing_shifts":
        hits = find_country_mentions(title, APPAREL_SOURCING_PEERS)
        if hits:
            return hits, f"Mentions {', '.join(hits)} -- a directly comparable apparel-exporting economy."
        return [], "General apparel-sourcing trend story; no specific comparable country named in the title."
    if bucket == "critical_minerals_peers":
        hits = find_country_mentions(title, SACU_PEERS)
        return hits, (f"SACU peer: {', '.join(hits)}." if hits else "Matched the SACU-scoped query but no peer name found in title text (recall limitation -- see module docstring).")
    if bucket == "data_centre_investment":
        hits = find_country_mentions(title, EMERGING_DATACENTRE_MARKETS)
        if hits:
            return hits, f"Mentions {', '.join(hits)} -- a developing-market data-centre case, more directly comparable to Lesotho than a mature-market story."
        return [], "Data-centre investment story with no emerging-market country named in the title (may still be a mature-market story, e.g. US/EU)."
    if bucket == "trade_preference_policy":
        hits = find_country_mentions(title, SACU_PEERS + ["Lesotho"])
        if hits:
            return hits, f"Directly names {', '.join(hits)} -- a regional trade-preference development, not just a generic policy story."
        return [], "General AGOA/AfCFTA/trade-preference story; no SACU country named in the title."
    return [], "Unrecognised bucket -- no relevance rule defined."
