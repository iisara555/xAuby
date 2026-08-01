"""Equivalence tests for ``ctx_window_bars`` in the plugin replay path.

Bounding the per-bar MarketContext window is what makes a 15m replay feasible:
``ContextBuilder.build`` copies the whole prefix on every bar, so an unbounded
replay is quadratic in bar count. That is invisible at 4h (~10k bars) and fatal
at 15m (~200k bars).

The optimisation is only legitimate if it changes nothing. These tests pin that:
for a window at or above a strategy's structural lookback, the windowed replay
must reproduce the unbounded replay exactly. They also pin the failure mode —
a window BELOW the structural lookback is allowed to differ, which is why the
window is a caller's explicit choice and never a silent default.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.backtest.replay import run_plugin_replay
from xauby.observability.replay import ContextBuilder
from xauby.observability.replay_validation import load_bot_config
from xauby.runtime.trading_config import split_legacy_runtime_config

# Above every structural lookback on the 15m slate: supertrend_ema200's EMA200
# is the deepest, and it demonstrably still moves at 400 (see
# test_window_below_lookback_may_differ).
SAFE_WINDOW = 750

COMPARE_KEYS = (
    "total_trades",
    "net_profit_pct",
    "profit_factor",
    "max_drawdown_pct",
    "win_rate",
)


def _synthetic_15m(n: int = 3000, seed: int = 7) -> pd.DataFrame:
    """Deterministic random-walk OHLCV on a 15m grid."""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    return pd.DataFrame(
        {
            "open": np.concatenate([[close[0]], close[:-1]]),
            "high": close * (1 + np.abs(rng.normal(0, 0.002, n))),
            "low": close * (1 - np.abs(rng.normal(0, 0.002, n))),
            "close": close,
            "volume": rng.uniform(500, 2000, n),
            "timestamp": 1_600_000_000 + np.arange(n) * 900,
        }
    )


def _replay(strategy: str, df: pd.DataFrame, window):
    cfg = load_bot_config()
    strat_cfg, trading_cfg = split_legacy_runtime_config(
        cfg, strategy, symbol="SOLUSDT", for_live=False
    )
    merged = dict(cfg)
    merged["trading"] = trading_cfg
    return run_plugin_replay(
        df,
        strategy_config={**strat_cfg, "enable_short": True},
        engine_config=merged,
        symbol="SOLUSDT",
        strategy_name=strategy,
        primary_timeframe="15m",
        regime_timeframe=None,
        ctx_window_bars=window,
    )


def _metrics(stats) -> tuple:
    return tuple(round(float(stats.get(k) or 0.0), 8) for k in COMPARE_KEYS)


class TestContextBuilderWindow(unittest.TestCase):
    def test_window_none_is_unbounded(self):
        df = _synthetic_15m(500)
        ctx = ContextBuilder.build(
            symbol="SOLUSDT", timeframe_primary="15m", df_primary=df,
            current_price=float(df["close"].iloc[-1]), end_index=399,
        )
        self.assertEqual(len(ctx.df_primary), 400)

    def test_window_caps_history_and_reindexes(self):
        df = _synthetic_15m(500)
        ctx = ContextBuilder.build(
            symbol="SOLUSDT", timeframe_primary="15m", df_primary=df,
            current_price=float(df["close"].iloc[-1]), end_index=399,
            window_bars=120,
        )
        self.assertEqual(len(ctx.df_primary), 120)
        # The strategy must see an ordinary 0-based frame, not one indexed at 280.
        self.assertEqual(list(ctx.df_primary.index), list(range(120)))
        # And it must be the MOST RECENT 120 bars, ending on the same bar.
        self.assertEqual(
            int(ctx.df_primary["timestamp"].iloc[-1]), int(df["timestamp"].iloc[399])
        )

    def test_window_larger_than_history_is_harmless(self):
        df = _synthetic_15m(500)
        ctx = ContextBuilder.build(
            symbol="SOLUSDT", timeframe_primary="15m", df_primary=df,
            current_price=float(df["close"].iloc[-1]), end_index=99,
            window_bars=10_000,
        )
        self.assertEqual(len(ctx.df_primary), 100)


class TestWindowedReplayEquivalence(unittest.TestCase):
    """A safe window must not move a single metric."""

    def test_squeeze_momentum(self):
        df = _synthetic_15m(3000)
        self.assertEqual(
            _metrics(_replay("squeeze_momentum", df, None)),
            _metrics(_replay("squeeze_momentum", df, SAFE_WINDOW)),
        )

    def test_bbrsi_mean_reversion(self):
        df = _synthetic_15m(3000)
        self.assertEqual(
            _metrics(_replay("bbrsi_mean_reversion", df, None)),
            _metrics(_replay("bbrsi_mean_reversion", df, SAFE_WINDOW)),
        )

    def test_supertrend_ema200(self):
        df = _synthetic_15m(3000)
        self.assertEqual(
            _metrics(_replay("supertrend_ema200", df, None)),
            _metrics(_replay("supertrend_ema200", df, SAFE_WINDOW)),
        )

    def test_trades_actually_happened(self):
        """Guard against the tests above passing on two empty replays."""
        stats = _replay("squeeze_momentum", _synthetic_15m(3000), SAFE_WINDOW)
        self.assertGreater(int(stats.get("total_trades") or 0), 0)


class TestWindowFloorIsReal(unittest.TestCase):
    def test_window_below_lookback_may_differ(self):
        """EMA200 has long memory: a 200-bar window truncates it and results move.

        This is the documented reason ``ctx_window_bars`` defaults to None and
        must be chosen per slate rather than guessed.
        """
        df = _synthetic_15m(3000)
        full = _metrics(_replay("supertrend_ema200", df, None))
        truncated = _metrics(_replay("supertrend_ema200", df, 200))
        self.assertNotEqual(full, truncated)


if __name__ == "__main__":
    unittest.main()
