"""Tests for the Donchian trend-following strategy."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.strategies.context import MarketContext
from xauby.strategies.donchian_trend.strategy import DonchianTrendStrategy, PARAM_GRID, TARGET_REGIMES
from xauby.strategies.registry import available_strategies, load_strategy


def _uptrend_breakout_df(n: int = 300) -> pd.DataFrame:
    rows = []
    for i in range(n):
        base = 100.0 + i * 0.1
        if i == n - 1:
            # Strong breakout candle above the prior channel high.
            rows.append({"open": base, "high": base + 8.0, "low": base - 0.3, "close": base + 7.0, "volume": 2500.0})
        else:
            rows.append({"open": base, "high": base + 0.4, "low": base - 0.4, "close": base + 0.1, "volume": 1000.0})
    return pd.DataFrame(rows)


def _collapse_df(n: int = 300) -> pd.DataFrame:
    df = _uptrend_breakout_df(n)
    # Replace the last bar with a collapse far below the recent range.
    base = 100.0 + (n - 1) * 0.1
    df.iloc[-1] = {"open": base, "high": base, "low": base - 30.0, "close": base - 25.0, "volume": 1500.0}
    return df


class TestDonchianTrendStrategy(unittest.TestCase):
    def test_registered_and_loadable(self):
        self.assertIn("xauby_donchian_trend", available_strategies())
        s = load_strategy("donchian_trend", {"entry_len": 20})
        self.assertEqual(s.name, "xauby_donchian_trend")
        self.assertEqual(s.required_timeframes, ["1h"])
        self.assertEqual(s.config["entry_len"], 20)

    def test_param_grid_and_targets_declared(self):
        self.assertGreater(len(PARAM_GRID), 0)
        self.assertLessEqual(len(PARAM_GRID), 24)
        self.assertIn("BULL_TREND_STRONG", TARGET_REGIMES)

    def _signal(self, df: pd.DataFrame, has_position: bool = False, sl_confirmed: bool = False, **config):
        strategy = DonchianTrendStrategy({"entry_len": 20, "adx_min": 0.0, "vol_min_ratio": 0.0, **config})
        return strategy.analyze(
            MarketContext(
                symbol="BTCUSDT",
                timeframe_primary="1h",
                df_primary=df,
                current_price=float(df["close"].iloc[-1]),
                has_position=has_position,
                sl_confirmed=sl_confirmed,
            )
        )

    def test_breakout_above_ema200_buys(self):
        signal = self._signal(_uptrend_breakout_df())
        self.assertEqual(signal.action, "BUY")
        self.assertIsNotNone(signal.stop_loss_distance)
        self.assertGreater(signal.stop_loss_distance, 0.0)

    def test_holds_when_insufficient_bars(self):
        signal = self._signal(_uptrend_breakout_df(120))
        self.assertEqual(signal.action, "HOLD")

    def test_no_breakout_holds(self):
        df = _uptrend_breakout_df()[:-1]
        signal = self._signal(df)
        self.assertEqual(signal.action, "HOLD")

    def test_sl_confirmed_sells(self):
        signal = self._signal(_uptrend_breakout_df(), has_position=True, sl_confirmed=True)
        self.assertEqual(signal.action, "SELL")
        self.assertIn("sl", signal.reason.lower())

    def test_collapse_exits_position(self):
        signal = self._signal(_collapse_df(), has_position=True)
        self.assertEqual(signal.action, "SELL")

    def test_never_sells_when_flat(self):
        signal = self._signal(_collapse_df(), has_position=False)
        self.assertNotEqual(signal.action, "SELL")


if __name__ == "__main__":
    unittest.main()
