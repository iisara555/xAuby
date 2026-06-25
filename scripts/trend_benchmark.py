"""Benchmark donchian_trend vs cdc_action_zone across market regimes.

Tests three market conditions verified by classify_market:
  1. BULL_TREND_STRONG  — strong uptrend
  2. BULL_BREAKOUT      — volatile breakout trend
  3. SIDEWAYS_CHOP      — range-bound (control group)

5 random seeds per condition, 800 bars each.
"""
from __future__ import annotations

import sys
from collections import Counter

import numpy as np
import pandas as pd

PYTHONPATH_HINT = "/home/user/Binance_Cryptonice_lite"
if PYTHONPATH_HINT not in sys.path:
    sys.path.insert(0, PYTHONPATH_HINT)

from xauby.backtest.replay import run_plugin_replay
from xauby.regime.classifier import classify_market

BARS = 800
BASE = 100.0
SEEDS = [7, 21, 42, 99, 123]
STRATEGIES = ["donchian_trend", "cdc_action_zone"]


# ── synthetic data generators ────────────────────────────────────────────────

def _make_df(opens, closes, seed):
    rng = np.random.default_rng(seed)
    n = len(closes)
    spread = np.abs(rng.normal(0, 0.4, n))
    return pd.DataFrame({
        "open_time": ((np.arange(n) * 4 * 3600 + 1_700_000_000) * 1000).astype("int64"),
        "open":   opens,
        "high":   np.maximum(opens, closes) + spread,
        "low":    np.minimum(opens, closes) - spread,
        "close":  closes,
        "volume": rng.uniform(800, 1400, n),
    })


def bull_trend_df(seed: int) -> pd.DataFrame:
    """Strong exponential uptrend with small pullbacks (BULL_TREND_STRONG)."""
    rng = np.random.default_rng(seed)
    p = np.empty(BARS + 1); p[0] = BASE
    for i in range(1, BARS + 1):
        drift = 0.0012
        p[i] = p[i-1] * (1 + drift + rng.normal(0, 0.008))
    return _make_df(p[:-1], p[1:], seed)


def breakout_df(seed: int) -> pd.DataFrame:
    """Choppy base then explosive move (BULL_BREAKOUT)."""
    rng = np.random.default_rng(seed)
    p = np.empty(BARS + 1); p[0] = BASE
    breakout_bar = BARS // 3
    for i in range(1, BARS + 1):
        if i < breakout_bar:
            drift = rng.normal(0, 0.006)
        else:
            drift = 0.0018 + rng.normal(0, 0.012)
        p[i] = p[i-1] * (1 + drift)
    return _make_df(p[:-1], p[1:], seed)


def chop_df(seed: int) -> pd.DataFrame:
    """Mean-reverting range (SIDEWAYS_CHOP) — control group."""
    rng = np.random.default_rng(seed)
    p = np.empty(BARS + 1); p[0] = BASE
    for i in range(1, BARS + 1):
        p[i] = p[i-1] + 0.12 * (BASE - p[i-1]) + rng.normal(0, 0.45)
    return _make_df(p[:-1], p[1:], seed)


GENERATORS = {
    "BULL_TREND_STRONG": bull_trend_df,
    "BULL_BREAKOUT":     breakout_df,
    "SIDEWAYS_CHOP":     chop_df,
}


def regime_dist(df: pd.DataFrame, window=250, step=10) -> Counter:
    records = df[["open","high","low","close","volume"]].to_dict("records")
    c: Counter = Counter()
    for end in range(window, len(records)+1, step):
        c[classify_market(records[end-window:end], timeframe="4h").regime] += 1
    return c


# ── benchmark runner ─────────────────────────────────────────────────────────

def run_strat(df, strat):
    try:
        return run_plugin_replay(
            df.copy(), {}, {}, symbol="BENCHUSDT",
            strategy_name=strat, initial_balance=10_000.0,
        )
    except Exception as e:
        print(f"  [ERR] {strat}: {e}", file=sys.stderr)
        return None


def bench(condition, gen):
    print(f"\n── {condition} ──")
    dfs = {s: gen(s) for s in SEEDS}

    print("Regime check:")
    for seed, df in dfs.items():
        dist = regime_dist(df)
        total = sum(dist.values())
        target_pct = 100 * (dist.get(condition, 0) +
                            dist.get("BULL_TREND_WEAK", 0) * (condition != "SIDEWAYS_CHOP")) / max(total,1)
        print(f"  seed {seed}: {dict(dist)}")

    rows = {}
    for strat in STRATEGIES:
        nets, wins, dds, pfs = [], [], [], []
        for seed, df in dfs.items():
            s = run_strat(df, strat)
            if s is None:
                continue
            nets.append(float(s.get("net_profit_pct", 0)))
            wins.append(float(s.get("win_rate_pct", s.get("win_rate", 0))))
            dds.append(float(s.get("max_drawdown_pct", s.get("max_dd_pct", 0))))
            pf = float(s.get("profit_factor", 0))
            pfs.append(pf if np.isfinite(pf) else 0)
        rows[strat] = {
            "net_avg": np.mean(nets) if nets else 0,
            "net_min": np.min(nets) if nets else 0,
            "win":     np.mean(wins) if wins else 0,
            "dd":      np.mean(dds)  if dds  else 0,
            "pf":      np.mean(pfs)  if pfs  else 0,
        }

    hdr = f"{'strategy':<22} {'net_avg':>8} {'net_min':>8} {'win%':>6} {'max_dd':>7} {'PF':>6}"
    sep = "-" * len(hdr)
    print(f"\n{hdr}\n{sep}")
    for strat in STRATEGIES:
        r = rows[strat]
        flag = " ◀ winner" if r["net_avg"] == max(v["net_avg"] for v in rows.values()) else ""
        print(f"{strat:<22} {r['net_avg']:>8.2f} {r['net_min']:>8.2f} {r['win']:>6.1f} {r['dd']:>7.2f} {r['pf']:>6.2f}{flag}")
    return rows


def main():
    all_results = {}
    for cond, gen in GENERATORS.items():
        all_results[cond] = bench(cond, gen)

    print("\n\n══ OVERALL VERDICT ══")
    for cond, rows in all_results.items():
        winner = max(rows, key=lambda s: rows[s]["net_avg"])
        d = rows["donchian_trend"]["net_avg"] - rows["cdc_action_zone"]["net_avg"]
        sign = "donchian_trend WINS" if d > 0 else "cdc_action_zone WINS"
        print(f"  {cond:<22}: {sign}  (Δnet = {d:+.2f}%)")


if __name__ == "__main__":
    main()
