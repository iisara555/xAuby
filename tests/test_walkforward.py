"""Walk-forward library (roadmap P1.1).

Two real bugs motivated this module, both found in my own throwaway harnesses
after they had already produced published numbers. The tests below exist to make
those two mistakes unrepresentable rather than merely documented.

1. A warmup lead-in that gets traded. Slicing 300 warmup bars while the replay
   only skips the strategy's default 100 means 200 bars of the previous window
   are counted again, so consecutive monthly windows overlap by about a month.
2. A variant that overrides part of a group of related keys, letting the rest
   leak in from the base config — which silently collapsed six D1 variants into
   two when the base config gained a new key.
"""
from __future__ import annotations

import unittest

import pandas as pd

from xauby.backtest.walkforward import (
    CONTROL_GROUPS,
    VariantSpecError,
    Window,
    WindowResult,
    aggregate,
    aggregate_by_phase,
    month_windows,
    resolve_variant,
    slice_window,
)

FOUR_HOURS_MS = 4 * 3_600_000


def _frame(start: str, bars: int, step_ms: int = FOUR_HOURS_MS) -> pd.DataFrame:
    origin = int(pd.Timestamp(start).timestamp() * 1000)
    times = [origin + i * step_ms for i in range(bars)]
    return pd.DataFrame({
        "open_time": times,
        "open": [100.0] * bars,
        "high": [101.0] * bars,
        "low": [99.0] * bars,
        "close": [100.0 + i * 0.1 for i in range(bars)],
        "volume": [10.0] * bars,
    })


class TestWarmupIsNeverTraded(unittest.TestCase):
    """The bug: slicing a warmup lead-in and then trading it."""

    def setUp(self):
        # Six months of 4h bars starting well before the window under test.
        self.df = _frame("2026-01-01", 6 * 30 * 6)

    def test_skip_bars_equals_the_warmup_actually_prepended(self):
        window = Window("2026-04", *self._bounds("2026-04-01", "2026-05-01"))
        sliced = slice_window(self.df, window, warmup_bars=300)
        self.assertIsNotNone(sliced)
        self.assertEqual(sliced.skip_bars, 300)
        # And the frame really does carry those extra bars.
        self.assertEqual(sliced.traded_bars, len(sliced.frame) - 300)

    def test_skip_bars_is_clamped_at_the_start_of_history(self):
        # First month: there is no prior data to warm on, so nothing is skipped
        # that does not exist.
        window = Window("2026-01", *self._bounds("2026-01-01", "2026-02-01"))
        sliced = slice_window(self.df, window, warmup_bars=300)
        self.assertEqual(sliced.skip_bars, 0)

    def test_warmup_comes_only_from_before_the_window(self):
        window = Window("2026-04", *self._bounds("2026-04-01", "2026-05-01"))
        sliced = slice_window(self.df, window, warmup_bars=300)
        warmup = sliced.frame.iloc[:sliced.skip_bars]
        self.assertTrue((warmup["open_time"] < window.start_ms).all(),
                        "warmup must be past data only, never look-ahead")

    def test_window_is_half_open(self):
        window = Window("2026-04", *self._bounds("2026-04-01", "2026-05-01"))
        sliced = slice_window(self.df, window, warmup_bars=0)
        self.assertTrue((sliced.frame["open_time"] >= window.start_ms).all())
        self.assertTrue((sliced.frame["open_time"] < window.end_ms).all())

    def test_run_slice_cannot_be_called_without_the_skip(self):
        # The regression guard: min_bars_override is read off the slice, so
        # there is no argument a caller can omit.
        import inspect

        from xauby.backtest.walkforward import run_slice

        params = inspect.signature(run_slice).parameters
        self.assertNotIn("min_bars_override", params)
        self.assertNotIn("skip_bars", params)

    def test_run_slice_forwards_the_skip(self):
        from unittest import mock

        from xauby.backtest import walkforward

        window = Window("2026-04", *self._bounds("2026-04-01", "2026-05-01"))
        sliced = slice_window(self.df, window, warmup_bars=300)
        captured = {}

        def fake_replay(df, **kwargs):
            captured.update(kwargs)
            return {"total_trades": 0, "net_profit_pct": 0.0}

        with mock.patch("xauby.backtest.replay.run_plugin_replay", fake_replay):
            walkforward.run_slice(
                sliced, strategy_name="s", strategy_config={},
                engine_config={}, symbol="XAUUSDT",
            )
        self.assertEqual(captured["min_bars_override"], 300)

    def test_empty_window_returns_none(self):
        window = Window("2030-01", *self._bounds("2030-01-01", "2030-02-01"))
        self.assertIsNone(slice_window(self.df, window, warmup_bars=300))

    @staticmethod
    def _bounds(start: str, end: str):
        return (int(pd.Timestamp(start).timestamp() * 1000),
                int(pd.Timestamp(end).timestamp() * 1000))


class TestVariantIsolation(unittest.TestCase):
    """The bug: a partial override letting the base config leak into a variant."""

    BASE = {
        "use_d1_regime_filter": True,
        "use_d1_regime_filter_long": False,
        "use_d1_regime_filter_short": None,
        "enable_short": True,
    }

    def test_partial_control_group_is_rejected(self):
        # Exactly the shape that collapsed six variants into two.
        with self.assertRaises(VariantSpecError) as ctx:
            resolve_variant(self.BASE, {"use_d1_regime_filter": False})
        message = str(ctx.exception)
        self.assertIn("use_d1_regime_filter_long", message)
        self.assertIn("inherited", message)

    def test_complete_control_group_is_accepted(self):
        merged = resolve_variant(self.BASE, {
            "use_d1_regime_filter": False,
            "use_d1_regime_filter_long": False,
            "use_d1_regime_filter_short": False,
        })
        self.assertFalse(merged["use_d1_regime_filter_long"])
        self.assertFalse(merged["use_d1_regime_filter_short"])

    def test_untouched_groups_still_inherit(self):
        merged = resolve_variant(self.BASE, {"enable_short": False})
        self.assertTrue(merged["use_d1_regime_filter"])
        self.assertFalse(merged["enable_short"])

    def test_partial_tp_is_also_a_control_group(self):
        # Same class of mistake, different keys: a partial TP pct without its
        # fraction inherits a stale fraction.
        with self.assertRaises(VariantSpecError):
            resolve_variant({"partial_tp_pct": 0.0, "partial_tp_fraction": 0.5},
                            {"partial_tp_pct": 12.0})

    def test_asymmetric_variants_stay_distinct(self):
        # The end-to-end property: two variants that differ only in which side
        # is gated must not resolve to the same config.
        gated_short = resolve_variant(self.BASE, {
            "use_d1_regime_filter": True,
            "use_d1_regime_filter_long": False,
            "use_d1_regime_filter_short": True,
        })
        gated_long = resolve_variant(self.BASE, {
            "use_d1_regime_filter": True,
            "use_d1_regime_filter_long": True,
            "use_d1_regime_filter_short": False,
        })
        self.assertNotEqual(gated_short, gated_long)

    def test_control_groups_cover_the_keys_that_caused_the_bug(self):
        self.assertIn("use_d1_regime_filter_long", CONTROL_GROUPS["d1_gate"])
        self.assertIn("use_d1_regime_filter_short", CONTROL_GROUPS["d1_gate"])


class TestMonthWindows(unittest.TestCase):
    def test_complete_months_only_by_default(self):
        # Ends mid-month: that month must be dropped.
        df = _frame("2026-01-01", 6 * 30 * 3 + 6 * 10)
        labels = [w.label for w in month_windows(df)]
        self.assertNotIn("2026-04", labels)
        self.assertIn("2026-03", labels)

    def test_partial_month_kept_when_requested(self):
        df = _frame("2026-01-01", 6 * 30 * 3 + 6 * 10)
        labels = [w.label for w in month_windows(df, complete_only=False)]
        self.assertIn("2026-04", labels)

    def test_windows_are_sorted_and_contiguous(self):
        df = _frame("2026-01-01", 6 * 30 * 3)
        windows = month_windows(df, complete_only=False)
        self.assertEqual([w.label for w in windows], sorted(w.label for w in windows))
        for earlier, later in zip(windows, windows[1:]):
            self.assertEqual(earlier.end_ms, later.start_ms, "windows must not gap or overlap")

    def test_first_filter(self):
        df = _frame("2026-01-01", 6 * 30 * 3)
        labels = [w.label for w in month_windows(df, complete_only=False, first="2026-02")]
        self.assertNotIn("2026-01", labels)


class TestAggregation(unittest.TestCase):
    @staticmethod
    def _result(label, net, trades=1, phase="bull"):
        return WindowResult(window=Window(label, 0, 1),
                            stats={"net_profit_pct": net, "total_trades": trades},
                            phase=phase)

    def test_compounding_not_summing(self):
        agg = aggregate([self._result("a", 10.0), self._result("b", 10.0)])
        self.assertAlmostEqual(agg["compounded_pct"], 21.0, places=2)

    def test_counts_and_extremes(self):
        agg = aggregate([self._result("a", 5.0), self._result("b", -3.0),
                         self._result("c", 0.0)])
        self.assertEqual(agg["windows"], 3)
        self.assertEqual(agg["positive_windows"], 1)
        self.assertEqual(agg["worst_window_pct"], -3.0)
        self.assertEqual(agg["best_window_pct"], 5.0)

    def test_empty_is_not_an_error(self):
        self.assertEqual(aggregate([])["windows"], 0)

    def test_phase_buckets_plus_all(self):
        results = [self._result("a", 5.0, phase="bull"),
                   self._result("b", -2.0, phase="bear"),
                   self._result("c", 1.0, phase="bull")]
        by_phase = aggregate_by_phase(results)
        self.assertEqual(by_phase["bull"]["windows"], 2)
        self.assertEqual(by_phase["bear"]["windows"], 1)
        self.assertEqual(by_phase["ALL"]["windows"], 3)
        self.assertNotIn("sideways", by_phase)


if __name__ == "__main__":
    unittest.main()
