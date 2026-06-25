"""Tests for the RSI(2) mean-reversion strategy."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.strategies.context import MarketContext
from xauby.strategies.rsi2_meanrev.strategy import RSI2MeanReversionStrategy, PARAM_GRID, TARGET_REGIMES
from xauby.strategies.registry import available_strategies, load_strategy


def _uptrend_with_dip_df(n: int = 300, dip_bars: int = 4) -> pd.DataFrame:
    rows = []
    for i in range(n - dip_bars):
        base = 100.0 + i * 0.2
        rows.append({"open": base, "high": base + 0.3, "low": base - 0.3, "close": base + 0.1, "volume": 1000.0})
    # Sharp consecutive down bars: RSI(2) -> ~0, close drops below SMA5 but
    # stays far above EMA200 of the long uptrend.
    top = 100.0 + (n - dip_bars) * 0.2
    for j in range(dip_bars):
        px = top - (j + 1) * 1.5
        rows.append({"open": px + 1.5, "high": px + 1.6, "low": px - 0.2, "close": px, "volume": 1200.0})
    return pd.DataFrame(rows)


def _pure_uptrend_df(n: int = 300) -> pd.DataFrame:
    rows = []
    for i in range(n):
        base = 100.0 + i * 0.2
        rows.append({"open": base, "high": base + 0.3, "low": base - 0.3, "close": base + 0.25, "volume": 1000.0})
    return pd.DataFrame(rows)


class TestRSI2MeanReversionStrategy(unittest.TestCase):
    def test_registered_and_loadable(self):
        self.assertIn("rsi2_meanrev", available_strategies())
        s = load_strategy("rsi2_meanrev", {"rsi_buy": 15.0})
        self.assertEqual(s.name, "rsi2_meanrev")
        self.assertEqual(s.required_timeframes, ["1h"])

    def test_param_grid_and_targets_declared(self):
        self.assertGreater(len(PARAM_GRID), 0)
        self.assertLessEqual(len(PARAM_GRID), 24)
        self.assertIn("SIDEWAYS_CHOP", TARGET_REGIMES)

    def _signal(self, df: pd.DataFrame, has_position: bool = False, sl_confirmed: bool = False, **config):
        strategy = RSI2MeanReversionStrategy({**config})
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

    def test_oversold_dip_in_uptrend_buys(self):
        signal = self._signal(_uptrend_with_dip_df())
        self.assertEqual(signal.action, "BUY")
        self.assertLess(signal.indicators["rsi2"], 10.0)

    def test_holds_when_insufficient_bars(self):
        signal = self._signal(_uptrend_with_dip_df(120))
        self.assertEqual(signal.action, "HOLD")

    def test_no_dip_holds(self):
        signal = self._signal(_pure_uptrend_df())
        self.assertEqual(signal.action, "HOLD")

    def test_sl_confirmed_sells(self):
        signal = self._signal(_uptrend_with_dip_df(), has_position=True, sl_confirmed=True)
        self.assertEqual(signal.action, "SELL")

    def test_reversion_above_sma5_exits(self):
        signal = self._signal(_pure_uptrend_df(), has_position=True)
        self.assertEqual(signal.action, "SELL")
        self.assertIn("sma5", signal.reason.lower())

    def test_never_sells_when_flat(self):
        signal = self._signal(_pure_uptrend_df(), has_position=False)
        self.assertNotEqual(signal.action, "SELL")


if __name__ == "__main__":
    unittest.main()
