"""
Phase C of the predictive-analytics expansion (per the LNDC Industrial
Intelligence Radar diagnostic report, Chapter 3.1) -- extends Phase B's
discovery candidates with two genuinely new, independent signal types
Phase B's own script never checks against, plus temporal persistence
now that compute_sector_discovery.py has been switched from overwriting
its output to appending a genuine historical snapshot each run.

WHY THIS IS A SEPARATE SCRIPT FROM compute_sector_discovery.py, NOT AN
EXTENSION OF IT: Phase B's script computes candidates fresh from article
text every run and has no reason to look at its own past output. This
script's whole job IS looking at that accumulated history, plus two
completely different data sources (anomaly signals, peer insights) that
have nothing to do with article-title extraction. Keeping them separate
means Phase B keeps working exactly as before if this script's inputs
are ever unavailable, and this script can evolve its corroboration
logic without touching the (already tested, already live) discovery
engine itself.

THREE CORROBORATION SIGNALS ADDED HERE, NONE OF THEM CHECKED BY PHASE B:

1. TEMPORAL PERSISTENCE. A phrase appearing in only one run could be a
   one-off news cycle; the same phrase recurring across multiple
   SEPARATE, independently-computed runs (spanning real elapsed days,
   given the daily schedule) is a materially different, stronger signal
   -- exactly the "does this keep showing up" question a human analyst
   would ask before taking a candidate seriously. HONEST LIMITATION:
   this log has only just started accumulating history (Phase B's
   append-vs-overwrite fix shipped alongside this script), so this
   signal starts weak and gets more meaningful automatically as more
   daily runs accumulate -- the same honest trajectory as the
   news-volume anomaly check and this project's other "needs more
   history" signals.

2. ANOMALY CROSS-REFERENCE. Checks whether a discovery candidate shares
   significant words with any currently-flagged anomaly's own label
   (e.g. a trade anomaly in a specific product category). A candidate
   theme that's ALSO showing up as a genuine statistical anomaly
   elsewhere in the pipeline is corroborated by a completely different
   detection method (z-score screening against real trade/macro data),
   not just recurring news coverage.

3. PEER-INSIGHT CROSS-REFERENCE. Checks whether a candidate shares
   significant words with any SACU peer-country finding (Phase 12).
   A theme also showing up in what Lesotho's neighbours are
   experiencing is corroborated by regional context, not just Lesotho-
   specific or global news.

Every corroboration claim here, like Phase B's, links back to the
specific matching record (the anomaly label, the peer insight headline)
so a human reviewer can verify it directly rather than trust a bare
count.

HONEST LIMITATION ON THE WORD-OVERLAP MATCHING ITSELF: excluding
generic/near-universal words (see GENERIC_OVERLAP_WORDS below) catches
the false-positive pattern actually observed in testing (a shared
country name or "exports" alone), but a genuinely polysemous word --
one that means different things in different contexts, like "solar"
in a solar-panel story versus a solar-eclipse story -- can still produce
an occasional false match, since it isn't a generic/common word in the
sense that "exports" is. This is a real, acknowledged limitation of
simple word-overlap matching, not something this design claims to solve.
The cost is low: a human reviewer looking at a corroboration claim and
its linked source text would dismiss a genuinely irrelevant match in
seconds, which is exactly why every claim links back to its source
rather than just reporting a bare score.
"""
import datetime
import os
import re
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

DISCOVERY_LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "sector_discovery_log.csv")
ANOMALY_SIGNALS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "anomaly_signals_log.csv")
PEER_INSIGHTS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "peer_insights_log.csv")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "derived", "sector_corroboration_log.csv")

OUTPUT_COLUMNS = [
    "computed_at", "candidate_phrase", "base_corroboration_count", "runs_seen_in", "first_seen",
    "persistent_across_runs", "anomaly_signal", "anomaly_matches", "peer_signal", "peer_matches",
    "enhanced_corroboration_count", "example_titles",
]

MIN_RUNS_FOR_PERSISTENCE = 2  # appearing in only 1 run isn't "persistence" -- it's just this run's result
STOPWORD_LITE = {"the", "a", "an", "and", "or", "in", "on", "at", "to", "for", "of", "with", "by"}

# Words too generic to count as a meaningful topical match on their own --
# checked against real output before shipping, not guessed in advance: a
# first real run showed "gold exports" (Ghana's gold-export policy)
# "matching" an anomaly about Botswana's UNRELATED manufactured exports,
# purely because both happen to contain the word "exports" -- and "africa
# chrome" matching three South African export anomalies about fruit,
# edible preparations, and optical instruments, purely via "africa"/
# "south africa" appearing in nearly every South African trade-anomaly
# label regardless of subject. These specific words are so close to
# universal in trade-anomaly labels and peer-insight text that they're
# excluded outright, the same way compute_sector_discovery.py excludes
# country names from its own candidate list for an analogous reason.
# (An earlier version ALSO required 2+ shared words on top of this
# exclusion, reasoning a single shared word was too easy to hit by
# coincidence -- removed after testing showed it rejected genuine
# single-specific-word matches like "hydrogen"; see meaningful_overlap()'s
# own docstring for the full detail.)
GENERIC_OVERLAP_WORDS = {
    "africa", "african", "south", "exports", "export", "imports", "import",
    "trade", "goods", "merchandise", "products", "product",
}


def utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def significant_words(text):
    words = re.findall(r"[a-z]+", str(text).lower())
    return [w for w in words if w not in STOPWORD_LITE and len(w) > 2]


def meaningful_overlap(phrase_words, other_words, exclude_generic=True):
    """The actual matching rule used by both check_anomaly_overlap and
    check_peer_overlap: any shared word after removing generic, near-
    universal terms. Checked directly against real data before settling
    on this rule, not designed in the abstract: an EARLIER version also
    required 2+ shared words on top of generic-word exclusion, reasoning
    that a single shared word was too easy to hit by coincidence -- but
    that extra requirement was actually redundant with generic-word
    exclusion AND actively harmful. Confirmed directly: "green hydrogen"
    vs. "Hydrogen fuel cell components" share exactly one word after
    exclusion ("hydrogen"), which IS a genuine, specific, meaningful
    match -- requiring a second shared word would have rejected this
    real corroboration outright. Meanwhile, both original real false-
    positive cases ("gold exports" vs. an unrelated Botswana anomaly;
    "africa chrome" vs. an unrelated fruit-export anomaly) already
    produce ZERO overlap once generic words are excluded -- the count
    minimum was never what was rejecting them in the first place."""
    a = phrase_words - GENERIC_OVERLAP_WORDS if exclude_generic else phrase_words
    b = other_words - GENERIC_OVERLAP_WORDS if exclude_generic else other_words
    return bool(a & b)


def load_discovery_history():
    if not os.path.exists(DISCOVERY_LOG_PATH):
        return None, None
    df = pd.read_csv(DISCOVERY_LOG_PATH, keep_default_na=False)
    if df.empty:
        return None, None
    latest_ts = df["computed_at"].max()
    latest = df[df["computed_at"] == latest_ts]
    return df, latest


def compute_persistence(history, phrase):
    """How many DISTINCT past runs (by computed_at) has this exact phrase
    appeared in, across the log's full history -- not row count, since a
    phrase's own row exists exactly once per run by construction."""
    matching = history[history["candidate_phrase"] == phrase]
    runs = matching["computed_at"].nunique()
    first_seen = matching["computed_at"].min()
    return runs, first_seen


def load_anomaly_labels():
    if not os.path.exists(ANOMALY_SIGNALS_PATH):
        return []
    df = pd.read_csv(ANOMALY_SIGNALS_PATH, keep_default_na=False)
    if "is_anomaly" not in df.columns:
        return []
    flagged = df[df["is_anomaly"].astype(str) == "True"]
    return list(flagged["series_label"].unique())


def load_peer_insight_texts():
    if not os.path.exists(PEER_INSIGHTS_PATH):
        return []
    df = pd.read_csv(PEER_INSIGHTS_PATH, keep_default_na=False)
    if "headline" not in df.columns:
        return []
    return [(row["headline"], row.get("detail", "")) for _, row in df.iterrows()]


def check_anomaly_overlap(phrase, anomaly_labels):
    phrase_words = set(phrase.split())
    matches = []
    for label in anomaly_labels:
        label_words = set(significant_words(label))
        if meaningful_overlap(phrase_words, label_words):
            matches.append(label)
    return matches


def check_peer_overlap(phrase, peer_texts):
    phrase_words = set(phrase.split())
    matches = []
    for headline, detail in peer_texts:
        combined_words = set(significant_words(headline)) | set(significant_words(detail))
        if meaningful_overlap(phrase_words, combined_words):
            matches.append(headline)
    return matches


def main():
    computed_at = utc_now_iso()

    history, latest = load_discovery_history()
    if latest is None:
        print("No sector_discovery_log.csv data available yet -- nothing to corroborate. Will try again once Phase B has produced at least one run.")
        pd.DataFrame([], columns=OUTPUT_COLUMNS).to_csv(OUTPUT_PATH, index=False)
        return

    anomaly_labels = load_anomaly_labels()
    peer_texts = load_peer_insight_texts()
    print(f"Cross-referencing {len(latest)} current discovery candidates against {len(anomaly_labels)} flagged anomalies and {len(peer_texts)} peer insights.")

    rows = []
    for _, candidate in latest.iterrows():
        phrase = candidate["candidate_phrase"]
        base_count = int(candidate["corroboration_count"])

        runs_seen_in, first_seen = compute_persistence(history, phrase)
        persistent = runs_seen_in >= MIN_RUNS_FOR_PERSISTENCE

        anomaly_matches = check_anomaly_overlap(phrase, anomaly_labels)
        peer_matches = check_peer_overlap(phrase, peer_texts)

        enhanced_count = base_count + int(persistent) + int(bool(anomaly_matches)) + int(bool(peer_matches))

        rows.append({
            "computed_at": computed_at,
            "candidate_phrase": phrase,
            "base_corroboration_count": base_count,
            "runs_seen_in": runs_seen_in,
            "first_seen": first_seen,
            "persistent_across_runs": persistent,
            "anomaly_signal": bool(anomaly_matches),
            "anomaly_matches": " | ".join(anomaly_matches[:3]),
            "peer_signal": bool(peer_matches),
            "peer_matches": " | ".join(peer_matches[:3]),
            "enhanced_corroboration_count": enhanced_count,
            "example_titles": candidate.get("example_titles", ""),
        })

    rows.sort(key=lambda r: -r["enhanced_corroboration_count"])

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    pd.DataFrame(rows, columns=OUTPUT_COLUMNS).to_csv(OUTPUT_PATH, index=False)
    print(f"Wrote {len(rows)} corroboration row(s) to {OUTPUT_PATH}.")
    elevated = [r for r in rows if r["enhanced_corroboration_count"] > r["base_corroboration_count"]]
    print(f"  {len(elevated)} candidate(s) elevated beyond Phase B's own score by persistence, anomaly, or peer corroboration.")
    for r in rows[:10]:
        print(f"  [{r['enhanced_corroboration_count']}] \"{r['candidate_phrase']}\" (base {r['base_corroboration_count']}, seen in {r['runs_seen_in']} run(s), anomaly={r['anomaly_signal']}, peer={r['peer_signal']})")


if __name__ == "__main__":
    main()
