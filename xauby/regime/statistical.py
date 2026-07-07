"""Independent statistical regime model used as a cross-check.

The rule-based classifier (``xauby.regime.classifier``) applies hand-tuned
thresholds. This module fits an *unsupervised* Gaussian mixture over the
(trend, volatility) feature plane and reports which cluster the latest bar
belongs to, with a posterior probability and the cluster's empirical
persistence. It exists to answer one question at runtime: does an independent,
data-driven model agree with the rule-based label?

It never places orders and never overrides the rule-based regime — the engine
attaches its output as ``stat_*`` cross-check fields only, gated behind the
``architecture.regime_statistical_crosscheck`` flag (default off). Keeping it in
its own module means ``xauby.regime.classifier`` stays pure-stdlib; numpy (a hard
dependency) is imported only on this optional path.

Design notes:
  - 2 features — trend (rolling log-return sum) and log realized volatility —
    because item-1 validation showed the regimes separate volatility strongly
    and direction weakly, so those are the axes worth modelling.
  - Diagonal-covariance GMM fit by EM in log-space (deterministic seed, capped
    iterations, variance floor) — no scipy/sklearn dependency.
  - Clusters are labelled from their standardised centroids into coarse
    families (UP / DOWN / RANGE / HIGH_VOL) so they can be compared to the
    rule-based regime's family.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Coarse families the statistical clusters and the rule-based regimes both map
# into, so the two can be compared on equal footing.
FAMILY_UP = "UP"
FAMILY_DOWN = "DOWN"
FAMILY_RANGE = "RANGE"
FAMILY_HIGH_VOL = "HIGH_VOL"

# Rule-based regime -> coarse family (mirrors the intent of the router mapping).
_RULE_FAMILY: Dict[str, str] = {
    "BULL_BREAKOUT": FAMILY_UP,
    "BULL_TREND_STRONG": FAMILY_UP,
    "BULL_TREND_WEAK": FAMILY_UP,
    "BEAR_BREAKDOWN": FAMILY_DOWN,
    "BEAR_TREND_STRONG": FAMILY_DOWN,
    "BEAR_TREND_WEAK": FAMILY_DOWN,
    "PANIC_SELL": FAMILY_HIGH_VOL,
    "VOLATILITY_EXPANSION": FAMILY_HIGH_VOL,
    "SIDEWAYS_CHOP": FAMILY_RANGE,
    "LOW_VOL_RANGE": FAMILY_RANGE,
    "LOW_VOL_ACCUMULATION": FAMILY_RANGE,
    "UNKNOWN": "",
}

# Cluster labelling uses the RAW centroid, not the standardised one: EM clusters
# in standardised space (so it can find structure regardless of scale), but the
# family must reflect the *absolute* character. A uniformly-bullish window has
# near-zero standardised trend (it is "trendless relative to itself"), so only
# the raw drift sign tells UP from DOWN. The trend deadband is set as a fraction
# of the window's trend dispersion; a cluster inside the band is direction-less.
_TREND_DEADBAND_FRAC = 0.25
_VOL_HIGH_FRAC = 0.5

DEFAULT_COMPONENTS = 3
DEFAULT_MIN_BARS = 200
_FEATURE_WINDOW = 20
_MAX_ITER = 60
_VAR_FLOOR = 1e-6
_SEED = 12345


@dataclass
class StatisticalRegime:
    available: bool
    label: str = "STAT_UNKNOWN"
    family: str = ""
    cluster: int = -1
    posterior: float = 0.0        # P(assigned cluster | latest bar)
    stay_prob: float = 0.0        # empirical persistence of the assigned cluster
    n_components: int = 0
    converged: bool = False
    centroid: List[float] = field(default_factory=list)  # [trend_z, logvol_z]
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "label": self.label,
            "family": self.family,
            "cluster": self.cluster,
            "posterior": round(self.posterior, 4),
            "stay_prob": round(self.stay_prob, 4),
            "n_components": self.n_components,
            "converged": self.converged,
            "reason": self.reason,
        }


def rule_family(regime: str) -> str:
    """Coarse family of a rule-based regime label ('' if unknown)."""
    return _RULE_FAMILY.get(str(regime), "")


def _feature_matrix(closes: np.ndarray, window: int = _FEATURE_WINDOW) -> Optional[np.ndarray]:
    """Rows of [rolling log-return sum (trend), log rolling volatility]."""
    closes = np.asarray(closes, dtype=float)
    closes = closes[np.isfinite(closes) & (closes > 0)]
    if closes.size < window + 2:
        return None
    logret = np.diff(np.log(closes))
    n = logret.size
    if n < window + 1:
        return None
    rows = []
    for end in range(window, n + 1):
        seg = logret[end - window:end]
        trend = float(seg.sum())
        vol = float(seg.std(ddof=1)) if seg.size > 1 else 0.0
        rows.append((trend, np.log(vol + 1e-9)))
    return np.asarray(rows, dtype=float)


def _standardise(x: np.ndarray) -> np.ndarray:
    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std = np.where(std < 1e-12, 1.0, std)
    return (x - mean) / std


def _log_gaussian_diag(x: np.ndarray, mean: np.ndarray, var: np.ndarray) -> np.ndarray:
    """Log N(x | mean, diag(var)) for each row of x. Returns shape (n,)."""
    var = np.maximum(var, _VAR_FLOOR)
    diff = x - mean
    return -0.5 * (
        x.shape[1] * np.log(2.0 * np.pi)
        + np.sum(np.log(var))
        + np.sum(diff * diff / var, axis=1)
    )


def _fit_gmm(x: np.ndarray, k: int):
    """Diagonal-covariance GMM via EM. Returns (weights, means, vars, resp, converged)."""
    rng = np.random.default_rng(_SEED)
    n, d = x.shape
    # Deterministic init: means at evenly spaced quantiles of the data order.
    order = np.argsort(x[:, 0])
    idx = np.linspace(0, n - 1, k).round().astype(int)
    means = x[order[idx]].copy()
    # Nudge duplicates apart so components don't collapse immediately.
    means += rng.normal(0, 1e-3, means.shape)
    var = np.tile(x.var(axis=0) + _VAR_FLOOR, (k, 1))
    weights = np.full(k, 1.0 / k)

    prev_ll = -np.inf
    converged = False
    resp = np.full((n, k), 1.0 / k)
    for _ in range(_MAX_ITER):
        # E-step (log-space).
        log_resp = np.empty((n, k))
        for j in range(k):
            log_resp[:, j] = np.log(weights[j] + 1e-12) + _log_gaussian_diag(x, means[j], var[j])
        max_lr = log_resp.max(axis=1, keepdims=True)
        log_norm = max_lr[:, 0] + np.log(np.exp(log_resp - max_lr).sum(axis=1))
        resp = np.exp(log_resp - log_norm[:, None])
        ll = float(log_norm.sum())
        # M-step.
        nk = resp.sum(axis=0) + 1e-12
        weights = nk / n
        for j in range(k):
            means[j] = (resp[:, j, None] * x).sum(axis=0) / nk[j]
            diff = x - means[j]
            var[j] = (resp[:, j, None] * diff * diff).sum(axis=0) / nk[j]
        var = np.maximum(var, _VAR_FLOOR)
        if abs(ll - prev_ll) < 1e-5 * max(1.0, abs(prev_ll)):
            converged = True
            break
        prev_ll = ll
    return weights, means, var, resp, converged


def _label_cluster(raw_centroid: np.ndarray, feats: np.ndarray) -> Tuple[str, str]:
    """Map a cluster's RAW centroid [trend_sum, logvol] to (label, family).

    ``feats`` is the full raw feature matrix, used to derive window-relative
    references: the trend deadband (drift indistinguishable from zero) and the
    high-volatility cutoff.
    """
    trend_raw, vol_raw = float(raw_centroid[0]), float(raw_centroid[1])
    trend_scale = float(feats[:, 0].std()) or 1e-9
    deadband = _TREND_DEADBAND_FRAC * trend_scale
    if trend_raw > deadband:
        return "STAT_TREND_UP", FAMILY_UP
    if trend_raw < -deadband:
        return "STAT_TREND_DOWN", FAMILY_DOWN
    vol_cut = float(np.median(feats[:, 1])) + _VOL_HIGH_FRAC * float(feats[:, 1].std())
    if vol_raw >= vol_cut:
        return "STAT_HIGH_VOL", FAMILY_HIGH_VOL
    return "STAT_RANGE", FAMILY_RANGE


def classify_statistical(
    prices: List[Dict[str, Any]],
    *,
    components: int = DEFAULT_COMPONENTS,
    min_bars: int = DEFAULT_MIN_BARS,
) -> StatisticalRegime:
    """Fit the GMM over the window and classify the latest bar.

    ``prices`` are chronological candles (oldest first) with a 'close' key.
    Degrades to ``available=False`` (never raises) when data is insufficient or
    the fit is degenerate, so the caller can safely treat it as advisory.
    """
    try:
        closes = np.asarray([float(p["close"]) for p in prices], dtype=float)
    except (KeyError, TypeError, ValueError):
        return StatisticalRegime(available=False, reason="bad_input")

    if closes.size < min_bars:
        return StatisticalRegime(
            available=False, reason=f"insufficient_bars<{min_bars}"
        )

    feats = _feature_matrix(closes)
    if feats is None or feats.shape[0] < max(components * 5, 30):
        return StatisticalRegime(available=False, reason="insufficient_features")

    k = max(2, min(int(components), feats.shape[0] // 5))
    x = _standardise(feats)
    try:
        weights, means, var, resp, converged = _fit_gmm(x, k)
    except (np.linalg.LinAlgError, FloatingPointError, ValueError):
        return StatisticalRegime(available=False, reason="fit_failed")

    assign = resp.argmax(axis=1)
    latest = int(assign[-1])
    posterior = float(resp[-1, latest])
    # Label from the assigned cluster's RAW centroid (absolute character), not the
    # standardised EM mean (which is window-relative and loses drift sign).
    raw_centroid = feats[assign == latest].mean(axis=0)
    label, family = _label_cluster(raw_centroid, feats)

    # Empirical persistence: P(stay in the latest cluster) over the assignment
    # sequence — the data-driven analogue of the debounce stay-probability.
    trans_from = int(np.sum(assign[:-1] == latest))
    if trans_from > 0:
        stays = int(np.sum((assign[:-1] == latest) & (assign[1:] == latest)))
        stay_prob = stays / trans_from
    else:
        stay_prob = 0.0

    return StatisticalRegime(
        available=True,
        label=label,
        family=family,
        cluster=latest,
        posterior=posterior,
        stay_prob=stay_prob,
        n_components=k,
        converged=converged,
        centroid=[float(raw_centroid[0]), float(raw_centroid[1])],
        reason="ok",
    )


def crosscheck_features(rule_regime: str, stat: StatisticalRegime) -> Dict[str, Any]:
    """Build the ``stat_*`` fields to merge into a GoldRegime.features dict.

    ``stat_agreement`` is True/False when both families are known and comparable,
    else None (encoded as the string 'unknown' so it survives JSON/round-trip).
    """
    out: Dict[str, Any] = {
        "stat_available": stat.available,
        "stat_label": stat.label,
        "stat_family": stat.family,
        "stat_posterior": round(stat.posterior, 4),
        "stat_stay_prob": round(stat.stay_prob, 4),
        "stat_components": stat.n_components,
        "stat_converged": stat.converged,
    }
    rfam = rule_family(rule_regime)
    if stat.available and rfam and stat.family:
        out["stat_agreement"] = bool(rfam == stat.family)
        out["stat_rule_family"] = rfam
    else:
        out["stat_agreement"] = "unknown"
        out["stat_rule_family"] = rfam
    return out
