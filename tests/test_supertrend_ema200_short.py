"""Tests for the config-gated short side of supertrend_ema200."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.strategies.context import MarketContext
from xauby.strategies.supertrend_ema200.strategy import SuperTrendEMA200Strategy


def _downtrend_df(n: int = 320) -> pd.DataFrame:
    """Steady decline: close below EMA200, SuperTrend bear-confirmed."""
    rows = []
    price = 500.0
    for i in range(n):
        price *= 0.995
        rows.append(
            {
                "open": price * 1.002,
                "high": price * 1.004,
                "low": price * 0.998,
                "close": price,
                "volume": 1000.0,
            }
        )
    return pd.DataFrame(rows)


def _uptrend_df(n: int = 320) -> pd.DataFrame:
    rows = []
    price = 100.0
    for i in range(n):
        price *= 1.005
        rows.append(
            {
                "open": price * 0.998,
                "high": price * 1.002,
                "low": price * 0.996,
                "close": price,
                "volume": 1000.0,
            }
        )
    return pd.DataFrame(rows)


class TestSuperTrendEMA200Short(unittest.TestCase):
    def _signal(self, df, *, has_position=False, position_side=None, **config):
        strategy = SuperTrendEMA200Strategy(
            {"vol_min_ratio": 0.0, "entry_on_flip_only": False, **config}
        )
        return strategy.analyze(
            MarketContext(
                symbol="BTCUSDT",
                timeframe_primary="4h",
                df_primary=df,
                current_price=float(df["close"].iloc[-1]),
                has_position=has_position,
                position_side=position_side,
            )
        )

    def test_short_disabled_by_default_in_downtrend(self):
        signal = self._signal(_downtrend_df())

        self.assertEqual(signal.action, "HOLD")

    def test_downtrend_opens_short_when_enabled(self):
        signal = self._signal(_downtrend_df(), enable_short=True)

        self.assertEqual(signal.action, "SELL")
        self.assertEqual(signal.intent, "OPEN")
        self.assertTrue(signal.is_short)
        self.assertGreater(signal.stop_loss_distance or 0.0, 0.0)

    def test_short_exit_when_supertrend_flips_bullish(self):
        signal = self._signal(
            _uptrend_df(),
            has_position=True,
            position_side="SHORT",
            enable_short=True,
        )

        self.assertEqual(signal.action, "BUY")
        self.assertEqual(signal.intent, "CLOSE")
        self.assertTrue(signal.is_short)

    def test_long_entry_path_unchanged(self):
        signal = self._signal(_uptrend_df())

        self.assertEqual(signal.action, "BUY")
        self.assertFalse(signal.is_short)

    def test_flip_only_hold_explains_the_current_side_and_freshness_gate(self):
        strategy = SuperTrendEMA200Strategy({
            "enable_short": True,
            "vol_min_ratio": 0.0,
            "entry_on_flip_only": True,
        })
        frame = _uptrend_df()

        signal = strategy.analyze(MarketContext(
            symbol="BTCUSDT",
            timeframe_primary="4h",
            df_primary=frame,
            current_price=float(frame["close"].iloc[-1]),
        ))

        self.assertEqual(signal.action, "HOLD")
        self.assertIn("bullish above EMA200", signal.reason)
        self.assertIn("fresh bull flip", signal.reason)
        supertrend = next(item for item in signal.checklist if item["label"] == "SuperTrend")
        self.assertFalse(supertrend["ok"])
        self.assertEqual(supertrend["hint"], "Fresh bull flip required")


if __name__ == "__main__":
    unittest.main()
