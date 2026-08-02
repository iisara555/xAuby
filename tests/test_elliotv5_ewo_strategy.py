"""Tests for the ElliotV5/SMAOffset (EWO) strategy + indicator plugins.

The unit-conversion test is the important one: freqtrade expresses `minimal_roi`
as fractions and this engine expects percent, so a verbatim copy of the upstream
ladder would set a 0.099% take-profit and exit on noise.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.runtime.exits import resolve_minimal_roi, validate_exit_config
from xauby.strategies.context import MarketContext
from xauby.strategies.elliotv5_ewo.indicators import (
    compute_elliotv5_frame,
    elliott_wave_oscillator,
    elliotv5_snapshot,
)
from xauby.strategies.indicators.registry import (
    available_indicators,
    indicators_for_strategy,
    load_indicator,
)
from xauby.strategies.registry import available_strategies, load_strategy

BAR = 3600  # 1h


def _df(n: int = 400, seed: int = 5, drift: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))
    return pd.DataFrame({
        "open": np.concatenate([[close[0]], close[:-1]]),
        "high": close * (1 + np.abs(rng.normal(0, 0.004, n))),
        "low": close * (1 - np.abs(rng.normal(0, 0.004, n))),
        "close": close,
        "volume": rng.uniform(500, 2000, n),
        "timestamp": 1_600_000_000 + np.arange(n) * BAR,
    })


def _uptrend_then_dip(n: int = 400) -> pd.DataFrame:
    """Sustained rise (positive EWO) ending in a dip below the MA offset."""
    price = 100 + 0.35 * np.arange(n)
    price[-4:] = price[-5] * 0.93      # sharp dip under the buy line
    return pd.DataFrame({
        "open": np.concatenate([[price[0]], price[:-1]]),
        "high": price * 1.001, "low": price * 0.999, "close": price,
        "volume": np.full(n, 1000.0),
        "timestamp": 1_600_000_000 + np.arange(n) * BAR,
    })


class TestMinimalRoiUnits(unittest.TestCase):
    """freqtrade fractions vs xauby percent — the trap this port must not fall in."""

    def test_default_ladder_is_percent_not_fraction(self):
        cfg = load_strategy("elliotv5_ewo", {}).config
        steps = resolve_minimal_roi(cfg)
        self.assertTrue(steps, "ladder must resolve")
        age0, roi0 = steps[0]
        self.assertEqual(age0, 0.0)
        # freqtrade publishes 0.099; percent is 9.9. A 0.099 here would take
        # profit at a tenth of a percent.
        self.assertAlmostEqual(roi0, 9.9, places=6)

    def test_ladder_is_monotonically_decreasing_and_sorted(self):
        steps = resolve_minimal_roi(load_strategy("elliotv5_ewo", {}).config)
        ages = [a for a, _ in steps]
        rois = [r for _, r in steps]
        self.assertEqual(ages, sorted(ages))
        self.assertEqual(rois, sorted(rois, reverse=True))

    def test_terminal_rung_survives_the_roi_gt_zero_filter(self):
        """freqtrade's `"119": 0` would be dropped; a small positive keeps it."""
        steps = resolve_minimal_roi(load_strategy("elliotv5_ewo", {}).config)
        self.assertEqual(steps[-1][0], 119.0)
        self.assertGreater(steps[-1][1], 0.0)

    def test_validate_config_flags_a_fraction_ladder(self):
        strat = load_strategy("elliotv5_ewo", {
            "minimal_roi": {"0": 0.099, "10": 0.033},
        })
        warns = strat.validate_config()
        self.assertTrue(any("PERCENT" in w for w in warns), warns)

    def test_default_config_does_not_trip_the_exit_guard(self):
        # minimal_roi without partial_tp must be accepted at startup.
        validate_exit_config(load_strategy("elliotv5_ewo", {}).config,
                             symbol="SOLUSDT")


class TestEWOMath(unittest.TestCase):
    def test_ewo_is_positive_in_an_uptrend(self):
        close = pd.Series(100 + 0.5 * np.arange(300, dtype="float64"))
        ewo = elliott_wave_oscillator(close, 50, 200)
        self.assertGreater(float(ewo.iloc[-1]), 0.0)

    def test_ewo_is_negative_in_a_downtrend(self):
        close = pd.Series(300 - 0.5 * np.arange(300, dtype="float64"))
        ewo = elliott_wave_oscillator(close, 50, 200)
        self.assertLess(float(ewo.iloc[-1]), 0.0)

    def test_ewo_is_a_percentage_of_price(self):
        """Scaling price must not change EWO — it is normalised by close."""
        base = pd.Series(100 + 0.5 * np.arange(300, dtype="float64"))
        a = float(elliott_wave_oscillator(base, 50, 200).iloc[-1])
        b = float(elliott_wave_oscillator(base * 37.0, 50, 200).iloc[-1])
        self.assertAlmostEqual(a, b, places=6)

    def test_frame_columns(self):
        frame = compute_elliotv5_frame(_df(), {})
        for col in ("ewo", "ma_buy", "ma_sell", "buy_line", "sell_line",
                    "rsi", "atr", "ewo_zone"):
            self.assertIn(col, frame.columns)

    def test_offsets_bracket_the_moving_averages(self):
        frame = compute_elliotv5_frame(_df(), {}).dropna(
            subset=["buy_line", "sell_line", "ma_buy", "ma_sell"])
        self.assertGreater(len(frame), 50)
        self.assertTrue((frame["buy_line"] < frame["ma_buy"]).all())
        self.assertTrue((frame["sell_line"] > frame["ma_sell"]).all())

    def test_empty_frame_is_safe(self):
        self.assertTrue(compute_elliotv5_frame(pd.DataFrame(), {}).empty)

    def test_is_causal(self):
        df = _df(400)
        full = compute_elliotv5_frame(df, {})
        cut = 300
        prefix = compute_elliotv5_frame(df.iloc[:cut].copy(), {})
        self.assertAlmostEqual(float(full["buy_line"].iloc[cut - 1]),
                               float(prefix["buy_line"].iloc[-1]), places=9)


class TestElliotV5Strategy(unittest.TestCase):
    def test_registered_and_research_maturity(self):
        self.assertIn("elliotv5_ewo", available_strategies())
        self.assertEqual(load_strategy("elliotv5_ewo", {}).maturity, "research")

    def test_default_config_validates_clean(self):
        self.assertEqual(load_strategy("elliotv5_ewo", {}).validate_config(), [])

    def test_invalid_config_is_flagged_as_must(self):
        warns = load_strategy("elliotv5_ewo", {"slow_ewo": 10, "fast_ewo": 50}).validate_config()
        self.assertTrue(any("must" in w for w in warns))

    def _signal(self, df, cfg=None, has_position=False):
        strat = load_strategy("elliotv5_ewo", cfg or {})
        ctx = MarketContext(
            symbol="SOLUSDT", timeframe_primary="1h", df_primary=df,
            current_price=float(df["close"].iloc[-1]), has_position=has_position,
        )
        return strat.analyze(ctx)

    def test_hold_when_not_enough_bars(self):
        self.assertEqual(self._signal(_df(100)).action, "HOLD")

    def test_buys_the_dip_in_an_impulse(self):
        sig = self._signal(_uptrend_then_dip())
        self.assertEqual(sig.action, "BUY")
        self.assertFalse(sig.is_short)
        self.assertGreater(sig.stop_loss_distance or 0.0, 0.0)

    def test_never_emits_a_short(self):
        """The original is a spot long-only strategy."""
        for seed in range(6):
            for pos in (False, True):
                sig = self._signal(_df(400, seed=seed, drift=-0.004), has_position=pos)
                self.assertFalse(sig.is_short, f"seed={seed} pos={pos}")

    def test_exits_above_the_sell_offset(self):
        # Rising series ends well above the sell line.
        n = 400
        price = 100 + 0.4 * np.arange(n)
        df = pd.DataFrame({
            "open": price, "high": price * 1.002, "low": price * 0.998,
            "close": price, "volume": np.full(n, 1000.0),
            "timestamp": 1_600_000_000 + np.arange(n) * BAR,
        })
        self.assertEqual(self._signal(df, has_position=True).action, "SELL")

    def test_sl_confirmed_exits_first(self):
        strat = load_strategy("elliotv5_ewo", {})
        df = _uptrend_then_dip()
        ctx = MarketContext(
            symbol="SOLUSDT", timeframe_primary="1h", df_primary=df,
            current_price=float(df["close"].iloc[-1]),
            has_position=True, sl_confirmed=True,
        )
        sig = strat.analyze(ctx)
        self.assertEqual(sig.action, "SELL")
        self.assertIn("SL confirmed", sig.reason)

    def test_rsi_buy_gate_blocks_a_hot_entry(self):
        sig = self._signal(_uptrend_then_dip(), {"rsi_buy": 0.0, "ewo_low": -999.0})
        self.assertEqual(sig.action, "HOLD")


class TestElliotV5IndicatorPlugin(unittest.TestCase):
    def test_registered_and_mapped(self):
        self.assertIn("elliotv5_ewo", available_indicators())
        loaded = indicators_for_strategy("elliotv5_ewo")
        self.assertEqual([type(i).__name__ for i in loaded], ["ElliotV5EWOIndicator"])

    def test_compute_matches_strategy_math(self):
        df = _df()
        via = load_indicator("elliotv5_ewo", {}).compute(df, {})
        direct = compute_elliotv5_frame(df, {})
        pd.testing.assert_series_equal(via["ewo"], direct["ewo"])

    def test_display_config(self):
        cfg = load_indicator("elliotv5_ewo", {}).display_config
        self.assertEqual(cfg["zone_column"], "ewo_zone")
        keys = [line["key"] for line in cfg["lines"]]
        self.assertIn("buy_line", keys)
        self.assertIn("sell_line", keys)


if __name__ == "__main__":
    unittest.main()
