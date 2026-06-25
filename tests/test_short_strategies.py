"""Tests for semantic OPEN/CLOSE SHORT strategy signals."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.strategies.context import MarketContext
from xauby.strategies.donchian_short.strategy import DonchianShortStrategy
from xauby.strategies.rsi2_short.strategy import RSI2ShortStrategy
from xauby.strategies.supertrend_short.strategy import SuperTrendShortStrategy
from xauby.strategies.registry import available_strategies


def _downtrend_breakdown_df(n: int = 300) -> pd.DataFrame:
    rows = []
    for i in range(n):
        base = 200.0 - i * 0.1
        if i == n - 1:
            rows.append({"open": base, "high": base + 0.3, "low": base - 8.0, "close": base - 7.0, "volume": 2500.0})
        else:
            rows.append({"open": base, "high": base + 0.4, "low": base - 0.4, "close": base - 0.1, "volume": 1000.0})
    return pd.DataFrame(rows)


def _downtrend_rally_df(n: int = 300, rally_bars: int = 4) -> pd.DataFrame:
    rows = []
    for i in range(n - rally_bars):
        base = 200.0 - i * 0.2
        rows.append({"open": base, "high": base + 0.3, "low": base - 0.3, "close": base - 0.1, "volume": 1000.0})
    bottom = 200.0 - (n - rally_bars) * 0.2
    for j in range(rally_bars):
        px = bottom + (j + 1) * 1.5
        rows.append({"open": px - 1.5, "high": px + 0.2, "low": px - 1.6, "close": px, "volume": 1200.0})
    return pd.DataFrame(rows)


def _pure_downtrend_df(n: int = 300) -> pd.DataFrame:
    rows = []
    for i in range(n):
        base = 200.0 - i * 0.2
        rows.append({"open": base, "high": base + 0.3, "low": base - 0.3, "close": base - 0.25, "volume": 1000.0})
    return pd.DataFrame(rows)


def _ctx(df, has_position=False, sl_confirmed=False):
    return MarketContext(
        symbol="BTCUSDT",
        timeframe_primary="1h",
        df_primary=df,
        current_price=float(df["close"].iloc[-1]),
        has_position=has_position,
        sl_confirmed=sl_confirmed,
    )


class TestShortPluginsRegistered(unittest.TestCase):
    def test_registered_with_warnings(self):
        names = available_strategies()
        for n in ("donchian_short", "supertrend_short", "rsi2_short"):
            self.assertIn(n, names)
        for cls in (DonchianShortStrategy, SuperTrendShortStrategy, RSI2ShortStrategy):
            warnings = cls({}).validate_config()
            self.assertTrue(any("RESEARCH-ONLY" in w for w in warnings))


class TestDonchianShort(unittest.TestCase):
    def test_breakdown_below_ema200_signals_entry(self):
        sig = DonchianShortStrategy({"entry_len": 20}).analyze(_ctx(_downtrend_breakdown_df()))
        self.assertEqual(sig.intent, "OPEN")
        self.assertEqual(sig.position_side, "SHORT")
        self.assertIn("short", sig.reason.lower())

    def test_no_breakdown_holds(self):
        sig = DonchianShortStrategy({"entry_len": 20}).analyze(_ctx(_downtrend_breakdown_df()[:-1]))
        self.assertEqual(sig.action, "HOLD")

    def test_sl_confirmed_covers(self):
        sig = DonchianShortStrategy({}).analyze(_ctx(_downtrend_breakdown_df(), has_position=True, sl_confirmed=True))
        self.assertEqual(sig.intent, "CLOSE")
        self.assertEqual(sig.position_side, "SHORT")

    def test_reclaim_covers(self):
        df = _downtrend_breakdown_df()
        base = float(df["close"].iloc[-30])
        df.iloc[-1] = {"open": base, "high": base + 20, "low": base, "close": base + 18, "volume": 1500.0}
        sig = DonchianShortStrategy({"exit_len": 20}).analyze(_ctx(df, has_position=True))
        self.assertEqual(sig.intent, "CLOSE")


class TestSuperTrendShort(unittest.TestCase):
    def test_bear_state_below_ema200_signals_entry(self):
        sig = SuperTrendShortStrategy({"entry_on_flip_only": False}).analyze(_ctx(_pure_downtrend_df()))
        self.assertEqual(sig.intent, "OPEN")
        self.assertEqual(sig.position_side, "SHORT")

    def test_sl_confirmed_covers(self):
        sig = SuperTrendShortStrategy({}).analyze(_ctx(_pure_downtrend_df(), has_position=True, sl_confirmed=True))
        self.assertEqual(sig.intent, "CLOSE")


class TestRSI2Short(unittest.TestCase):
    def test_overbought_rally_in_downtrend_signals_entry(self):
        sig = RSI2ShortStrategy({"rsi_sell": 85.0}).analyze(_ctx(_downtrend_rally_df()))
        self.assertEqual(sig.intent, "OPEN")
        self.assertEqual(sig.position_side, "SHORT")
        self.assertGreater(sig.indicators["rsi2"], 85.0)

    def test_pure_downtrend_holds(self):
        sig = RSI2ShortStrategy({}).analyze(_ctx(_pure_downtrend_df()))
        self.assertEqual(sig.action, "HOLD")

    def test_reversion_below_sma5_covers(self):
        sig = RSI2ShortStrategy({}).analyze(_ctx(_pure_downtrend_df(), has_position=True))
        self.assertEqual(sig.intent, "CLOSE")


if __name__ == "__main__":
    unittest.main()
