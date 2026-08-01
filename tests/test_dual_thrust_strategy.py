"""Tests for the Dual Thrust strategy + indicator plugins.

The look-ahead guard is the important one here: a session's breakout lines must
be built from *prior* sessions only. A range system that can see its own
session's high/low backtests beautifully and is worthless live.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.strategies.context import MarketContext
from xauby.strategies.dual_thrust.indicators import (
    compute_dual_thrust_frame,
    dual_thrust_snapshot,
)
from xauby.strategies.indicators.registry import (
    available_indicators,
    indicators_for_strategy,
    load_indicator,
)
from xauby.strategies.registry import available_strategies, load_strategy

BAR_SECONDS = 900          # 15m
SESSION_BARS = 96          # one UTC day


def _df(n: int = 800, seed: int = 11, drift: float = 0.0) -> pd.DataFrame:
    """Random-walk 15m bars aligned to UTC midnight."""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(drift, 0.003, n)))
    return pd.DataFrame(
        {
            "open": np.concatenate([[close[0]], close[:-1]]),
            "high": close * (1 + np.abs(rng.normal(0, 0.002, n))),
            "low": close * (1 - np.abs(rng.normal(0, 0.002, n))),
            "close": close,
            "volume": rng.uniform(500, 2000, n),
            # 1_600_000_000 is not midnight; snap to a day boundary so sessions
            # line up with whole days.
            "timestamp": 1_600_041_600 + np.arange(n) * BAR_SECONDS,
        }
    )


def _breakout_df(n: int = 500) -> pd.DataFrame:
    """Quiet sessions then a decisive upward break in the final session."""
    base = np.full(n, 100.0)
    noise = np.tile(np.array([0.0, 0.3, -0.3, 0.15]), n // 4 + 1)[:n]
    price = base + noise
    price[-40:] = 100 + np.linspace(0.5, 12.0, 40)   # break upward
    return pd.DataFrame(
        {
            "open": np.concatenate([[price[0]], price[:-1]]),
            "high": price + 0.2,
            "low": price - 0.2,
            "close": price,
            "volume": np.full(n, 1000.0),
            "timestamp": 1_600_041_600 + np.arange(n) * BAR_SECONDS,
        }
    )


class TestDualThrustLookAhead(unittest.TestCase):
    def test_lines_do_not_depend_on_future_bars(self):
        """Recomputing on a truncated prefix must reproduce the same lines.

        This is the test that catches a session peeking at its own range.
        """
        df = _df(800)
        cfg = {"lookback_days": 4}
        full = compute_dual_thrust_frame(df, cfg)
        for cut in (500, 620, 730):
            prefix = compute_dual_thrust_frame(df.iloc[:cut].copy(), cfg)
            for col in ("dt_buy_line", "dt_sell_line", "dt_range"):
                a = float(full[col].iloc[cut - 1])
                b = float(prefix[col].iloc[-1])
                if np.isnan(a) and np.isnan(b):
                    continue
                self.assertAlmostEqual(
                    a, b, places=9,
                    msg=f"{col} at cut={cut} depends on future bars",
                )

    def test_range_is_constant_within_a_session(self):
        """The range comes from prior sessions, so it cannot move intraday."""
        frame = compute_dual_thrust_frame(_df(800), {"lookback_days": 4})
        # Isolate exactly one session: from the last session start onward.
        starts = frame.index[frame["dt_bar_in_session"] == 0]
        self.assertGreater(len(starts), 5)
        one_session = frame.loc[starts[-2] : starts[-1] - 1]
        self.assertGreater(len(one_session), 10)
        ranges = one_session["dt_range"].dropna().unique()
        self.assertEqual(len(ranges), 1, "range changed mid-session")

    def test_warmup_sessions_have_no_lines(self):
        frame = compute_dual_thrust_frame(_df(800), {"lookback_days": 4})
        # The first lookback sessions cannot have a prior-N-session range.
        self.assertTrue(np.isnan(float(frame["dt_range"].iloc[0])))


class TestDualThrustIndicatorMath(unittest.TestCase):
    def test_frame_has_expected_columns(self):
        frame = compute_dual_thrust_frame(_df(500), {})
        for col in ("dt_session_open", "dt_range", "dt_buy_line",
                    "dt_sell_line", "dt_zone", "atr"):
            self.assertIn(col, frame.columns)

    def test_empty_frame_is_safe(self):
        self.assertTrue(compute_dual_thrust_frame(pd.DataFrame(), {}).empty)

    def test_buy_line_is_above_sell_line(self):
        frame = compute_dual_thrust_frame(_df(800), {"lookback_days": 4})
        valid = frame.dropna(subset=["dt_buy_line", "dt_sell_line"])
        self.assertGreater(len(valid), 100)
        self.assertTrue((valid["dt_buy_line"] > valid["dt_sell_line"]).all())

    def test_k_multipliers_widen_the_bands(self):
        df = _df(800)
        narrow = compute_dual_thrust_frame(df, {"k_upper": 0.3, "k_lower": 0.3})
        wide = compute_dual_thrust_frame(df, {"k_upper": 1.0, "k_lower": 1.0})
        i = -1
        self.assertGreater(
            float(wide["dt_buy_line"].iloc[i]), float(narrow["dt_buy_line"].iloc[i])
        )

    def test_snapshot_flags_upper_breakout(self):
        snap = dual_thrust_snapshot(_breakout_df(), {"lookback_days": 2})
        self.assertEqual(snap["zone"], "LONG_BREAK")


class TestDualThrustStrategy(unittest.TestCase):
    def test_registered(self):
        self.assertIn("dual_thrust", available_strategies())

    def test_maturity_is_research_not_production(self):
        self.assertEqual(load_strategy("dual_thrust", {}).maturity, "research")

    def test_default_config_validates_clean(self):
        self.assertEqual(load_strategy("dual_thrust", {}).validate_config(), [])

    def test_invalid_config_is_flagged_as_must(self):
        warns = load_strategy("dual_thrust", {"k_upper": 0.0}).validate_config()
        self.assertTrue(any("must" in w for w in warns))

    def _signal(self, df, cfg=None, has_position=False, position_side=None):
        strat = load_strategy("dual_thrust", cfg or {})
        ctx = MarketContext(
            symbol="SOLUSDT", timeframe_primary="15m", df_primary=df,
            current_price=float(df["close"].iloc[-1]),
            has_position=has_position, position_side=position_side,
        )
        return strat.analyze(ctx)

    def test_hold_when_not_enough_bars(self):
        self.assertEqual(self._signal(_df(100)).action, "HOLD")

    def test_buys_on_upper_breakout(self):
        # one_entry_per_session off: this session already broke out on an
        # earlier bar, which the guarded case asserts separately below.
        sig = self._signal(
            _breakout_df(), {"lookback_days": 2, "one_entry_per_session": False}
        )
        self.assertEqual(sig.action, "BUY")
        self.assertFalse(sig.is_short)
        self.assertGreater(sig.stop_loss_distance or 0.0, 0.0)

    def test_holds_inside_the_range(self):
        # A flat series never leaves the band.
        n = 600
        price = np.full(n, 100.0) + np.tile([0.0, 0.05, -0.05, 0.02], n // 4)[:n]
        df = pd.DataFrame({
            "open": price, "high": price + 0.06, "low": price - 0.06,
            "close": price, "volume": np.full(n, 1000.0),
            "timestamp": 1_600_041_600 + np.arange(n) * BAR_SECONDS,
        })
        self.assertEqual(self._signal(df, {"lookback_days": 2}).action, "HOLD")

    def test_one_entry_per_session_blocks_a_repeat_break(self):
        df = _breakout_df()
        first = self._signal(df, {"lookback_days": 2, "one_entry_per_session": False})
        self.assertEqual(first.action, "BUY")
        # With the guard on, an earlier bar in this session already broke out.
        guarded = self._signal(df, {"lookback_days": 2, "one_entry_per_session": True})
        self.assertEqual(guarded.action, "HOLD")

    def test_min_range_pct_blocks_entry(self):
        sig = self._signal(_breakout_df(), {"lookback_days": 2, "min_range_pct": 99.0})
        self.assertEqual(sig.action, "HOLD")

    def test_closes_short_when_upper_line_reclaimed(self):
        sig = self._signal(_breakout_df(), {"lookback_days": 2},
                           has_position=True, position_side="SHORT")
        self.assertEqual(sig.action, "BUY")   # close_short
        self.assertTrue(sig.is_short)


class TestDualThrustIndicatorPlugin(unittest.TestCase):
    def test_registered(self):
        self.assertIn("dual_thrust", available_indicators())

    def test_mapped_for_strategy(self):
        loaded = indicators_for_strategy("dual_thrust")
        self.assertEqual([type(i).__name__ for i in loaded], ["DualThrustIndicator"])

    def test_compute_matches_strategy_math(self):
        df = _df(500)
        cfg = {"lookback_days": 3}
        via = load_indicator("dual_thrust", cfg).compute(df, cfg)
        direct = compute_dual_thrust_frame(df, cfg)
        pd.testing.assert_series_equal(via["dt_buy_line"], direct["dt_buy_line"])

    def test_display_config_declares_zone_and_lines(self):
        cfg = load_indicator("dual_thrust", {}).display_config
        self.assertEqual(cfg["zone_column"], "dt_zone")
        keys = [line["key"] for line in cfg["lines"]]
        self.assertIn("dt_buy_line", keys)
        self.assertIn("dt_sell_line", keys)


if __name__ == "__main__":
    unittest.main()
