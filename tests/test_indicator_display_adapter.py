"""IndicatorDisplayAdapter tests."""
import unittest

import pandas as pd

from xauby.ui.indicator_display import build_indicator_display_payload, build_indicator_view


class TestIndicatorDisplayAdapter(unittest.TestCase):
    def _sample_df(self) -> pd.DataFrame:
        n = 120
        close = [100.0 + i * 0.1 for i in range(n)]
        return pd.DataFrame(
            {
                "open": close,
                "high": [c + 1 for c in close],
                "low": [c - 1 for c in close],
                "close": close,
                "volume": [1000.0] * n,
            }
        )

    def test_build_cdc_view(self):
        view = build_indicator_view(self._sample_df(), "cdc_action_zone", {})
        self.assertEqual(view.strategy_name, "cdc_action_zone")
        self.assertIn("zone", view.df.columns)
        payload = build_indicator_display_payload(view)
        self.assertEqual(payload["strategy"], "cdc_action_zone")
        self.assertIn("metrics", payload)

    def test_build_supertrend_view(self):
        view = build_indicator_view(self._sample_df(), "supertrend_ema200", {})
        self.assertEqual(view.strategy_name, "supertrend_ema200")

    def test_production_candidate_views_are_strategy_specific(self):
        rsi2 = build_indicator_view(self._sample_df(), "rsi2_meanrev", {})
        vol = build_indicator_view(self._sample_df(), "vol_breakout", {})

        self.assertIn("rsi2_zone", rsi2.df.columns)
        self.assertIn("ema200", rsi2.df.columns)
        self.assertNotIn("ema12", rsi2.df.columns)
        self.assertIn("vol_breakout_zone", vol.df.columns)
        self.assertIn("range_high", vol.df.columns)
        self.assertNotIn("ema12", vol.df.columns)

    def test_zone_metric_is_not_rendered_twice(self):
        for strategy in ("cdc_action_zone", "supertrend_ema200"):
            view = build_indicator_view(self._sample_df(), strategy, {})
            labels = [item["label"] for item in view.panel_items]
            zone_label = "Zone" if strategy == "cdc_action_zone" else "ST Zone"
            self.assertEqual(labels.count(zone_label), 1)


if __name__ == "__main__":
    unittest.main()
