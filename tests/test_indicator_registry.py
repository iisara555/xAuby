import unittest

from xauby.strategies.indicators.registry import (
    available_indicators,
    indicators_for_strategy,
    load_indicator,
)


class TestIndicatorRegistry(unittest.TestCase):
    def test_discovers_plugins(self):
        names = available_indicators()
        self.assertIn("xauby_actionzone", names)
        self.assertNotIn("cdc_action_zone", names)
        self.assertIn("supertrend", names)
        self.assertIn("ema200", names)

    def test_load_unknown_raises(self):
        with self.assertRaises(KeyError):
            load_indicator("not_a_real_indicator")

    def test_load_cdc_plugin(self):
        ind = load_indicator("xauby_actionzone", {"ap_smoothing": 1})
        self.assertEqual(ind.name, "xauby_actionzone")
        self.assertIn("zones", ind.display_config)

    def test_legacy_indicator_ids_resolve_to_canonical_plugins(self):
        self.assertEqual(load_indicator("cdc_action_zone").name, "xauby_actionzone")
        self.assertEqual(load_indicator("donchian_trend").name, "xauby_donchian_trend")
        self.assertEqual(load_indicator("smc_luxalgo").name, "xauby_smc_pro")

    def test_default_strategy_chart_map_covers_all_strategy_indicators(self):
        cases = {
            "btc_ema_pullback": ["btc_ema_pullback"],
            "ict_lite_strategy": ["ict_lite"],
            "rsi2_meanrev": ["rsi2_meanrev"],
            "xauby_smc_pro": ["xauby_smc_pro"],
            "vol_breakout": ["vol_breakout"],
        }
        for strategy_name, expected_names in cases.items():
            indicators = indicators_for_strategy(strategy_name)
            self.assertEqual([ind.name for ind in indicators], expected_names)

    def test_legacy_strategy_chart_map_ids_resolve_to_canonical_indicators(self):
        self.assertEqual([ind.name for ind in indicators_for_strategy("cdc_action_zone")], ["xauby_actionzone"])
        self.assertEqual([ind.name for ind in indicators_for_strategy("donchian_trend")], ["xauby_donchian_trend"])
        self.assertEqual([ind.name for ind in indicators_for_strategy("smc_luxalgo")], ["xauby_smc_pro"])

    def test_unmapped_strategy_does_not_fall_back_to_cdc(self):
        self.assertEqual(indicators_for_strategy("unmapped_strategy"), [])
