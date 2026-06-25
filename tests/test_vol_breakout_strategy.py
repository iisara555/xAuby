"""Tests for the volatility-expansion breakout strategy."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.strategies.context import MarketContext
from xauby.strategies.vol_breakout.strategy import VolatilityBreakoutStrategy, PARAM_GRID, TARGET_REGIMES
from xauby.strategies.registry import available_strategies, load_strategy


def _quiet_then_breakout_df(n: int = 300, breakout_bars: int = 3) -> pd.DataFrame:
    rows = []
    for i in range(n - breakout_bars):
        base = 100.0
        rows.append({"open": base, "high": base + 0.2, "low": base - 0.2, "close": base + 0.05, "volume": 1000.0})
    # Volatility expansion: wide-range candles on heavy volume closing above
    # the prior quiet range.
    for j in range(breakout_bars):
        base = 100.0 + (j + 1) * 4.0
        rows.append({"open": base - 4.0, "high": base + 2.0, "low": base - 4.5, "close": base + 1.5, "volume": 3500.0})
    return pd.DataFrame(rows)


def _quiet_then_drop_df(n: int = 300) -> pd.DataFrame:
    df = _quiet_then_breakout_df(n)
    base = 100.0
    df.iloc[-1] = {"open": base, "high": base, "low": base - 12.0, "close": base - 10.0, "volume": 2000.0}
    return df


class TestVolatilityBreakoutStrategy(unittest.TestCase):
    def test_registered_and_loadable(self):
        self.assertIn("vol_breakout", available_strategies())
        s = load_strategy("vol_breakout", {"lookback": 12})
        self.assertEqual(s.name, "vol_breakout")
        self.assertEqual(s.required_timeframes, ["1h"])

    def test_param_grid_and_targets_declared(self):
        self.assertGreater(len(PARAM_GRID), 0)
        self.assertLessEqual(len(PARAM_GRID), 24)
        self.assertIn("VOLATILITY_EXPANSION", TARGET_REGIMES)

    def _signal(self, df: pd.DataFrame, has_position: bool = False, sl_confirmed: bool = False, **config):
        strategy = VolatilityBreakoutStrategy({"vol_min_ratio": 0.0, **config})
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

    def test_expansion_breakout_buys(self):
        signal = self._signal(_quiet_then_breakout_df())
        self.assertEqual(signal.action, "BUY")
        self.assertGreater(signal.indicators["atr"], signal.indicators["atr_avg"])

    def test_holds_when_insufficient_bars(self):
        signal = self._signal(_quiet_then_breakout_df(120))
        self.assertEqual(signal.action, "HOLD")

    def test_quiet_market_holds(self):
        df = _quiet_then_breakout_df()[:-4]
        signal = self._signal(df)
        self.assertEqual(signal.action, "HOLD")

    def test_sl_confirmed_sells(self):
        signal = self._signal(_quiet_then_breakout_df(), has_position=True, sl_confirmed=True)
        self.assertEqual(signal.action, "SELL")

    def test_drop_below_exit_ema_sells(self):
        signal = self._signal(_quiet_then_drop_df(), has_position=True)
        self.assertEqual(signal.action, "SELL")
        self.assertIn("ema", signal.reason.lower())

    def test_never_sells_when_flat(self):
        signal = self._signal(_quiet_then_drop_df(), has_position=False)
        self.assertNotEqual(signal.action, "SELL")


if __name__ == "__main__":
    unittest.main()
