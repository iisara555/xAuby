"""Tests for BB + RSI mean reversion strategy."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.strategies.context import MarketContext
from xauby.strategies.bbrsi_mean_reversion.indicators import compute_indicators
from xauby.strategies.bbrsi_mean_reversion.strategy import BBRSIMeanReversionStrategy
from xauby.strategies.indicators.indicator_bbrsi_mean_reversion import BBRSIMeanReversionIndicator
from xauby.strategies.indicators.registry import available_indicators, load_indicator
from xauby.strategies.registry import available_strategies, load_strategy


def _oversold_df() -> pd.DataFrame:
    rows = []
    for i in range(100):
        price = 100 + 2 * np.sin(i * 0.3)
        rows.append(
            {
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price,
                "volume": 1000.0,
            }
        )
    rows[-2].update({"open": 99, "high": 100, "low": 97, "close": 98, "volume": 1200.0})
    rows[-1].update({"open": 95, "high": 96, "low": 88, "close": 89, "volume": 1500.0})
    return pd.DataFrame(rows)


class TestBBRSIMeanReversionStrategy(unittest.TestCase):
    def test_strategy_is_registered_and_loadable(self):
        self.assertIn("bbrsi_mean_reversion", available_strategies())
        strategy = load_strategy("bbrsi_mean_reversion", {"rsi_oversold": 25.0})
        self.assertEqual(strategy.name, "bbrsi_mean_reversion")
        self.assertEqual(strategy.required_timeframes, ["1h"])

    def test_indicator_plugin_is_registered(self):
        self.assertIn("bbrsi_mean_reversion", available_indicators())
        indicator = load_indicator("bbrsi_mean_reversion")
        out = indicator.compute(_oversold_df(), {"bb_length": 20})
        self.assertIn("bb_lower", out.columns)
        self.assertIn("rsi", out.columns)
        self.assertIn("zone", out.columns)

    def test_indicator_volume_ratio_matches_strategy_snapshot(self):
        cfg = {
            "bb_length": 20,
            "bb_std": 2.0,
            "rsi_period": 14,
            "rsi_oversold": 30.0,
            "rsi_overbought": 70.0,
            "volume_ma_period": 20,
        }
        df = _oversold_df()

        strategy_indicators = compute_indicators(df, cfg)
        indicator_row = BBRSIMeanReversionIndicator().compute(df, cfg).iloc[-1]

        self.assertAlmostEqual(
            float(indicator_row["volume_ratio"]),
            float(strategy_indicators["volume_ratio"]),
            places=4,
        )

    def test_volume_ratio_uses_latest_closed_candle(self):
        df = _oversold_df()
        df.loc[df.index[-1], "volume"] = 9000.0

        indicators = compute_indicators(df, {"volume_ma_period": 20})

        self.assertGreater(indicators["volume_ratio"], 3.0)

    def test_indicator_rsi_panel_uses_configured_oversold_threshold(self):
        cfg = {"rsi_oversold": 5.0}
        indicator = BBRSIMeanReversionIndicator()

        snapshot = indicator.snapshot(_oversold_df(), cfg)
        items = indicator.panel_items(snapshot)
        rsi_item = next(item for item in items if item["label"] == "RSI")

        self.assertFalse(rsi_item["ok"])
        self.assertEqual(rsi_item["hint"], "<= 5")

    def _signal(self, df: pd.DataFrame, *, has_position: bool = False, **config):
        strategy = BBRSIMeanReversionStrategy({"vol_min_ratio": 0.0, **config})
        return strategy.analyze(
            MarketContext(
                symbol="BTCUSDT",
                timeframe_primary="4h",
                df_primary=df,
                current_price=float(df["close"].iloc[-1]),
                has_position=has_position,
            )
        )

    def test_oversold_touch_buys(self):
        signal = self._signal(_oversold_df())

        self.assertEqual(signal.action, "BUY")
        self.assertIn("oversold", signal.reason.lower())
        self.assertEqual(signal.indicators["zone"], "OVERSOLD")
        self.assertTrue(signal.indicators["below_lower_band"])

    def test_neutral_market_holds(self):
        rows = []
        for i in range(100):
            price = 100.0
            rows.append(
                {
                    "open": price,
                    "high": price + 0.5,
                    "low": price - 0.5,
                    "close": price,
                    "volume": 1000.0,
                }
            )
        signal = self._signal(pd.DataFrame(rows))

        self.assertEqual(signal.action, "HOLD")

    def test_mean_reversion_exit_at_mid_band(self):
        df = _oversold_df()
        df.loc[df.index[-1], "close"] = 102.0
        df.loc[df.index[-1], "high"] = 103.0
        df.loc[df.index[-1], "low"] = 100.0
        df.loc[df.index[-1], "open"] = 100.0

        signal = self._signal(df, has_position=True)

        self.assertEqual(signal.action, "SELL")
        self.assertIn("mid band", signal.reason.lower())


if __name__ == "__main__":
    unittest.main()
