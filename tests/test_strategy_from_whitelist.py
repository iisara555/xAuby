import json
import os
import tempfile
import unittest

import yaml

from xauby.runtime.config_error import ConfigError
from xauby.runtime.trading_config import strategy_name_for_symbol


class TestStrategyFromWhitelist(unittest.TestCase):
    def test_legacy_prefers_yaml_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            wl = {
                "assets": [
                    {"symbol": "XAUT", "enabled": True, "strategy": "supertrend_ema200"},
                ]
            }
            with open(os.path.join(tmp, "coin_whitelist.json"), "w", encoding="utf-8") as f:
                json.dump(wl, f)
            cfg = {
                "architecture": {"whitelist_strict": False},
                "strategy": {
                    "active": "cdc_action_zone",
                    "symbols": {"XAUTUSDT": {"strategy": "cdc_action_zone"}},
                },
            }
            name = strategy_name_for_symbol(
                cfg,
                "XAUTUSDT",
                project_root=tmp,
            )
            self.assertEqual(name, "cdc_action_zone")

    def test_strict_reads_whitelist_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            wl = {
                "assets": [
                    {"symbol": "BTC", "enabled": True, "strategy": "supertrend_ema200"},
                ]
            }
            with open(os.path.join(tmp, "coin_whitelist.json"), "w", encoding="utf-8") as f:
                json.dump(wl, f)
            cfg = {
                "architecture": {"whitelist_strict": True},
                "strategy": {
                    "active": "cdc_action_zone",
                    "symbols": {"BTCUSDT": {"strategy": "cdc_action_zone"}},
                },
            }
            name = strategy_name_for_symbol(
                cfg,
                "BTCUSDT",
                project_root=tmp,
                strict=True,
            )
            self.assertEqual(name, "supertrend_ema200")

    def test_strict_missing_strategy_raises(self):
        cfg = {"architecture": {"whitelist_strict": True}}
        with tempfile.TemporaryDirectory() as tmp:
            wl = {"assets": [{"symbol": "XAUT", "enabled": True}]}
            with open(os.path.join(tmp, "coin_whitelist.json"), "w", encoding="utf-8") as f:
                json.dump(wl, f)
            with self.assertRaises(ConfigError):
                strategy_name_for_symbol(
                    cfg,
                    "XAUTUSDT",
                    project_root=tmp,
                    strict=True,
                )
