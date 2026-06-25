"""Tests for the SimpleScalp+ chart indicator plugin + chart-map wiring."""
import unittest

import numpy as np
import pandas as pd

from xauby.strategies.indicators.registry import (
    available_indicators,
    indicators_for_strategy,
    load_indicator,
)


def _frame(start: float, end: float, n: int = 220) -> pd.DataFrame:
    base = np.linspace(start, end, n)
    return pd.DataFrame(
        {
            "open": base,
            "high": base + 0.6,
            "low": base - 0.6,
            "close": base,
            "volume": np.linspace(1000, 3000, n),
        }
    )


class TestSimpleScalpPlusIndicator(unittest.TestCase):
    def setUp(self) -> None:
        self.ind = load_indicator("simple_scalp_plus", {})
        self.df = _frame(100, 160)

    def test_registered(self):
        self.assertIn("simple_scalp_plus", available_indicators())

    def test_compute_appends_columns(self):
        out = self.ind.compute(self.df, {})
        for col in ("hull", "vwap", "ema_fast", "ema_slow", "scalp_zone", "buy_count", "sell_count"):
            self.assertIn(col, out.columns)
        self.assertEqual(len(out), len(self.df))

    def test_zone_values_valid(self):
        out = self.ind.compute(self.df, {})
        self.assertTrue(set(out["scalp_zone"].unique()).issubset({"BULL", "BEAR", "NEUTRAL"}))
        # A clean uptrend should resolve to a BULL zone on the last bar.
        self.assertEqual(out["scalp_zone"].iloc[-1], "BULL")

    def test_snapshot_and_panel(self):
        snap = self.ind.snapshot(self.df, {})
        self.assertEqual(snap.get("scalp_zone"), "BULL")
        items = self.ind.panel_items(snap)
        self.assertTrue(items)
        # First row is the canonical buy-zone row.
        self.assertEqual(items[0]["label"], "Scalp Zone")
        self.assertTrue(items[0]["ok"])

    def test_chart_map_resolves_to_plugin(self):
        plugins = indicators_for_strategy("simple_scalp_plus")
        self.assertEqual([type(p).__name__ for p in plugins], ["SimpleScalpPlusIndicator"])

    def test_legend_entries_present(self):
        legend = self.ind.legend_entries()
        labels = {row[1] for row in legend}
        self.assertIn("Buy zone", labels)
        self.assertIn("Hull MA", labels)
        self.assertIn("VWAP", labels)


if __name__ == "__main__":
    unittest.main()
