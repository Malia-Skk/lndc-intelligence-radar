"""
Phase 7 — trend forecasting core.

The gap this fills: everything built through Phase 6 monitors (what's
happening / what happened) or ranks (where things stand right now).
Nothing projects forward. This is what makes "predictive analytics" an
honest description of this system rather than an aspirational one.

Method: ordinary least-squares linear trend, with a genuine prediction
interval (not a decorative one) using the t-distribution critical value
for the fit's actual degrees of freedom -- not the normal-distribution
1.96 shortcut, which meaningfully understates uncertainty at the small
sample sizes this project works with (e.g. df=4 needs t=2.78, not 1.96,
for a 95% interval; the gap matters more the fewer points there are).

Why a straight line, not a more sophisticated model: same reasoning as
Fitness-Complexity over Method of Reflections (Phase 2) and a z-score over
an ARIMA model (Phase 6) -- with a handful to a few dozen historical
points, a fancier model isn't more rigorous, it's just harder to audit and
no more reliable at this scale. A linear trend with an honestly-widening
uncertainty band, extrapolated as far as the target year actually requires
(sometimes several years past the last real data point -- the interval
should say so, loudly, not hide it).

This deliberately does NOT smooth over or exclude "inconvenient" data
points (e.g. a COVID-era shock in a growth series). Excluding real data
because it's awkward would be a worse methodological sin than the wide
interval that results from including it -- the wide interval IS the
honest answer when the underlying series is genuinely volatile.
"""
import numpy as np
from scipy import stats

MIN_POINTS_FOR_TREND = 4


def fit_linear_trend(x, y):
    """x, y: equal-length lists of numbers (x values must be distinct).

    Returns a fit dict, or None if there isn't enough data (fewer than
    MIN_POINTS_FOR_TREND points) to fit a trend with a meaningful
    uncertainty band. Returning None here rather than a number computed
    from too little data is deliberate -- the same reasoning as
    anomaly.py's min_prior_changes floor in Phase 6.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(x) != len(y):
        raise ValueError("x and y must be the same length")
    n = len(x)
    if n < MIN_POINTS_FOR_TREND:
        return None
    if len(set(x.tolist())) != n:
        raise ValueError("x values must be distinct")

    x_mean = float(x.mean())
    y_mean = float(y.mean())
    sxx = float(np.sum((x - x_mean) ** 2))
    if sxx == 0:
        return None  # all x identical -- can't fit a slope (shouldn't happen given the distinctness check above, but defensive)

    slope = float(np.sum((x - x_mean) * (y - y_mean)) / sxx)
    intercept = y_mean - slope * x_mean
    fitted = intercept + slope * x
    residuals = y - fitted
    dof = n - 2
    residual_std = float(np.sqrt(np.sum(residuals ** 2) / dof)) if dof > 0 else 0.0

    return {
        "n": n, "dof": dof, "slope": slope, "intercept": intercept,
        "x_mean": x_mean, "sxx": sxx, "residual_std": residual_std,
        "x_min": float(x.min()), "x_max": float(x.max()),
    }


def predict_with_interval(fit, x0, confidence=0.95):
    """Returns (point_estimate, lower_bound, upper_bound, years_beyond_data)
    for a new x0, or None if fit is None.

    years_beyond_data is how far x0 sits past the actual historical data's
    range -- 0 if x0 falls within the observed range (interpolation), a
    positive number if it's an extrapolation. Callers should surface this
    directly rather than let a projection look equally confident whether
    it's 1 year or 6 years past the last real observation.
    """
    if fit is None:
        return None
    point = fit["intercept"] + fit["slope"] * x0

    years_beyond_data = max(0.0, x0 - fit["x_max"], fit["x_min"] - x0)

    if fit["dof"] <= 0 or fit["residual_std"] == 0:
        return point, point, point, years_beyond_data  # degenerate: perfect fit or no residual dof

    se = fit["residual_std"] * np.sqrt(1 + 1 / fit["n"] + (x0 - fit["x_mean"]) ** 2 / fit["sxx"])
    t_crit = float(stats.t.ppf(1 - (1 - confidence) / 2, fit["dof"]))
    margin = t_crit * se
    return float(point), float(point - margin), float(point + margin), years_beyond_data
