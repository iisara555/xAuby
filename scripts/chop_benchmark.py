"""Benchmark all strategies against synthetic SIDEWAYS_CHOP market data.

Generates range-bound OHLCV series verified by the regime classifier as
predominantly SIDEWAYS_CHOP, then replays every registered strategy through
run_plugin_replay and ranks them by net profit / win rate / drawdown.

Usage: python scripts/chop_benchmark.py
"""
from __future__ import annotations

import sys
from collections import Counter

import numpy as np
import pandas as pd

from xauby.backtest.replay import run_plugin_replay
from xauby.regime.classifier import classify_market
from xauby.strategies.registry import available_strategies

BARS = 800          # ~133 days of 4h candles
BASE_PRICE = 100.0
SEEDS = [7, 21, 42, 99, 123]


def make_chop_df(seed: int, bars: int = BARS) -> pd.DataFrame:
    """Range-bound mean-reverting series (Ornstein-Uhlenbeck style) with
    mild noise so EMAs stay flat and ATR stays normal -> SIDEWAYS_CHOP."""
    rng = np.random.default_rng(seed)
    prices = np.empty(bars + 1)
    prices[0] = BASE_PRICE
    theta, sigma = 0.05, 0.45
    for i in range(1, bars + 1):
        drift = theta * (BASE_PRICE - prices[i - 1])
        prices[i] = prices[i - 1] + drift + rng.normal(0.0, sigma)

    opens = prices[:-1]
    closes = prices[1:]
    spread = np.abs(rng.normal(0.0, 0.35, bars))
    highs = np.maximum(opens, closes) + spread
    lows = np.minimum(opens, closes) - spread
    volume = rng.uniform(800, 1200, bars)
    open_time = (np.arange(bars) * 4 * 3600 + 1_700_000_000) * 1000

    return pd.DataFrame(
        {
            "open_time": open_time.astype("int64"),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volume,
        }
    )


def regime_distribution(df: pd.DataFrame, window: int = 250, step: int = 10) -> Counter:
    counts: Counter = Counter()
    records = df[["open", "high", "low", "close", "volume"]].to_dict("records")
    for end in range(window, len(records) + 1, step):
        reg = classify_market(records[end - window : end], timeframe="4h")
        counts[reg.regime] += 1
    return counts


def main() -> None:
    strategies = available_strategies()
    dfs = {seed: make_chop_df(seed) for seed in SEEDS}

    print("=== Regime verification (sampled windows per seed) ===")
    for seed, df in dfs.items():
        dist = regime_distribution(df)
        total = sum(dist.values())
        chop_pct = 100.0 * (
            dist.get("SIDEWAYS_CHOP", 0) + dist.get("LOW_VOL_RANGE", 0)
        ) / max(total, 1)
        print(f"seed {seed}: {dict(dist)}  range/chop={chop_pct:.0f}%")

    rows = []
    for strat in strategies:
        nets, wins, dds, pfs, trades_n = [], [], [], [], []
        for seed, df in dfs.items():
            try:
                stats = run_plugin_replay(
                    df.copy(),
                    strategy_config={},
                    engine_config={},
                    symbol="CHOPUSDT",
                    strategy_name=strat,
                    initial_balance=10_000.0,
                )
            except Exception as exc:
                print(f"  [skip] {strat} seed {seed}: {exc}", file=sys.stderr)
                continue
            nets.append(float(stats.get("net_profit_pct", 0.0)))
            wins.append(float(stats.get("win_rate_pct", stats.get("win_rate", 0.0))))
            dds.append(float(stats.get("max_drawdown_pct", stats.get("max_dd_pct", 0.0))))
            pfs.append(float(stats.get("profit_factor", 0.0)))
            trades_n.append(int(stats.get("total_trades", stats.get("trades", 0))))
        if not nets:
            continue
        rows.append(
            {
                "strategy": strat,
                "net_pct_avg": np.mean(nets),
                "net_pct_min": np.min(nets),
                "win_rate": np.mean(wins),
                "max_dd": np.mean(dds),
                "profit_factor": np.mean([p for p in pfs if np.isfinite(p)] or [0.0]),
                "trades_avg": np.mean(trades_n),
            }
        )

    out = pd.DataFrame(rows).sort_values("net_pct_avg", ascending=False)
    print("\n=== Strategy ranking on SIDEWAYS_CHOP (avg over seeds) ===")
    print(out.to_string(index=False, float_format=lambda v: f"{v:.2f}"))


if __name__ == "__main__":
    main()
