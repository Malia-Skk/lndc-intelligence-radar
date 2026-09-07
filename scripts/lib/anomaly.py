"""
Phase 6 — anomaly detection core.

One deliberately simple, well-understood method used everywhere in this
project rather than a different, harder-to-validate technique per domain:
for a series of (period, value) points, is the MOST RECENT period-over-
period change unusual relative to that same series' own historical
changes? Implemented as a z-score against the mean/std of prior changes,
not a fancier time-series model.

Why simple: most of the individual series this gets applied to (a single
country/indicator pair, a single reporter/product/flow triple) have only
a handful of data points -- 6-8 years at most. A sophisticated model
fit to 6 points is not more rigorous than a z-score, it's just harder to
audit and more likely to be silently wrong. This mirrors the same
reasoning that chose Fitness-Complexity over Method of Reflections in
Phase 2 (simpler and more robust wins over fancier and less verifiable,
for small-N data) and the assumption-tagged stage weights in Phase 4
(an honest simple number beats a falsely-precise complex one).

This is explicitly a SCREENING HEURISTIC, not a statistical hypothesis
test -- with as few as 4-5 prior changes, a z-score is indicative, not
rigorous. Never present a single flagged series as proof something is
wrong; it's a "worth a human look" signal, nothing stronger.
"""
import numpy as np


def compute_changes(values):
    return [values[i] - values[i - 1] for i in range(1, len(values))]


def evaluate_latest_anomaly(periods, values, z_threshold=2.0, min_prior_changes=3):
    """
    periods, values: parallel lists, sorted ascending by period (e.g. by year).

    Returns a dict evaluating whether the LATEST period-over-period change
    is unusual relative to this series' own prior changes, or None if
    there isn't enough history to evaluate meaningfully (fewer than
    min_prior_changes prior changes available) -- returning None here
    rather than a low-confidence number is the point: a z-score computed
    from one or two data points isn't a meaningful statistic, and
    presenting one as if it were would overclaim confidence this project
    has avoided everywhere else.
    """
    if len(periods) != len(values):
        raise ValueError("periods and values must be the same length")
    if len(values) < min_prior_changes + 2:
        return None

    changes = compute_changes(values)
    latest_change = changes[-1]
    prior_changes = changes[:-1]

    if len(prior_changes) < min_prior_changes:
        return None

    mean_prior = float(np.mean(prior_changes))
    std_prior = float(np.std(prior_changes, ddof=1)) if len(prior_changes) > 1 else 0.0

    if std_prior == 0:
        # Every prior change was identical (e.g. a perfectly flat series).
        # A z-score is undefined (division by zero) -- flag directly if the
        # latest change differs from that constant at all, rather than
        # silently skipping a genuinely unusual break from a flat pattern.
        z_score = None
        is_anomaly = latest_change != mean_prior
    else:
        z_score = (latest_change - mean_prior) / std_prior
        is_anomaly = abs(z_score) >= z_threshold

    return {
        "latest_period": periods[-1],
        "latest_value": values[-1],
        "latest_change": latest_change,
        "prior_change_mean": round(mean_prior, 6),
        "prior_change_std": round(std_prior, 6),
        "z_score": round(z_score, 3) if z_score is not None else None,
        "is_anomaly": bool(is_anomaly),
        "n_prior_changes": len(prior_changes),
    }
