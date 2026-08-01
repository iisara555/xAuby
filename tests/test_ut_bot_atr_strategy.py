"""Tests for the UT Bot ATR strategy + indicator plugins."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.strategies.context import MarketContext
from xauby.strategies.indicators.registry import (
    available_indicators,
    indicators_for_strategy,
    load_indicator,
)
from xauby.strategies.registry import available_strategies, load_strategy
from xauby.strategies.ut_bot_atr.indicators import (
    _ut_bot_stop,
    _wilder_atr,
    compute_ut_bot_frame,
    ut_bot_snapshot,
)


def _trend_df(n: int = 400, slope: float = 0.05) -> pd.DataFrame:
    """A clean uptrend — UT Bot should end long."""
    price = 100 + slope * np.arange(n)
    return pd.DataFrame(
        [
            {"open": p, "high": p + 0.5, "low": p - 0.5, "close": p,
             "volume": 1000.0, "timestamp": 1_600_000_000 + i * 900}
            for i, p in enumerate(price)
        ]
    )


def _vshape_df(n: int = 400) -> pd.DataFrame:
    """Down then up — forces at least one flip in each direction."""
    half = n // 2
    price = np.concatenate([100 - 0.08 * np.arange(half),
                            100 - 0.08 * half + 0.08 * np.arange(n - half)])
    return pd.DataFrame(
        [
            {"open": p, "high": p + 0.4, "low": p - 0.4, "close": p,
             "volume": 1000.0, "timestamp": 1_600_000_000 + i * 900}
            for i, p in enumerate(price)
        ]
    )


class TestUTBotIndicatorMath(unittest.TestCase):
    def test_wilder_atr_on_constant_range_equals_that_range(self):
        n = 60
        high = np.full(n, 101.0)
        low = np.full(n, 99.0)
        close = np.full(n, 100.0)
        atr = _wilder_atr(high, low, close, 14)
        self.assertTrue(np.isnan(atr[:13]).all())
        np.testing.assert_allclose(atr[13:], 2.0, rtol=1e-9)

    def test_stop_trails_up_and_never_falls_during_an_uptrend(self):
        src = np.linspace(100, 140, 200)
        nloss = np.full(200, 2.0)
        stop, pos = _ut_bot_stop(src, nloss)
        rising = stop[1:] >= stop[:-1] - 1e-9
        self.assertTrue(rising.all(), "trailing stop must ratchet up in an uptrend")
        self.assertEqual(int(pos[-1]), 1)

    def test_stop_flips_short_on_a_sharp_reversal(self):
        up = np.linspace(100, 130, 100)
        down = np.linspace(130, 90, 100)
        src = np.concatenate([up, down])
        nloss = np.full(200, 1.5)
        _stop, pos = _ut_bot_stop(src, nloss)
        self.assertEqual(int(pos[-1]), -1)

    def test_frame_has_expected_columns(self):
        frame = compute_ut_bot_frame(_trend_df(), {"key_value": 1.0, "atr_period": 10})
        for col in ("ut_atr", "ut_nloss", "ut_stop", "ut_pos",
                    "ut_flip_up", "ut_flip_down", "ut_zone"):
            self.assertIn(col, frame.columns)

    def test_empty_frame_is_safe(self):
        frame = compute_ut_bot_frame(pd.DataFrame(), {})
        self.assertTrue(frame.empty)

    def test_snapshot_reports_long_zone_in_uptrend(self):
        snap = ut_bot_snapshot(_trend_df(), {"key_value": 1.0, "atr_period": 10})
        self.assertEqual(snap["zone"], "LONG")
        self.assertEqual(snap["pos"], 1)
        self.assertGreater(snap["bars_in_pos"], 1)

    def test_is_causal_last_value_independent_of_future_bars(self):
        """Recomputing on a truncated prefix must not change the value at the cut."""
        df = _vshape_df(400)
        full = compute_ut_bot_frame(df, {"key_value": 1.0, "atr_period": 10})
        cut = 300
        prefix = compute_ut_bot_frame(df.iloc[:cut].copy(), {"key_value": 1.0, "atr_period": 10})
        self.assertAlmostEqual(
            float(full["ut_stop"].iloc[cut - 1]),
            float(prefix["ut_stop"].iloc[-1]),
            places=9,
        )


class TestUTBotStrategy(unittest.TestCase):
    def test_registered(self):
        self.assertIn("ut_bot_atr", available_strategies())

    def test_maturity_is_research_not_production(self):
        strat = load_strategy("ut_bot_atr", {})
        self.assertEqual(strat.maturity, "research")

    def test_default_config_validates_clean(self):
        strat = load_strategy("ut_bot_atr", {})
        self.assertEqual(strat.validate_config(), [])

    def test_invalid_config_is_flagged_as_must(self):
        strat = load_strategy("ut_bot_atr", {"key_value": -1.0})
        warns = strat.validate_config()
        self.assertTrue(any("must" in w for w in warns))

    def _signal(self, df, cfg=None, has_position=False, position_side=None):
        strat = load_strategy("ut_bot_atr", cfg or {})
        ctx = MarketContext(
            symbol="SOLUSDT", timeframe_primary="15m", df_primary=df,
            current_price=float(df["close"].iloc[-1]),
            has_position=has_position, position_side=position_side,
        )
        return strat.analyze(ctx)

    def test_hold_when_not_enough_bars(self):
        sig = self._signal(_trend_df(50))
        self.assertEqual(sig.action, "HOLD")

    def test_buys_in_an_uptrend(self):
        sig = self._signal(_trend_df())
        self.assertEqual(sig.action, "BUY")
        self.assertFalse(sig.is_short)
        self.assertGreater(sig.stop_loss_distance or 0.0, 0.0)

    def test_opens_short_in_a_downtrend_when_enabled(self):
        # open_short is action SELL with position_side SHORT.
        df = _vshape_df(400).iloc[:200].reset_index(drop=True)
        sig = self._signal(df, {"enable_short": True})
        self.assertEqual(sig.action, "SELL")
        self.assertTrue(sig.is_short, "downtrend should emit open_short")

    def test_short_disabled_holds_instead(self):
        df = _vshape_df(400).iloc[:200].reset_index(drop=True)
        sig = self._signal(df, {"enable_short": False})
        self.assertEqual(sig.action, "HOLD")

    def test_closes_short_when_flipping_up(self):
        # close_short is action BUY with position_side SHORT.
        sig = self._signal(_trend_df(), {"enable_short": True},
                           has_position=True, position_side="SHORT")
        self.assertEqual(sig.action, "BUY")
        self.assertTrue(sig.is_short)

    def test_min_atr_pct_blocks_entry_in_dead_chop(self):
        sig = self._signal(_trend_df(), {"min_atr_pct": 99.0})
        self.assertEqual(sig.action, "HOLD")


class TestUTBotIndicatorPlugin(unittest.TestCase):
    def test_registered(self):
        self.assertIn("ut_bot_atr", available_indicators())

    def test_mapped_for_strategy(self):
        loaded = indicators_for_strategy("ut_bot_atr")
        self.assertEqual([type(ind).__name__ for ind in loaded], ["UTBotATRIndicator"])

    def test_compute_matches_strategy_math(self):
        df = _trend_df()
        cfg = {"key_value": 1.0, "atr_period": 10}
        indicator = load_indicator("ut_bot_atr", cfg)
        via_indicator = indicator.compute(df, cfg)
        direct = compute_ut_bot_frame(df, cfg)
        pd.testing.assert_series_equal(via_indicator["ut_stop"], direct["ut_stop"])

    def test_display_config_declares_zone_and_lines(self):
        indicator = load_indicator("ut_bot_atr", {})
        cfg = indicator.display_config
        self.assertEqual(cfg["zone_column"], "ut_zone")
        self.assertIn("ut_stop", [line["key"] for line in cfg["lines"]])
        self.assertEqual(cfg["buy_zones"], ["LONG"])


if __name__ == "__main__":
    unittest.main()
