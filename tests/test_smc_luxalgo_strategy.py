"""Tests for the LuxAlgo-inspired SMC strategy."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.strategies.context import MarketContext
from xauby.strategies.indicators.indicator_smc_luxalgo import SMCLuxAlgoIndicator
from xauby.strategies.registry import available_strategies, load_strategy
from xauby.strategies.smc_luxalgo.strategy import SMCLuxAlgoStrategy


def _smc_bullish_df() -> pd.DataFrame:
    rows = []
    for i in range(210):
        base = 100.0 + i * 0.05
        rows.append(
            {
                "timestamp": i,
                "open": base - 0.05,
                "high": base + 0.40,
                "low": base - 0.40,
                "close": base,
                "volume": 1000.0,
            }
        )

    # Confirmed swing/internal high before the entry candle.
    rows[175].update({"open": 106.0, "high": 118.0, "low": 105.5, "close": 116.0, "volume": 1300.0})
    for idx in range(176, 198):
        base = 110.0 - (idx - 176) * 0.08
        rows[idx].update({"open": base + 0.15, "high": base + 0.55, "low": base - 0.55, "close": base, "volume": 950.0})
    rows[199].update({"open": 107.8, "high": 108.4, "low": 104.2, "close": 107.2, "volume": 1000.0})
    rows[200].update({"open": 107.2, "high": 108.0, "low": 103.0, "close": 107.6, "volume": 1000.0})
    rows[201].update({"open": 107.6, "high": 109.0, "low": 106.8, "close": 108.4, "volume": 1000.0})
    rows[202].update({"open": 108.4, "high": 110.0, "low": 107.9, "close": 109.5, "volume": 1000.0})
    rows[203].update({"open": 109.5, "high": 112.0, "low": 109.0, "close": 111.5, "volume": 1200.0})
    rows[204].update({"open": 111.5, "high": 114.0, "low": 111.2, "close": 113.6, "volume": 1300.0})
    rows[205].update({"open": 113.6, "high": 117.0, "low": 113.2, "close": 116.5, "volume": 1400.0})
    # Breaks the confirmed 118 high and creates a bullish FVG versus bar -2.
    rows[206].update({"open": 116.5, "high": 121.0, "low": 118.5, "close": 120.2, "volume": 1800.0})
    rows[207].update({"open": 120.2, "high": 122.0, "low": 119.4, "close": 121.0, "volume": 1600.0})
    rows[208].update({"open": 121.0, "high": 123.0, "low": 120.0, "close": 121.8, "volume": 1600.0})
    rows[209].update({"open": 121.8, "high": 123.5, "low": 120.8, "close": 122.6, "volume": 1600.0})
    return pd.DataFrame(rows[:207])


def _smc_bearish_df() -> pd.DataFrame:
    df = _smc_bullish_df()
    start = len(df) - 1
    extra = []
    for j in range(8):
        base = 122.0 - j * 3.0
        extra.append(
            {
                "timestamp": start + j + 1,
                "open": base + 1.0,
                "high": base + 1.6,
                "low": base - 2.6,
                "close": base - 2.0,
                "volume": 1800.0,
            }
        )
    return pd.concat([df, pd.DataFrame(extra)], ignore_index=True)


class TestSMCLuxAlgoStrategy(unittest.TestCase):
    def _signal(self, df: pd.DataFrame, **kwargs):
        cfg = {
            "internal_length": 3,
            "swing_length": 8,
            "entry_event_window": 6,
            "require_swing_bias": False,
            "confluence_min_score": 1.0,
            "vol_min_ratio": 0.0,
            "max_calc_bars": 260,
            **kwargs.pop("config", {}),
        }
        strategy = SMCLuxAlgoStrategy(cfg)
        return strategy.analyze(
            MarketContext(
                symbol="XAUTUSDT",
                timeframe_primary="4h",
                df_primary=df,
                current_price=float(df["close"].iloc[-1]),
                has_position=kwargs.get("has_position", False),
                position_side=kwargs.get("position_side"),
                sl_confirmed=kwargs.get("sl_confirmed", False),
            )
        )

    def test_registered_and_loadable(self):
        self.assertIn("smc_luxalgo", available_strategies())
        strategy = load_strategy("smc_luxalgo", {"internal_length": 3})
        self.assertEqual(strategy.name, "smc_luxalgo")

    def test_bullish_structure_with_confluence_buys(self):
        signal = self._signal(_smc_bullish_df())

        self.assertEqual(signal.action, "BUY")
        self.assertIn("SMC bullish", signal.reason)
        self.assertGreaterEqual(signal.indicators["bull_confluence_score"], 1.0)
        self.assertIsNotNone(signal.stop_loss_price)

    def test_holds_when_confluence_threshold_too_high(self):
        signal = self._signal(_smc_bullish_df(), config={"confluence_min_score": 5.0})

        self.assertEqual(signal.action, "HOLD")
        self.assertIn("lacks confluence", signal.reason)

    def test_bearish_structure_exits_long(self):
        signal = self._signal(_smc_bearish_df(), has_position=True, position_side="LONG")

        self.assertEqual(signal.action, "SELL")
        self.assertIn("bearish", signal.reason.lower())

    def test_bearish_structure_can_open_short_when_enabled(self):
        signal = self._signal(
            _smc_bearish_df(),
            config={"allow_short": True, "confluence_min_score": 0.0},
        )

        self.assertEqual(signal.action, "SELL")
        self.assertEqual(signal.intent, "OPEN")
        self.assertEqual(signal.position_side, "SHORT")

    def test_indicator_columns_and_panel_thresholds(self):
        indicator = SMCLuxAlgoIndicator()
        cfg = {
            "internal_length": 3,
            "swing_length": 8,
            "entry_event_window": 6,
            "confluence_min_score": 5.0,
            "vol_min_ratio": 2.0,
        }

        computed = indicator.compute(_smc_bullish_df(), cfg)
        self.assertIn("smc_zone", computed.columns)
        self.assertIn("smc_bull_confluence_score", computed.columns)

        snapshot = indicator.snapshot(_smc_bullish_df(), cfg)
        items = indicator.panel_items(snapshot)
        bull_item = next(item for item in items if item["label"] == "Bull Score")
        vol_item = next(item for item in items if item["label"] == "Vol Ratio")
        self.assertFalse(bull_item["ok"])
        self.assertEqual(bull_item["hint"], ">= 5.0")
        self.assertFalse(vol_item["ok"])
        self.assertEqual(vol_item["hint"], ">= 2.00x")


if __name__ == "__main__":
    unittest.main()
