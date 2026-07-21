"""Tests for per-symbol strategy selection."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.strategies.registry import (
    available_strategy_manifests,
    available_strategies,
    load_strategy,
    strategy_manifest,
)


class TestPairStrategySelection(unittest.TestCase):
    def test_strategy_manifest_is_marketplace_ready(self):
        manifest = strategy_manifest("xauby_actionzone")

        self.assertEqual(manifest["manifest_version"], 1)
        self.assertEqual(manifest["id"], "xauby_actionzone")
        self.assertEqual(manifest["name"], "xAuby ActionZone")
        self.assertEqual(manifest["source"], "builtin")
        self.assertIn("4h", manifest["required_timeframes"])
        self.assertIn("default_config", manifest)
        self.assertEqual(
            manifest["permissions"],
            {"network": False, "database": False, "orders": False},
        )

    def test_available_strategy_manifests_cover_registered_plugins(self):
        manifests = available_strategy_manifests()
        ids = {m["id"] for m in manifests}

        self.assertIn("xauby_actionzone", ids)
        self.assertIn("xauby_donchian_trend", ids)
        self.assertIn("xauby_smc_pro", ids)
        self.assertNotIn("cdc_action_zone", ids)
        self.assertNotIn("donchian_trend", ids)
        self.assertEqual(ids, set(available_strategies()))

    def test_legacy_strategy_ids_resolve_to_canonical_plugins(self):
        self.assertEqual(strategy_manifest("cdc_action_zone")["id"], "xauby_actionzone")
        self.assertEqual(strategy_manifest("donchian_trend")["id"], "xauby_donchian_trend")
        self.assertEqual(strategy_manifest("smc_luxalgo")["id"], "xauby_smc_pro")

        self.assertEqual(load_strategy("cdc_action_zone").name, "xauby_actionzone")
        self.assertEqual(load_strategy("donchian_trend").name, "xauby_donchian_trend")
        self.assertEqual(load_strategy("smc_luxalgo").name, "xauby_smc_pro")

    def test_ict_lite_strategy_is_registered_and_loadable(self):
        self.assertIn("ict_lite_strategy", available_strategies())

        strategy = load_strategy(
            "ict_lite_strategy",
            {"ema_fast": 20, "ema_slow": 50, "sl_atr_mult": 3.0},
        )

        self.assertEqual(strategy.name, "ict_lite_strategy")
        self.assertEqual(strategy.config["sl_atr_mult"], 3.0)
        self.assertEqual(strategy.required_timeframes, ["4h"])

    def test_btc_ema_pullback_strategy_is_registered_and_loadable(self):
        self.assertIn("btc_ema_pullback", available_strategies())

        strategy = load_strategy(
            "btc_ema_pullback",
            {"ema_fast": 12, "ema_slow": 34, "ema_trend": 100},
        )

        self.assertEqual(strategy.name, "btc_ema_pullback")
        self.assertEqual(strategy.config["ema_fast"], 12)
        self.assertEqual(strategy.required_timeframes, ["1h"])

    def test_supertrend_ema200_strategy_is_registered_and_loadable(self):
        self.assertIn("supertrend_ema200", available_strategies())

        strategy = load_strategy(
            "supertrend_ema200",
            {"ema_period": 200, "supertrend_mult": 2.5},
        )

        self.assertEqual(strategy.name, "supertrend_ema200")
        self.assertEqual(strategy.config["supertrend_mult"], 2.5)
        self.assertEqual(strategy.required_timeframes, ["4h"])


if __name__ == "__main__":
    unittest.main()
