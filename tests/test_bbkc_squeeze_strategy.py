"""Tests for BBKC squeeze breakout strategy."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.strategies.context import MarketContext
from xauby.strategies.bbkc_squeeze.indicators import compute_indicators
from xauby.strategies.bbkc_squeeze.strategy import BBKCSqueezeStrategy
from xauby.strategies.indicators.indicator_bbkc_squeeze import BBKCSqueezeIndicator
from xauby.strategies.indicators.registry import available_indicators, load_indicator
from xauby.strategies.registry import available_strategies, load_strategy


def _squeeze_breakout_df(n: int = 120) -> pd.DataFrame:
    rows = []
    for i in range(n):
        if i < 90:
            base = 100.0 + 0.01 * np.sin(i * 0.5)
            rows.append(
                {
                    "open": base,
                    "high": base + 0.2,
                    "low": base - 0.2,
                    "close": base + 0.05,
                    "volume": 1000.0,
                }
            )
        elif i < 98:
            base = 100.0
            rows.append(
                {
                    "open": base,
                    "high": base + 0.15,
                    "low": base - 0.15,
                    "close": base,
                    "volume": 1000.0,
                }
            )
        else:
            base = 100 + (i - 98) * 5
            rows.append(
                {
                    "open": base - 1,
                    "high": base + 3,
                    "low": base - 1,
                    "close": base + 2,
                    "volume": 2500.0,
                }
            )
    return pd.DataFrame(rows)


class TestBBKCSqueezeStrategy(unittest.TestCase):
    def test_strategy_is_registered_and_loadable(self):
        self.assertIn("bbkc_squeeze", available_strategies())
        strategy = load_strategy("bbkc_squeeze", {"bb_length": 20})
        self.assertEqual(strategy.name, "bbkc_squeeze")
        self.assertEqual(strategy.required_timeframes, ["1h"])

    def test_indicator_plugin_is_registered(self):
        self.assertIn("bbkc_squeeze", available_indicators())
        indicator = load_indicator("bbkc_squeeze")
        out = indicator.compute(_squeeze_breakout_df(), {"bb_length": 20})
        self.assertIn("bb_upper", out.columns)
        self.assertIn("momentum", out.columns)
        self.assertIn("zone", out.columns)

    def test_indicator_volume_ratio_matches_strategy_snapshot(self):
        cfg = BBKCSqueezeStrategy.default_config()
        df = _squeeze_breakout_df()

        strategy_indicators = compute_indicators(df, cfg)
        indicator_row = BBKCSqueezeIndicator().compute(df, cfg).iloc[-1]

        self.assertAlmostEqual(
            float(indicator_row["volume_ratio"]),
            float(strategy_indicators["volume_ratio"]),
            places=4,
        )

    def test_volume_ratio_uses_latest_closed_candle(self):
        cfg = BBKCSqueezeStrategy.default_config()
        df = _squeeze_breakout_df()
        df.loc[df.index[-1], "volume"] = 9000.0

        indicators = compute_indicators(df, cfg)

        self.assertGreater(indicators["volume_ratio"], 3.0)

    def test_checklist_and_indicator_panel_use_configured_rsi_range(self):
        df = _squeeze_breakout_df()
        cfg = {"rsi_min": 0.0, "rsi_max": 1.0, "vol_min_ratio": 0.0}
        signal = self._signal(df, **cfg)
        rsi_item = next(item for item in signal.checklist if item["label"] == "RSI")

        indicator = BBKCSqueezeIndicator()
        snapshot = indicator.snapshot(df, cfg)
        panel_rsi = next(item for item in indicator.panel_items(snapshot) if item["label"] == "RSI")

        self.assertFalse(rsi_item["ok"])
        self.assertFalse(panel_rsi["ok"])
        self.assertEqual(panel_rsi["hint"], "0-1")

    def _signal(self, df: pd.DataFrame, **config):
        strategy = BBKCSqueezeStrategy(
            {
                "entry_on_release_only": False,
                "vol_min_ratio": 0.0,
                "rsi_min": 0.0,
                "rsi_max": 100.0,
                **config,
            }
        )
        return strategy.analyze(
            MarketContext(
                symbol="BTCUSDT",
                timeframe_primary="4h",
                df_primary=df,
                current_price=float(df["close"].iloc[-1]),
            )
        )

    def test_squeeze_breakout_buys(self):
        signal = self._signal(_squeeze_breakout_df())

        self.assertEqual(signal.action, "BUY")
        self.assertIn("breakout", signal.reason.lower())
        self.assertTrue(signal.indicators["squeeze_fired"])

    def test_fresh_release_entry(self):
        df = _squeeze_breakout_df()[:99]
        signal = self._signal(
            df,
            entry_on_release_only=True,
            momentum_min=0.0,
        )

        self.assertEqual(signal.action, "BUY")
        self.assertTrue(signal.indicators["squeeze_release"])

    def test_release_window_allows_entry_for_three_candles(self):
        signal = self._signal(
            _squeeze_breakout_df()[:101],
            entry_on_release_only=True,
            release_window=3,
        )

        self.assertEqual(signal.action, "BUY")
        self.assertFalse(signal.indicators["squeeze_release"])
        self.assertTrue(signal.indicators["squeeze_release_recent"])
        self.assertEqual(signal.indicators["bars_since_release"], 2)

    def test_release_window_rejects_entry_after_three_candles(self):
        signal = self._signal(
            _squeeze_breakout_df()[:102],
            entry_on_release_only=True,
            release_window=3,
        )

        self.assertEqual(signal.action, "HOLD")
        self.assertFalse(signal.indicators["squeeze_release_recent"])

    def test_compressed_market_holds(self):
        df = _squeeze_breakout_df()[:85]
        signal = self._signal(df)

        self.assertEqual(signal.action, "HOLD")
        self.assertTrue(signal.indicators["squeeze_on"])

    def test_open_position_exits_on_momentum_loss(self):
        strategy = BBKCSqueezeStrategy(
            {
                "exit_on_momentum_loss": True,
                "exit_on_squeeze_reentry": False,
            }
        )
        compressed = _squeeze_breakout_df()[:90]
        exit_signal = strategy.analyze(
            MarketContext(
                symbol="BTCUSDT",
                timeframe_primary="4h",
                df_primary=compressed,
                current_price=float(compressed["close"].iloc[-1]),
                has_position=True,
            )
        )
        self.assertEqual(exit_signal.action, "SELL")
        self.assertIn("momentum", exit_signal.reason.lower())


if __name__ == "__main__":
    unittest.main()
