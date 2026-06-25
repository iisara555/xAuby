import json
import os
import tempfile
import unittest

import yaml

from xauby.runtime.config_error import ConfigError
from xauby.runtime.pair_registry import PairRegistry


class TestPairRegistryStrict(unittest.TestCase):
    def _write(self, root: str, *, strict: bool, assets: list, yaml_pairs=None):
        cfg = {
            "architecture": {"whitelist_strict": strict, "sync_yaml_pairs_from_whitelist": False},
            "trading": {"timeframe": "4h"},
            "strategy_mode": {"active": "trend_only", "trend_only": {"confirm_timeframe": "1d"}},
            "strategy": {
                "active": "cdc_action_zone",
                "symbols": {
                    "EXTRAUSDT": {"strategy": "cdc_action_zone"},
                },
            },
            "data": {
                "pairs": yaml_pairs or ["EXTRAUSDT"],
                "hybrid_dynamic_coin_config": {"require_supported_market": False},
            },
        }
        with open(os.path.join(root, "bot_config.yaml"), "w", encoding="utf-8") as f:
            yaml.dump(cfg, f)
        wl = {"version": 1, "quote_asset": "USDT", "assets": assets}
        with open(os.path.join(root, "coin_whitelist.json"), "w", encoding="utf-8") as f:
            json.dump(wl, f)

    def test_legacy_merges_yaml_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(
                tmp,
                strict=False,
                assets=[{"symbol": "XAUT", "enabled": True, "primary_timeframe": "4h", "confirm_timeframe": "1d", "strategy": "cdc_action_zone"}],
                yaml_pairs=["EXTRAUSDT"],
            )
            with open(os.path.join(tmp, "bot_config.yaml"), "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            reg = PairRegistry(cfg, project_root=tmp)
            active = {s.symbol for s in reg.load(None)}
            self.assertIn("XAUTUSDT", active)
            self.assertIn("EXTRAUSDT", active)

    def test_strict_whitelist_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(
                tmp,
                strict=True,
                assets=[
                    {
                        "symbol": "XAUT",
                        "enabled": True,
                        "primary_timeframe": "4h",
                        "confirm_timeframe": "1d",
                        "strategy": "cdc_action_zone",
                    }
                ],
                yaml_pairs=["EXTRAUSDT"],
            )
            with open(os.path.join(tmp, "bot_config.yaml"), "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            reg = PairRegistry(cfg, project_root=tmp)
            active = {s.symbol for s in reg.load(None)}
            self.assertEqual(active, {"XAUTUSDT"})

    def test_strict_missing_strategy_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(
                tmp,
                strict=True,
                assets=[{"symbol": "XAUT", "enabled": True, "primary_timeframe": "4h", "confirm_timeframe": "1d"}],
            )
            with open(os.path.join(tmp, "bot_config.yaml"), "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            reg = PairRegistry(cfg, project_root=tmp)
            with self.assertRaises(ConfigError):
                reg.load(None)

    def test_strict_hot_reload_keeps_previous_specs_when_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(
                tmp,
                strict=True,
                assets=[
                    {
                        "symbol": "XAUT",
                        "enabled": True,
                        "primary_timeframe": "4h",
                        "confirm_timeframe": "1d",
                        "strategy": "cdc_action_zone",
                    }
                ],
                yaml_pairs=["EXTRAUSDT"],
            )
            with open(os.path.join(tmp, "bot_config.yaml"), "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            reg = PairRegistry(cfg, project_root=tmp)
            reg._reload_interval = 0
            active = {s.symbol for s in reg.load(None)}
            self.assertEqual(active, {"XAUTUSDT"})

            wl_path = os.path.join(tmp, "coin_whitelist.json")
            with open(wl_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": 1,
                        "quote_asset": "USDT",
                        "assets": [
                            {
                                "symbol": "XAUT",
                                "enabled": True,
                                "primary_timeframe": "4h",
                                "confirm_timeframe": "1d",
                            }
                        ],
                    },
                    f,
                )
            os.utime(wl_path, None)

            self.assertFalse(reg.maybe_reload(None))
            self.assertEqual({s.symbol for s in reg.active()}, {"XAUTUSDT"})
