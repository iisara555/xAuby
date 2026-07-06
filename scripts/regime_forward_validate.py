#!/usr/bin/env python3
"""Validate that the regime classifier separates the REAL market.

The regime-fit benchmark (scripts/regime_fit_benchmark.py) checks that
synthetic data engineered to look like regime X gets *labelled* X. That is a
self-consistency test, not proof that a regime means anything. This script
answers the professional question instead:

  "Conditioned on the regime label, are the forward-return and realized-vol
   distributions actually different — and are regimes persistent enough to
   trade?"

It drives the REAL production classifier (xauby.regime.classifier.classify_market)
over historical candles with the same 250-candle window the engine feeds it,
then, for every distinct regime label, measures:

  - drift  : mean of the next 1-bar returns while in that regime (bps/bar)
  - vol    : stdev of those returns  -> the regime's realized volatility
  - sharpe : drift / vol  (per-bar, unannualised)
  - fwdH   : median cumulative return H bars ahead (the tradeable edge)
  - hit%   : P(fwdH return > 0)
  - run    : mean consecutive-candle run length (persistence)
  - t1/2   : implied half-life from the label's stay-probability

Then a summary judges the classifier as a whole:

  - persistence : mean run length + raw flip rate (churn)
  - separation  : eta^2 = fraction of 1-bar-return variance explained by the
                  regime label, with a permutation p-value (does regime matter
                  more than a random relabelling would?)
  - ordering    : do bull-family regimes actually out-drift bear-family ones?

Data source: real OHLCV CSV (columns open,high,low,close,volume; optional
open_time/timestamp), e.g. produced by scripts/fetch_global_klines.py:

    python scripts/fetch_global_klines.py --symbol PAXGUSDT --timeframe 4h --years 4
    python scripts/regime_forward_validate.py \
        --csv core/backtest_candles_4h_paxgusdt_full.csv --timeframe 4h

Everything is pure stdlib so it runs offline with no numpy/pandas.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import random
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from xauby.regime.classifier import classify_market  # noqa: E402

WINDOW = 250  # candles fed to the classifier each tick (matches engine limit)

# Regime families for the directional-ordering sanity check.
BULL_FAMILY = {"BULL_BREAKOUT", "BULL_TREND_STRONG", "BULL_TREND_WEAK"}
BEAR_FAMILY = {"BEAR_BREAKDOWN", "BEAR_TREND_STRONG", "BEAR_TREND_WEAK", "PANIC_SELL"}
NEUTRAL_FAMILY = {
    "SIDEWAYS_CHOP", "VOLATILITY_EXPANSION",
    "LOW_VOL_RANGE", "LOW_VOL_ACCUMULATION",
}


# ── data loading ──────────────────────────────────────────────────────────────

def load_csv(path: str) -> List[Dict[str, float]]:
    rows: List[Dict[str, float]] = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            try:
                rows.append({
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": float(r.get("volume", 0) or 0),
                })
            except (KeyError, TypeError, ValueError):
                continue
    return rows


# ── small stats helpers (stdlib only) ─────────────────────────────────────────

def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _stdev(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _median(xs: Sequence[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else 0.5 * (s[mid - 1] + s[mid])


def _hit_rate(xs: Sequence[float]) -> float:
    return sum(1 for x in xs if x > 0) / len(xs) if xs else 0.0


# ── per-regime aggregation ────────────────────────────────────────────────────

@dataclass
class RegimeStats:
    label: str
    n: int = 0
    bar_returns: List[float] = field(default_factory=list)   # next 1-bar returns
    fwd_returns: List[float] = field(default_factory=list)   # H-bar cumulative returns
    runs: List[int] = field(default_factory=list)            # consecutive-run lengths

    @property
    def drift_bps(self) -> float:
        return _mean(self.bar_returns) * 1e4

    @property
    def vol_pct(self) -> float:
        return _stdev(self.bar_returns) * 100.0

    @property
    def sharpe(self) -> float:
        v = _stdev(self.bar_returns)
        return _mean(self.bar_returns) / v if v > 0 else 0.0

    @property
    def fwd_med_pct(self) -> float:
        return _median(self.fwd_returns) * 100.0

    @property
    def fwd_mean_pct(self) -> float:
        return _mean(self.fwd_returns) * 100.0

    @property
    def hit_pct(self) -> float:
        return _hit_rate(self.fwd_returns) * 100.0

    @property
    def avg_run(self) -> float:
        return _mean(self.runs)


@dataclass
class Validation:
    symbol: str
    timeframe: str
    horizon: int
    candles: int
    labels: List[str]                 # regime label per classified candle (in order)
    stats: Dict[str, RegimeStats]
    stay_prob: Dict[str, float]       # P(next label == this label) per regime
    raw_flips: int
    eta_sq_dir: float                 # separation of the MEAN return (directional edge)
    perm_p_dir: float
    eta_sq_vol: float                 # separation of |return| (volatility state)
    perm_p_vol: float


def _bar_return(prev_close: float, close: float) -> float:
    return (close - prev_close) / prev_close if prev_close > 0 else 0.0


def classify_series(
    series: List[Dict[str, float]],
    *,
    timeframe: str,
    horizon: int,
    step: int,
) -> Tuple[List[str], List[int]]:
    """Return (label_per_index, index_of_each_label) walking the series."""
    labels: List[str] = []
    idxs: List[int] = []
    for i in range(WINDOW, len(series), step):
        window = series[i - WINDOW:i]
        reg = classify_market(window, indicators={}, macro_state={}, timeframe=timeframe)
        labels.append(reg.regime)
        idxs.append(i)
    return labels, idxs


def _run_lengths(labels: Sequence[str]) -> Dict[str, List[int]]:
    runs: Dict[str, List[int]] = defaultdict(list)
    if not labels:
        return runs
    cur = labels[0]
    length = 1
    for lab in labels[1:]:
        if lab == cur:
            length += 1
        else:
            runs[cur].append(length)
            cur, length = lab, 1
    runs[cur].append(length)
    return runs


def _eta_squared(groups: Dict[str, List[float]]) -> float:
    """Fraction of return variance explained by the regime label (0..1)."""
    allv = [v for vs in groups.values() for v in vs]
    if len(allv) < 2:
        return 0.0
    grand = _mean(allv)
    ss_total = sum((v - grand) ** 2 for v in allv)
    if ss_total <= 0:
        return 0.0
    ss_between = sum(len(vs) * (_mean(vs) - grand) ** 2 for vs in groups.values() if vs)
    return max(0.0, min(1.0, ss_between / ss_total))


def _permutation_p(
    label_seq: Sequence[str],
    value_seq: Sequence[float],
    observed_eta: float,
    *,
    trials: int,
    seed: int,
) -> float:
    """P(eta^2 of a random relabelling >= observed). Low p => regime matters."""
    if len(value_seq) < 2 or observed_eta <= 0:
        return 1.0
    rng = random.Random(seed)
    values = list(value_seq)
    labels = list(label_seq)
    ge = 0
    for _ in range(trials):
        shuffled = labels[:]
        rng.shuffle(shuffled)
        groups: Dict[str, List[float]] = defaultdict(list)
        for lab, val in zip(shuffled, values):
            groups[lab].append(val)
        if _eta_squared(groups) >= observed_eta:
            ge += 1
    return (ge + 1) / (trials + 1)


def validate(
    series: List[Dict[str, float]],
    *,
    symbol: str,
    timeframe: str,
    horizon: int,
    step: int,
    perm_trials: int,
    seed: int,
) -> Validation:
    labels, idxs = classify_series(series, timeframe=timeframe, horizon=horizon, step=step)

    stats: Dict[str, RegimeStats] = {}
    grouped_bar: Dict[str, List[float]] = defaultdict(list)
    flat_labels: List[str] = []
    flat_values: List[float] = []

    for lab, i in zip(labels, idxs):
        st = stats.setdefault(lab, RegimeStats(label=lab))
        st.n += 1
        # next 1-bar return (drift / realized vol)
        if i + 1 < len(series):
            br = _bar_return(series[i]["close"], series[i + 1]["close"])
            st.bar_returns.append(br)
            grouped_bar[lab].append(br)
            flat_labels.append(lab)
            flat_values.append(br)
        # H-bar forward cumulative return (tradeable edge)
        if i + horizon < len(series):
            fr = _bar_return(series[i]["close"], series[i + horizon]["close"])
            st.fwd_returns.append(fr)

    for lab, runs in _run_lengths(labels).items():
        if lab in stats:
            stats[lab].runs = runs

    # stay probability from the label transition sequence
    stay_hits: Dict[str, int] = defaultdict(int)
    stay_total: Dict[str, int] = defaultdict(int)
    for a, b in zip(labels, labels[1:]):
        stay_total[a] += 1
        if a == b:
            stay_hits[a] += 1
    stay_prob = {
        lab: (stay_hits[lab] / stay_total[lab] if stay_total[lab] else 0.0)
        for lab in stats
    }

    raw_flips = sum(1 for a, b in zip(labels, labels[1:]) if a != b)

    # Directional separation: how much the regime shifts the MEAN 1-bar return.
    eta_sq_dir = _eta_squared(grouped_bar)
    perm_p_dir = _permutation_p(
        flat_labels, flat_values, eta_sq_dir, trials=perm_trials, seed=seed
    )
    # Volatility separation: how much the regime shifts the DISPERSION of returns
    # (the primary job of a regime detector). Group |return| the same way.
    grouped_abs = {lab: [abs(v) for v in vs] for lab, vs in grouped_bar.items()}
    abs_values = [abs(v) for v in flat_values]
    eta_sq_vol = _eta_squared(grouped_abs)
    perm_p_vol = _permutation_p(
        flat_labels, abs_values, eta_sq_vol, trials=perm_trials, seed=seed + 1
    )

    return Validation(
        symbol=symbol,
        timeframe=timeframe,
        horizon=horizon,
        candles=len(labels),
        labels=labels,
        stats=stats,
        stay_prob=stay_prob,
        raw_flips=raw_flips,
        eta_sq_dir=eta_sq_dir,
        perm_p_dir=perm_p_dir,
        eta_sq_vol=eta_sq_vol,
        perm_p_vol=perm_p_vol,
    )


# ── reporting ─────────────────────────────────────────────────────────────────

def _half_life(stay_p: float) -> float:
    if stay_p <= 0:
        return 0.0
    if stay_p >= 1:
        return float("inf")
    return math.log(0.5) / math.log(stay_p)


def report(v: Validation) -> None:
    total = v.candles or 1
    print(f"\nREGIME FORWARD VALIDATION — {v.symbol} {v.timeframe}")
    print(f"candles classified : {v.candles} (window={WINDOW})")
    print(f"forward horizon    : {v.horizon} bars\n")

    header = (
        f"{'regime':<22}{'%time':>7}{'n':>6}{'drift_bps':>10}{'vol%':>7}"
        f"{'sharpe':>8}{'fwd_med%':>9}{'hit%':>6}{'avg_run':>8}{'t1/2':>7}"
    )
    print(header)
    print("-" * len(header))
    # order by % time descending
    for lab in sorted(v.stats, key=lambda k: -v.stats[k].n):
        st = v.stats[lab]
        hl = _half_life(v.stay_prob.get(lab, 0.0))
        hl_s = "inf" if hl == float("inf") else f"{hl:.1f}"
        print(
            f"{lab:<22}{100.0 * st.n / total:>6.1f}%{st.n:>6}"
            f"{st.drift_bps:>10.1f}{st.vol_pct:>7.2f}{st.sharpe:>8.3f}"
            f"{st.fwd_med_pct:>9.2f}{st.hit_pct:>6.0f}{st.avg_run:>8.1f}{hl_s:>7}"
        )

    # ── summary judgements ────────────────────────────────────────────────────
    flip_pct = 100.0 * v.raw_flips / max(1, v.candles - 1)
    all_runs = [r for st in v.stats.values() for r in st.runs]
    mean_run = _mean(all_runs)

    bull = [st.drift_bps for lab, st in v.stats.items() if lab in BULL_FAMILY and st.bar_returns]
    bear = [st.drift_bps for lab, st in v.stats.items() if lab in BEAR_FAMILY and st.bar_returns]
    # size-weighted family drift
    def _fam_drift(fam: set) -> Optional[float]:
        rets = [r for lab in fam if lab in v.stats for r in v.stats[lab].bar_returns]
        return _mean(rets) * 1e4 if rets else None
    bull_d = _fam_drift(BULL_FAMILY)
    bear_d = _fam_drift(BEAR_FAMILY)

    print("\nSUMMARY")
    print(f"  persistence : mean run = {mean_run:.1f} bars, flip rate = {flip_pct:.1f}% of candles")
    print(f"  vol sep     : eta^2 = {v.eta_sq_vol:.4f} (regime explains "
          f"{100 * v.eta_sq_vol:.1f}% of |return| variance), permutation p = {v.perm_p_vol:.4f}")
    print(f"  dir sep     : eta^2 = {v.eta_sq_dir:.4f} (regime explains "
          f"{100 * v.eta_sq_dir:.1f}% of return-mean variance), permutation p = {v.perm_p_dir:.4f}")
    if bull_d is not None and bear_d is not None:
        ok = "OK" if bull_d > bear_d else "INVERTED"
        print(f"  ordering    : bull-family drift = {bull_d:.1f} bps vs "
              f"bear-family drift = {bear_d:.1f} bps  [{ok}]")
    else:
        print("  ordering    : insufficient bull/bear samples to compare")

    # ── verdict ───────────────────────────────────────────────────────────────
    # A regime detector earns its keep primarily by separating VOLATILITY state
    # (position sizing / stop width) and secondarily by tilting direction. Judge
    # both, and don't call the classifier "noise" just because the 1-bar
    # directional edge is small — that edge is small for everyone.
    verdict: List[str] = []
    if v.perm_p_vol <= 0.05 and v.eta_sq_vol >= 0.02:
        verdict.append(f"strong volatility separation (eta^2 {v.eta_sq_vol:.3f}, p={v.perm_p_vol:.3f})")
    elif v.perm_p_vol <= 0.05:
        verdict.append(f"volatility separation significant but weak (eta^2 {v.eta_sq_vol:.3f})")
    else:
        verdict.append("NO volatility separation — vol states look interchangeable")
    if v.perm_p_dir <= 0.05:
        verdict.append(f"directional tilt is significant (eta^2 {v.eta_sq_dir:.4f})")
    else:
        verdict.append("directional edge is within noise (expected; size on vol, not 1-bar direction)")
    if mean_run >= 3:
        verdict.append(f"persistent (mean run {mean_run:.1f} >= 3 bars)")
    else:
        verdict.append(f"churny (mean run {mean_run:.1f} < 3 bars) — debounce is load-bearing")
    if bull_d is not None and bear_d is not None and bull_d <= bear_d:
        verdict.append("WARNING: bull regimes do not out-drift bear regimes on this asset")
    print("  verdict     : " + "; ".join(verdict))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True, help="real OHLCV CSV (open,high,low,close,volume)")
    ap.add_argument("--symbol", default="", help="label for the report (defaults to filename)")
    ap.add_argument("--timeframe", default="4h")
    ap.add_argument("--horizon", type=int, default=6,
                    help="forward horizon in bars for fwd_med%% / hit%% (default 6)")
    ap.add_argument("--step", type=int, default=1,
                    help="classify every Nth candle (default 1 = every candle)")
    ap.add_argument("--perm-trials", type=int, default=2000,
                    help="permutation trials for the separation p-value")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    series = load_csv(args.csv)
    if len(series) < WINDOW + args.horizon + 2:
        print(f"Not enough candles: need > {WINDOW + args.horizon + 2}, got {len(series)}",
              file=sys.stderr)
        return 1

    symbol = args.symbol or os.path.basename(args.csv)
    print(f"Data source: {args.csv} ({len(series)} bars)  timeframe={args.timeframe}")
    v = validate(
        series,
        symbol=symbol,
        timeframe=args.timeframe,
        horizon=args.horizon,
        step=args.step,
        perm_trials=args.perm_trials,
        seed=args.seed,
    )
    report(v)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
