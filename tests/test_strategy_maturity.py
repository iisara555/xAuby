"""Maturity is declared, and an undeclared plugin never reaches live money.

Before this, "is this plugin allowed near real capital" was answered by looking
for the string `research` in `tags` — a free-text list that also holds `btc`,
`4h` and `ema`. 16 of 17 plugins sat at version 0.1.0 with no certificate, and
any of them could be promoted to a live pair by editing a whitelist.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xauby.strategies import available_strategies, load_strategy  # noqa: E402
from xauby.strategies.base import MATURITIES, strategy_maturity  # noqa: E402


class _Plugin:
    name = "fake"
    tags: list = []
    maturity = None


class TestResolution(unittest.TestCase):
    def test_declaration_wins(self):
        plugin = _Plugin()
        plugin.tags = ["research"]
        plugin.maturity = "production"
        self.assertEqual(strategy_maturity(plugin), "production")

    def test_legacy_tags_still_work(self):
        # No existing plugin changes behaviour just because the field was added.
        research = _Plugin()
        research.tags = ["btc", "research", "1h"]
        self.assertEqual(strategy_maturity(research), "research")
        paper = _Plugin()
        paper.tags = ["btc", "paper-test"]
        self.assertEqual(strategy_maturity(paper), "paper")

    def test_undeclared_is_none_not_production(self):
        # The distinction the whole gate rests on: nobody has answered, which is
        # not the same as answering "yes".
        plugin = _Plugin()
        plugin.tags = ["btc", "4h", "ema"]
        self.assertIsNone(strategy_maturity(plugin))

    def test_an_unknown_value_is_rejected(self):
        plugin = _Plugin()
        plugin.maturity = "probably fine"
        with self.assertRaises(ValueError):
            strategy_maturity(plugin)

    def test_maturity_is_published_in_metadata(self):
        strategy = load_strategy("supertrend_ema200", {})
        self.assertEqual(strategy.get_meta().maturity, "production")
        self.assertEqual(strategy.describe().get("maturity"), "production")


class TestDeclaredPlugins(unittest.TestCase):
    def test_the_strategies_that_trade_live_are_declared_production(self):
        for name in ("supertrend_ema200", "cdc_action_zone"):
            with self.subTest(strategy=name):
                self.assertEqual(
                    strategy_maturity(load_strategy(name, {})), "production")

    def test_research_mirrors_stay_research(self):
        for name in (
            "supertrend_short",
            "rsi2_short",
            "donchian_short",
            "xauby_smc_pro",
        ):
            with self.subTest(strategy=name):
                self.assertEqual(
                    strategy_maturity(load_strategy(name, {})), "research")

    def test_every_declared_value_is_legal(self):
        for name in available_strategies():
            with self.subTest(strategy=name):
                try:
                    strategy = load_strategy(name, {})
                except Exception:  # a plugin that will not load is another test's problem
                    continue
                self.assertIn(strategy_maturity(strategy), (None,) + MATURITIES)

    def test_production_is_still_the_rare_case(self):
        # If this ever grows quietly, someone is declaring their way to live
        # rather than certifying their way there.
        production = []
        for name in available_strategies():
            try:
                strategy = load_strategy(name, {})
            except Exception:
                continue
            if strategy_maturity(strategy) == "production":
                production.append(strategy.name)
        self.assertEqual(sorted(set(production)), ["supertrend_ema200", "xauby_actionzone"])


class TestCatalogAgreement(unittest.TestCase):
    def test_every_live_approved_preset_names_a_production_strategy(self):
        # The SaaS side of the same rule: a tenant cannot be offered a live
        # preset built on a plugin nobody has signed off.
        from xauby.saas.preset_specs import PRESET_SPECS

        for spec in PRESET_SPECS:
            if not spec.get("live_certified"):
                continue
            with self.subTest(preset=spec["id"]):
                strategy = load_strategy(str(spec["strategy"]), {})
                self.assertEqual(strategy_maturity(strategy), "production")


if __name__ == "__main__":
    unittest.main()
