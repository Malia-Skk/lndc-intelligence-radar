"""
Phase B of the predictive-analytics expansion (per the LNDC Industrial
Intelligence Radar diagnostic report) -- automatic sector/opportunity
discovery. This is the piece the whole expansion was requested for:
surfacing recurring, growing themes NOT already in the strategy's named
13-sector list, using signal already flowing from Phase A (global
signal) and the existing pipeline (Lesotho-specific discovery feed).

WHAT THIS DELIBERATELY IS NOT: a machine-learning topic-modelling or
clustering system. That was a real design choice, not a shortcut --
LDA/embeddings-based clustering is opaque (a cluster's "meaning" has to
be inferred after the fact) and the diagnostic report's own standing
caution applies directly: fluent-sounding output is not the same as
correct output, and a black-box cluster a human reviewer can't trace
back to specific real headlines is exactly the kind of thing that
caution warns against. What this does instead is fully transparent and
auditable: extract recurring 2-3 word phrases from real article titles,
count how often each genuinely distinct phrase appears, exclude
anything already covered by the 13 named sectors or 17 existing news
categories, and score what's left by how many INDEPENDENT signal types
corroborate it (the report's own Chapter 3.1 principle: a signal in one
channel should be cross-checked against another). Every candidate this
produces links directly back to the real headlines and real trade/
pipeline records that produced it -- nothing here is a claim a human
reviewer can't immediately verify or reject.

HONEST LIMITATION, STATED PLAINLY: with roughly 600 global-signal rows
and 20 discovery-feed rows at the time this was built, this is
genuinely thin data for "trend detection" in the fullest sense (showing
a phrase is GROWING over time needs real history to compare against,
which barely exists yet). What this can honestly do today is candidate
GENERATION -- surfacing phrases that recur across multiple real
articles right now -- not yet confident growth-trend detection. That
will improve automatically as more daily pulls accumulate, the same
honest trajectory already used for the news-volume anomaly check in
Phase 12.

GOVERNANCE, MATCHING THE REPORT'S OWN CHAPTER 8.3: every row this
produces is explicitly a hypothesis for a human reviewer to weigh, not
a finding to publish directly. The output schema itself carries this --
every row includes the real headlines and URLs it's based on, so
"why did the system flag this" is never a black box.
"""
import csv
import datetime
import os
import re
import sys
from collections import Counter

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from lib.tagging import CATEGORY_KEYWORDS  # noqa: E402

DISCOVERY_FEED_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "discovery_feed_log.csv")
GLOBAL_SIGNAL_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "news", "global_signal_log.csv")
TRADE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "trade", "comtrade_sacu_basket_log.csv")
PIPELINE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "pipeline", "pipeline_canonical_latest.csv")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "sector_discovery_log.csv")

OUTPUT_COLUMNS = [
    "computed_at", "candidate_phrase", "distinct_domain_count", "lesotho_local", "global_trend",
    "trade_signal", "pipeline_signal", "corroboration_count", "example_titles", "example_urls",
]

# The 13 strategy-named sectors' own significant words, so a candidate
# phrase that's really just "wool processing" again doesn't get
# re-discovered as new. Kept as a flat set of individual words (not
# phrases) since candidate n-grams are checked word-by-word against it.
KNOWN_SECTOR_WORDS = {
    "grains", "poultry", "cereals", "meat", "electricity", "energy", "building", "materials",
    "cement", "steel", "clothing", "garment", "garments", "apparel", "textile", "textiles",
    "pharmaceutical", "pharmaceuticals", "minerals", "mining", "diamond", "diamonds", "tourism",
    "mice", "music", "arts", "sports", "wool", "mohair", "cashmere", "retail", "transport",
    "mountain", "data", "centre", "centres", "center", "centers",
}

# Every keyword across the 17 existing news-tag categories, flattened to
# individual words -- these are ALREADY-TRACKED themes, so a candidate
# phrase built from these words isn't a new discovery, it's the existing
# category showing up again under different wording.
KNOWN_TAG_WORDS = set()
for _keywords in CATEGORY_KEYWORDS.values():
    for _kw in _keywords:
        KNOWN_TAG_WORDS.update(_kw.lower().split())

# Generic English stopwords plus journalism boilerplate that recurs in
# headlines regardless of subject matter -- without this, the most
# "recurring phrase" in almost any news corpus is junk like "said the"
# or "according to", not a real theme.
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by",
    "is", "are", "was", "were", "be", "been", "being", "this", "that", "these", "those", "it",
    "its", "as", "from", "into", "over", "after", "before", "than", "then", "so", "such",
    "not", "no", "up", "out", "about", "against", "between", "through", "during", "amid",
    "amidst", "new", "year", "years", "day", "week", "month", "says", "said", "according",
    "reuters", "news", "report", "reports", "reported", "update", "latest", "breaking",
    "here", "what", "why", "how", "who", "when", "which", "will", "would", "could", "should",
    "has", "have", "had", "do", "does", "did", "can", "may", "might", "must", "one", "two",
    "three", "first", "second", "third", "top", "big", "major", "global", "world", "million",
    "billion", "percent", "per", "cent", "inc", "ltd", "corp", "co", "llc",
    # Wire-service / syndication credit-line boilerplate -- a real,
    # evidence-based addition: a first live run showed "APO Group -
    # Africa Newsroom / Press release" (a real syndication credit line)
    # tokenizing as if it were article CONTENT, producing nonsense
    # "candidates" like "apo group" and "newsroom press" purely from
    # attribution text, not any actual theme.
    "apo", "group", "newsroom", "press", "release", "bloomberg", "afp", "ap", "pti", "xinhua",
    "businesswire", "prnewswire", "globenewswire",
    # Generic finance/stock-market reporting boilerplate -- a first live
    # run showed "sells stock", "insider sells stock", "nuclear stocks"
    # surfacing purely from routine investor-news reporting patterns
    # ("Director Sells $X in Stock"), not any real industrial signal.
    "stock", "stocks", "shares", "share", "insider", "sells", "buys", "nasdaq", "nyse", "ipo",
    "ftse", "dividend", "earnings", "nyse", "etf",
    # Common political-process verbs that recur across totally unrelated
    # stories (a US governor signing an order, a different president
    # signing a trade bill) purely because "signs" is a common headline
    # verb, not because the stories share any real subject.
    "signs", "vetoes", "revoke", "revokes", "executive", "lawmakers", "congress", "senate",
    # Weather-alert boilerplate -- a first live run showed "orange level
    # warning" (a standard storm-alert phrasing used across many news
    # outlets) surfacing as a "candidate" purely because the same alert
    # format recurs across weather stories, not because it's an
    # economic signal of any kind.
    "orange", "red", "yellow", "warning", "warnings", "alert", "alerts", "storm", "storms",
    "thunderstorm", "thunderstorms", "hail", "flooding", "level",
}

# Major countries/regions -- a first live run showed "south africa",
# "dominican republic", and "african countries" surfacing as
# "candidates" purely because a country name recurs across stories, not
# because the country itself is a sector or theme. Deliberately broad
# (covers common Comtrade/GDELT-relevant country names) since a
# genuinely substantive co-occurring phrase from the same title (e.g.
# "chrome underground economy") is captured by a SEPARATE n-gram anyway
# -- excluding the pure place-name phrase doesn't lose real signal.
COUNTRY_STOPWORDS = {
    "south africa", "african countries", "dominican republic", "united states", "united kingdom",
    "saudi arabia", "hong kong", "new zealand", "sri lanka", "costa rica", "el salvador",
    "ivory coast", "burkina faso", "sierra leone", "south korea", "north korea",
}

WORD_RE = re.compile(r"[a-z]+")

MIN_DISTINCT_DOMAINS = 2  # a phrase appearing on only one domain is one story republished nowhere else, or one outlet's own framing -- not independent recurrence
MAX_CANDIDATES_OUTPUT = 40  # a screening list a human can actually read in one sitting


def utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def clean_words(title):
    """Lowercase, strip punctuation, drop stopwords -- returns the
    remaining significant words in original order."""
    words = WORD_RE.findall(title.lower())
    # Real bug caught via testing: a plain len(w) > 2 threshold silently
    # dropped "ai" (2 letters) entirely, missing exactly the kind of
    # acronym-heavy tech/economic term this discovery engine most needs
    # to catch. An explicit allowlist for meaningful short acronyms,
    # rather than just lowering the length threshold across the board
    # (which would let through more 2-letter noise words too). Note:
    # WORD_RE only matches [a-z]+, so a digit-containing acronym like
    # "5G" can never actually be extracted as a word here regardless of
    # this allowlist -- a known, minor limitation, not fixed here to
    # avoid the larger, separate question of how digits should be
    # handled throughout extraction generally.
    # "us" deliberately excluded from this allowlist despite being a
    # common abbreviation for "United States" -- it's also an extremely
    # common pronoun ("helps us understand"), and unlike "it" (already
    # excluded via STOPWORDS regardless of this allowlist), there's no
    # existing filter catching the pronoun sense. "united states" as a
    # full phrase is still excluded via COUNTRY_STOPWORDS above.
    SHORT_ACRONYM_ALLOWLIST = {"ai", "ev", "it", "ip", "vc", "pe", "un", "eu"}
    return [w for w in words if w not in STOPWORDS and (len(w) > 2 or w in SHORT_ACRONYM_ALLOWLIST)]


def extract_ngrams(words, n):
    return [" ".join(words[i:i + n]) for i in range(len(words) - n + 1)]


def is_known_phrase(phrase):
    """A candidate is NOT new if any of its words already belongs to a
    named sector or an existing tracked news category -- this is a
    deliberately generous exclusion (checking word-by-word, not requiring
    the whole phrase to match) so a phrase like "textile innovation hub"
    still gets excluded via "textile", rather than only excluding exact
    repeats of existing category names. Also excludes pure place-name
    phrases directly (see COUNTRY_STOPWORDS above)."""
    if phrase in COUNTRY_STOPWORDS:
        return True
    words = set(phrase.split())
    return bool(words & KNOWN_SECTOR_WORDS) or bool(words & KNOWN_TAG_WORDS)


def normalize_title(title):
    """Returns the SET of significant words in a title, for similarity
    comparison -- not a single normalized string. A first attempt at
    exact-string normalization missed real syndication cases: "Business :
    Africa richest man eyes continent largest IPO" vs "Africa richest man
    eyes continent largest IPO" differ by exactly one word ("business"),
    so they normalize to two different strings despite being the same
    story. Comparing word SETS with Jaccard similarity (see
    titles_are_similar below) catches this; exact-string matching
    structurally cannot."""
    return set(clean_words(title))


SIMILARITY_THRESHOLD = 0.7  # two titles sharing 70%+ of their significant words are treated as the same underlying story


def titles_are_similar(word_set_a, word_set_b):
    if not word_set_a or not word_set_b:
        return False
    intersection = len(word_set_a & word_set_b)
    union = len(word_set_a | word_set_b)
    return (intersection / union) >= SIMILARITY_THRESHOLD if union else False


def count_distinct_stories(word_sets):
    """Greedily groups a list of title word-sets into distinct stories --
    each new title joins an existing group if it's similar enough to
    that group's first (representative) member, otherwise it starts a
    new group. Returns the number of distinct groups, which is what
    "genuinely independent recurrence" actually means, not raw title
    count."""
    representatives = []
    for word_set in word_sets:
        if not any(titles_are_similar(word_set, rep) for rep in representatives):
            representatives.append(word_set)
    return len(representatives)


def load_titles_with_source(path, source_label):
    if not os.path.exists(path):
        return []
    df = pd.read_csv(path, keep_default_na=False)
    if "title" not in df.columns:
        return []
    if "language" in df.columns:
        df = df[df["language"] == "English"]  # exclude the pre-fix multi-language rows honestly, not silently
    url_col = "url" if "url" in df.columns else None
    # The two source files use different column names for the same
    # concept (discovery_feed_log.csv: domain_source; global_signal_log.csv: domain)
    domain_col = "domain" if "domain" in df.columns else ("domain_source" if "domain_source" in df.columns else None)
    return [
        {
            "title": r["title"], "url": r[url_col] if url_col else "",
            "domain": r[domain_col] if domain_col else "", "source": source_label,
        }
        for _, r in df.iterrows() if r.get("title")
    ]


def build_candidate_phrases(articles):
    """Counts every 2- and 3-word phrase across all articles, excluding
    already-known sector/category words, and tracks which DISTINCT
    DOMAINS and DISTINCT (word-set) TITLES produced each candidate, for
    later similarity-based story-counting in main(). Domain and title
    diversity both matter, for two different real failure modes a first
    live run showed: (1) the same story republished many times on the
    SAME domain, and (2) the exact same story syndicated, with minor
    wording variations, across many DIFFERENT domains -- which a naive
    domain-only check cannot catch, since the domains genuinely differ
    even though the underlying story doesn't."""
    phrase_domains = {}     # phrase -> set of distinct domains
    phrase_title_wordsets = {}  # phrase -> list of title word-sets (for similarity grouping)
    phrase_examples = {}    # phrase -> list of {title, url, domain, source}

    for article in articles:
        words = clean_words(article["title"])
        candidates = extract_ngrams(words, 2) + extract_ngrams(words, 3)
        seen_this_article = set()
        title_wordset = normalize_title(article["title"])
        for phrase in candidates:
            if is_known_phrase(phrase):
                continue
            if phrase in seen_this_article:
                continue  # count each article once per phrase, not once per occurrence within the same title
            seen_this_article.add(phrase)
            phrase_domains.setdefault(phrase, set()).add(article["domain"])
            phrase_title_wordsets.setdefault(phrase, []).append(title_wordset)
            phrase_examples.setdefault(phrase, []).append(article)

    return phrase_domains, phrase_title_wordsets, phrase_examples


def load_trade_descriptions():
    if not os.path.exists(TRADE_PATH):
        return ""
    df = pd.read_csv(TRADE_PATH, keep_default_na=False)
    if "cmd_desc" not in df.columns:
        return ""
    return " ".join(df["cmd_desc"].astype(str).unique()).lower()


def load_pipeline_sectors():
    if not os.path.exists(PIPELINE_PATH):
        return ""
    df = pd.read_csv(PIPELINE_PATH, keep_default_na=False)
    if "sector" not in df.columns:
        return ""
    return " ".join(df["sector"].astype(str).unique()).lower()


def check_corroboration(phrase, lesotho_sources, global_sources, trade_text, pipeline_text):
    phrase_words = phrase.split()
    lesotho_local = phrase in lesotho_sources
    global_trend = phrase in global_sources
    # Word-level match for trade/pipeline text, not phrase-level -- these
    # are short, sparse text fields (a handful of HS-chapter descriptions,
    # a handful of pipeline sector labels), so requiring the exact
    # multi-word phrase would almost always miss a genuine, real match.
    trade_signal = any(re.search(r"\b" + re.escape(w) + r"\b", trade_text) for w in phrase_words)
    pipeline_signal = any(re.search(r"\b" + re.escape(w) + r"\b", pipeline_text) for w in phrase_words)
    count = sum([lesotho_local, global_trend, trade_signal, pipeline_signal])
    return lesotho_local, global_trend, trade_signal, pipeline_signal, count


def main():
    computed_at = utc_now_iso()

    lesotho_articles = load_titles_with_source(DISCOVERY_FEED_PATH, "lesotho_discovery")
    global_articles = load_titles_with_source(GLOBAL_SIGNAL_PATH, "global_signal")
    all_articles = lesotho_articles + global_articles
    print(f"Scanning {len(lesotho_articles)} Lesotho-discovery-feed articles + {len(global_articles)} global-signal articles (English only).")

    if not all_articles:
        print("No article data available at all -- writing an empty (but correctly-headed) output.")
        pd.DataFrame([], columns=OUTPUT_COLUMNS).to_csv(OUTPUT_PATH, index=False)
        return

    phrase_domains, phrase_title_wordsets, phrase_examples = build_candidate_phrases(all_articles)
    phrase_distinct_stories = {p: count_distinct_stories(wordsets) for p, wordsets in phrase_title_wordsets.items()}
    recurring = {
        p: domains for p, domains in phrase_domains.items()
        if len(domains) >= MIN_DISTINCT_DOMAINS and phrase_distinct_stories[p] >= MIN_DISTINCT_DOMAINS
    }
    print(f"{len(phrase_domains)} distinct new (not-already-tracked) phrases found; {len(recurring)} recur across {MIN_DISTINCT_DOMAINS}+ separate domains AND {MIN_DISTINCT_DOMAINS}+ genuinely distinct stories (similarity-grouped, not exact-text-matched, so a syndicated story with minor wording differences across domains is still correctly recognised as ONE story, not counted as independent corroboration).")

    lesotho_phrase_set = set()
    for article in lesotho_articles:
        lesotho_phrase_set.update(extract_ngrams(clean_words(article["title"]), 2))
        lesotho_phrase_set.update(extract_ngrams(clean_words(article["title"]), 3))
    global_phrase_set = set()
    for article in global_articles:
        global_phrase_set.update(extract_ngrams(clean_words(article["title"]), 2))
        global_phrase_set.update(extract_ngrams(clean_words(article["title"]), 3))

    trade_text = load_trade_descriptions()
    pipeline_text = load_pipeline_sectors()

    rows = []
    for phrase, domains in recurring.items():
        lesotho_local, global_trend, trade_signal, pipeline_signal, corroboration = check_corroboration(
            phrase, lesotho_phrase_set, global_phrase_set, trade_text, pipeline_text
        )
        examples = phrase_examples[phrase][:3]
        rows.append({
            "computed_at": computed_at,
            "candidate_phrase": phrase,
            "distinct_domain_count": len(domains),
            "lesotho_local": lesotho_local,
            "global_trend": global_trend,
            "trade_signal": trade_signal,
            "pipeline_signal": pipeline_signal,
            "corroboration_count": corroboration,
            "example_titles": " | ".join(a["title"] for a in examples),
            "example_urls": " | ".join(a["url"] for a in examples),
        })

    # Sorted by corroboration first (a phrase backed by multiple
    # independent signal types is more worth a human's time than one
    # that just recurs in similar-sounding headlines), then by raw
    # mention count as a tiebreaker.
    rows.sort(key=lambda r: (-r["corroboration_count"], -r["distinct_domain_count"]))
    rows = rows[:MAX_CANDIDATES_OUTPUT]

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    pd.DataFrame(rows, columns=OUTPUT_COLUMNS).to_csv(OUTPUT_PATH, index=False)
    print(f"Wrote {len(rows)} candidate row(s) to {OUTPUT_PATH}.")
    for r in rows[:10]:
        print(f"  [{r['corroboration_count']} signals] \"{r['candidate_phrase']}\" -- {r['distinct_domain_count']} distinct domain(s)")


if __name__ == "__main__":
    main()
