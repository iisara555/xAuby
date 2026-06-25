"""Benchmark each production strategy in the regime it is mapped to.

For every regime in the router mapping, synthesise market data verified by
classify_market, then replay the *mapped* strategy. This answers: does each
strategy actually perform well in the regime it is responsible for?

Usage: python scripts/regime_fit_benchmark.py
"""
from __future__ import annotations

import sys
from collections import Counter

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/user/Binance_Cryptonice_lite")

from xauby.backtest.replay import run_plugin_replay
from xauby.regime.classifier import classify_market

BARS = 800
BASE = 100.0
SEEDS = [7, 21, 42, 99, 123]

# regime label -> mapped strategy (mirror of bot_config.yaml regime_router.mapping)
MAPPING = {
    "BULL_TREND_STRONG":    "donchian_trend",
    "BULL_BREAKOUT":        "donchian_trend",
    "LOW_VOL_ACCUMULATION": "bbkc_squeeze",
    "VOLATILITY_EXPANSION": "supertrend_ema200",
    "SIDEWAYS_CHOP":        "bbrsi_mean_reversion",
    "BEAR_TREND_WEAK":      "donchian_trend",
}


def _df(opens, closes, seed, spread_mult=0.4):
    rng = np.random.default_rng(seed + 999)
    n = len(closes)
    spread = np.abs(rng.normal(0, spread_mult, n))
    return pd.DataFrame({
        "open_time": ((np.arange(n) * 4 * 3600 + 1_700_000_000) * 1000).astype("int64"),
        "open":   opens,
        "high":   np.maximum(opens, closes) + spread,
        "low":    np.minimum(opens, closes) - spread,
        "close":  closes,
        "volume": rng.uniform(800, 1400, n),
    })


# ── regime-specific data generators ──────────────────────────────────────────

def bull_strong(seed):
    rng = np.random.default_rng(seed)
    p = np.empty(BARS + 1); p[0] = BASE
    for i in range(1, BARS + 1):
        p[i] = p[i - 1] * (1 + 0.0012 + rng.normal(0, 0.008))
    return _df(p[:-1], p[1:], seed)


def bull_breakout(seed):
    rng = np.random.default_rng(seed)
    p = np.empty(BARS + 1); p[0] = BASE
    bo = BARS // 3
    for i in range(1, BARS + 1):
        drift = rng.normal(0, 0.006) if i < bo else 0.0018 + rng.normal(0, 0.012)
        p[i] = p[i - 1] * (1 + drift)
    return _df(p[:-1], p[1:], seed)


def low_vol(seed):
    """Tight range, very low volatility pinned near EMA200 (LOW_VOL_*).

    Needs close within ~0.8% of EMA200 and low ATR, so we keep the level
    almost flat with tiny noise — EMA200 tracks price closely.
    """
    rng = np.random.default_rng(seed)
    p = np.empty(BARS + 1); p[0] = BASE
    for i in range(1, BARS + 1):
        p[i] = p[i - 1] + 0.6 * (BASE - p[i - 1]) + rng.normal(0, 0.05)
    return _df(p[:-1], p[1:], seed, spread_mult=0.03)


def vol_expansion(seed):
    """Wide swings with NO net trend (VOLATILITY_EXPANSION needs trend=NEUTRAL).

    Large alternating moves keep EMAs unaligned (neutral) while range_pct_20
    stays >= 0.035 and volatility_state expands.
    """
    rng = np.random.default_rng(seed)
    p = np.empty(BARS + 1); p[0] = BASE
    direction = 1
    for i in range(1, BARS + 1):
        if i % 8 == 0:
            direction *= -1
        shock = direction * 0.035 + rng.normal(0, 0.02)
        p[i] = max(1.0, p[i - 1] * (1 + shock))
    return _df(p[:-1], p[1:], seed, spread_mult=1.5)


def chop(seed):
    rng = np.random.default_rng(seed)
    p = np.empty(BARS + 1); p[0] = BASE
    for i in range(1, BARS + 1):
        p[i] = p[i - 1] + 0.12 * (BASE - p[i - 1]) + rng.normal(0, 0.45)
    return _df(p[:-1], p[1:], seed)


def bear_weak(seed):
    """Mild persistent downtrend (BEAR_TREND_WEAK)."""
    rng = np.random.default_rng(seed)
    p = np.empty(BARS + 1); p[0] = BASE
    for i in range(1, BARS + 1):
        p[i] = p[i - 1] * (1 - 0.0006 + rng.normal(0, 0.007))
    return _df(p[:-1], p[1:], seed)


GENERATORS = {
    "BULL_TREND_STRONG":    bull_strong,
    "BULL_BREAKOUT":        bull_breakout,
    "LOW_VOL_ACCUMULATION": low_vol,
    "VOLATILITY_EXPANSION": vol_expansion,
    "SIDEWAYS_CHOP":        chop,
    "BEAR_TREND_WEAK":      bear_weak,
}


def regime_match_pct(df, target, window=250, step=10):
    recs = df[["open", "high", "low", "close", "volume"]].to_dict("records")
    c = Counter()
    for end in range(window, len(recs) + 1, step):
        c[classify_market(recs[end - window:end], timeframe="4h").regime] += 1
    total = sum(c.values()) or 1
    # accept the target plus its same-family neighbours
    family = {
        "BULL_TREND_STRONG": {"BULL_TREND_STRONG", "BULL_TREND_WEAK", "BULL_BREAKOUT"},
        "BULL_BREAKOUT":     {"BULL_BREAKOUT", "BULL_TREND_STRONG", "BULL_TREND_WEAK"},
        "LOW_VOL_ACCUMULATION": {"LOW_VOL_ACCUMULATION", "LOW_VOL_RANGE", "SIDEWAYS_CHOP"},
        "VOLATILITY_EXPANSION": {"VOLATILITY_EXPANSION", "BULL_BREAKOUT", "BEAR_BREAKDOWN", "PANIC_SELL"},
        "SIDEWAYS_CHOP":     {"SIDEWAYS_CHOP", "LOW_VOL_RANGE", "BULL_TREND_WEAK", "BEAR_TREND_WEAK"},
        "BEAR_TREND_WEAK":   {"BEAR_TREND_WEAK", "BEAR_TREND_STRONG", "SIDEWAYS_CHOP"},
    }.get(target, {target})
    return 100 * sum(c[r] for r in family) / total, dict(c)


def run(df, strat):
    try:
        return run_plugin_replay(df.copy(), {}, {}, symbol="FITUSDT",
                                 strategy_name=strat, initial_balance=10_000.0)
    except Exception as e:
        print(f"  [ERR] {strat}: {e}", file=sys.stderr)
        return None


def main():
    print(f"{'regime':<22} {'strategy':<22} {'fit%':>5} {'net_avg':>8} {'net_min':>8} {'win%':>6} {'dd':>6} {'PF':>6} {'trades':>7}")
    print("-" * 96)
    for regime, gen in GENERATORS.items():
        strat = MAPPING[regime]
        nets, wins, dds, pfs, trs, fits = [], [], [], [], [], []
        for seed in SEEDS:
            df = gen(seed)
            fit, _ = regime_match_pct(df, regime)
            fits.append(fit)
            s = run(df, strat)
            if s is None:
                continue
            nets.append(float(s.get("net_profit_pct", 0)))
            wins.append(float(s.get("win_rate_pct", s.get("win_rate", 0))))
            dds.append(float(s.get("max_drawdown_pct", s.get("max_dd_pct", 0))))
            pf = float(s.get("profit_factor", 0))
            pfs.append(pf if np.isfinite(pf) else 0)
            trs.append(int(s.get("total_trades", s.get("trades", 0))))
        print(f"{regime:<22} {strat:<22} {np.mean(fits):>5.0f} {np.mean(nets):>8.2f} "
              f"{np.min(nets):>8.2f} {np.mean(wins):>6.1f} {np.mean(dds):>6.2f} "
              f"{np.mean(pfs):>6.2f} {np.mean(trs):>7.1f}")


if __name__ == "__main__":
    main()
