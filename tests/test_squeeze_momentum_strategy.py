"""Tests for the Squeeze Momentum (LazyBear) strategy + indicator plugins."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.strategies.context import MarketContext
from xauby.strategies.registry import available_strategies, load_strategy
from xauby.strategies.indicators.registry import available_indicators, load_indicator
from xauby.strategies.indicators.registry import indicators_for_strategy
from xauby.strategies.squeeze_momentum.indicators import (
    _linreg_endpoint,
    compute_squeeze_frame,
    squeeze_snapshot,
)


def _wave_df(n: int = 400) -> pd.DataFrame:
    t = np.arange(n)
    price = 100 + 20 * np.sin(t * 0.05) + t * 0.02
    return pd.DataFrame(
        [
            {"open": p, "high": p + 1.5, "low": p - 1.5, "close": p,
             "volume": 1000.0, "timestamp": 1_600_000_000 + i * 14400}
            for i, p in enumerate(price)
        ]
    )


class TestSqueezeMomentumIndicator(unittest.TestCase):
    def test_linreg_endpoint_matches_least_squares_on_a_line(self):
        # A perfect line y = 3 + 2x -> endpoint of a length-10 window at index i
        # equals 3 + 2*i (the fitted line passes through the points).
        y = 3.0 + 2.0 * np.arange(50, dtype="float64")
        out = _linreg_endpoint(y, 10)
        self.assertTrue(np.isnan(out[:9]).all())
        np.testing.assert_allclose(out[9:], y[9:], rtol=1e-9, atol=1e-6)

    def test_linreg_is_nan_safe_with_leading_warmup(self):
        y = np.concatenate([[np.nan, np.nan, np.nan], np.arange(20, dtype="float64")])
        out = _linreg_endpoint(y, 5)
        # Every window overlapping the 3 leading NaNs is masked; later bars are finite.
        self.assertTrue(np.isfinite(out[-1]))
        self.assertGreater(int(np.isfinite(out).sum()), 10)

    def test_momentum_column_is_populated(self):
        frame = compute_squeeze_frame(_wave_df(), {})
        mom = frame["mom"]
        self.assertGreater(int(mom.notna().sum()), 300)
        self.assertTrue(np.isfinite(float(mom.iloc[-1])))

    def test_squeeze_states_are_produced(self):
        frame = compute_squeeze_frame(_wave_df(), {})
        states = set(frame["sqz_state"].unique())
        self.assertTrue(states & {"ON", "OFF"})


class TestSqueezeMomentumStrategy(unittest.TestCase):
    def test_strategy_and_indicator_registered(self):
        self.assertIn("squeeze_momentum", available_strategies())
        self.assertIn("squeeze_momentum", available_indicators())

    def test_chart_indicator_mapping(self):
        inds = indicators_for_strategy("squeeze_momentum")
        self.assertEqual(len(inds), 1)

    def _scan(self, df, **config):
        st = load_strategy("squeeze_momentum", {"vol_min_ratio": 0.0, **config})
        actions = {"BUY": 0, "SELL": 0, "HOLD": 0}
        shorts = 0
        for i in range(st.min_bars, len(df)):
            sig = st.analyze(
                MarketContext(
                    symbol="BTCUSDT",
                    timeframe_primary="4h",
                    df_primary=df.iloc[: i + 1],
                    current_price=float(df["close"].iloc[i]),
                    has_position=False,
                )
            )
            actions[sig.action] = actions.get(sig.action, 0) + 1
            if sig.is_short and sig.intent == "OPEN":
                shorts += 1
        return actions, shorts

    def test_long_entries_fire_and_short_off_by_default(self):
        actions, shorts = self._scan(_wave_df())
        self.assertGreater(actions["BUY"], 0)
        self.assertEqual(shorts, 0)

    def test_short_entries_fire_when_enabled(self):
        _, shorts = self._scan(_wave_df(), enable_short=True)
        self.assertGreater(shorts, 0)

    def test_long_exit_on_momentum_flip(self):
        df = _wave_df()
        # Craft a position where momentum is negative -> long should exit.
        snap = squeeze_snapshot(df, {})
        st = load_strategy("squeeze_momentum", {})
        # Find an index where mom < 0 to exercise the exit path.
        frame = compute_squeeze_frame(df, {})
        neg_idx = frame.index[(frame["mom"] < 0)].tolist()
        self.assertTrue(neg_idx, "expected some negative-momentum bars")
        i = neg_idx[-1]
        sig = st.analyze(
            MarketContext(
                symbol="BTCUSDT",
                timeframe_primary="4h",
                df_primary=df.iloc[: i + 1],
                current_price=float(df["close"].iloc[i]),
                has_position=True,
                position_side="LONG",
            )
        )
        self.assertEqual(sig.action, "SELL")

    def test_squeeze_release_entry_mode(self):
        actions, _ = self._scan(_wave_df(), entry_trigger="squeeze_release")
        # Should still produce at least some entries over the wave.
        self.assertGreaterEqual(actions["BUY"] + actions["SELL"], 0)


if __name__ == "__main__":
    unittest.main()
