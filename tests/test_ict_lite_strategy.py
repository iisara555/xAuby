"""Tests for ICT Lite sweep/reclaim and MSS gates."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.strategies.context import MarketContext
from xauby.strategies.indicators.indicator_ict_lite import ICTLiteIndicator
from xauby.strategies.ict_lite_strategy.strategy import ICTLiteStrategy


def _base_df() -> pd.DataFrame:
    rows = []
    for i in range(130):
        price = 100.0 + i * 0.2
        rows.append(
            {
                "timestamp": i,
                "open": price - 0.05,
                "high": price + 0.4,
                "low": price - 0.4,
                "close": price,
                "volume": 1000.0,
            }
        )
    # Liquidity sweep below the known prior swing low.
    rows[-2].update({"open": 124.0, "high": 125.0, "low": 95.0, "close": 120.0, "volume": 1500.0})
    # Reclaim and market-structure shift above the post-sweep pivot high.
    rows[-1].update({"open": 126.0, "high": 132.0, "low": 124.0, "close": 131.0, "volume": 1600.0})
    return pd.DataFrame(rows)


class TestICTLiteStrategy(unittest.TestCase):
    def _signal(self, df: pd.DataFrame):
        strategy = ICTLiteStrategy(
            {
                "ema_fast": 12,
                "ema_slow": 34,
                "atr_period": 14,
                "swing_lookback": 20,
                "reclaim_window": 3,
                "mss_lookback": 3,
                "require_mss": True,
                "min_body_atr": 0.05,
                "rsi_min": 0.0,
                "rsi_max": 100.0,
                "vol_min_ratio": 0.0,
            }
        )
        return strategy.analyze(
            MarketContext(
                symbol="BTCUSDT",
                timeframe_primary="4h",
                df_primary=df,
                current_price=float(df["open"].iloc[-1]),
            )
        )

    def test_bullish_sweep_reclaim_with_mss_buys(self):
        signal = self._signal(_base_df())

        self.assertEqual(signal.action, "BUY")
        self.assertIn("MSS", signal.reason)
        self.assertTrue(next(item for item in signal.checklist if item["label"] == "MSS")["ok"])

    def test_reclaim_without_mss_holds(self):
        df = _base_df()
        df.loc[df.index[-1], "close"] = 124.5
        df.loc[df.index[-1], "high"] = 126.0

        signal = self._signal(df)

        self.assertEqual(signal.action, "HOLD")
        self.assertFalse(next(item for item in signal.checklist if item["label"] == "MSS")["ok"])

        indicator = ICTLiteIndicator().compute(
            df,
            {
                "ema_fast": 12,
                "ema_slow": 34,
                "swing_lookback": 20,
                "reclaim_window": 3,
                "mss_lookback": 3,
            },
        )
        self.assertNotEqual(indicator["ict_zone"].iloc[-1], "MSS")

    def test_indicator_rsi_matches_strategy_wilder_rsi(self):
        cfg = {"rsi_period": 14}
        df = _base_df()

        indicator = ICTLiteIndicator().compute(df, cfg)
        strategy_rsi = ICTLiteStrategy._rsi(df["close"].astype(float), 14)

        self.assertAlmostEqual(float(indicator["rsi"].iloc[-1]), float(strategy_rsi.iloc[-1]), places=8)

    def test_indicator_panel_uses_configured_rsi_and_volume_thresholds(self):
        cfg = {"rsi_min": 0.0, "rsi_max": 1.0, "vol_min_ratio": 2.0}
        indicator = ICTLiteIndicator()

        snapshot = indicator.snapshot(_base_df(), cfg)
        items = indicator.panel_items(snapshot)
        rsi_item = next(item for item in items if item["label"] == "RSI")
        vol_item = next(item for item in items if item["label"] == "Vol Ratio")

        self.assertFalse(rsi_item["ok"])
        self.assertEqual(rsi_item["hint"], "0-1")
        self.assertFalse(vol_item["ok"])
        self.assertEqual(vol_item["hint"], ">= 2.00x")


if __name__ == "__main__":
    unittest.main()
