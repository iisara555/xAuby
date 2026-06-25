"""Tests for multi-pair PairRegistry and whitelist merge."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from xauby.runtime.pair_registry import (
    PairRegistry,
    PairSpec,
    _normalize_symbol,
    _normalize_tf,
)
from xauby.runtime.pair_config import (
    add_whitelist_asset,
    set_pair_timeframes,
    sync_data_pairs_yaml,
    load_whitelist,
)


class TestPairRegistry(unittest.TestCase):
    def setUp(self):
        # PairRegistry.load() honours DEFAULT_SYMBOL/XAUBY_FOCUS_SYMBOL from the
        # environment as a forced pair. A developer/CI .env (loaded by sibling
        # engine tests) must not leak into these temp-whitelist registries, or
        # exact-count assertions break. Snapshot and clear them for isolation.
        patcher = patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("DEFAULT_SYMBOL", None)
        os.environ.pop("XAUBY_FOCUS_SYMBOL", None)

    def _write_files(self, root: str, yaml_pairs=None, assets=None):
        cfg = {
            "trading": {"timeframe": "4h"},
            "strategy_mode": {"active": "trend_only", "trend_only": {"confirm_timeframe": "1d"}},
            "strategies": {"cdc_action_zone": {"use_d1_regime_filter": False}},
            "data": {
                "pairs": yaml_pairs or ["XAUTUSDT"],
                "hybrid_dynamic_coin_config": {
                    "hot_reload_enabled": True,
                    "reload_interval_seconds": 30,
                    "require_supported_market": False,
                },
            },
        }
        cfg_path = os.path.join(root, "bot_config.yaml")
        with open(cfg_path, "w", encoding="utf-8") as f:
            import yaml

            yaml.dump(cfg, f)
        wl = {
            "version": 1,
            "quote_asset": "USDT",
            "assets": assets
            or [
                {
                    "symbol": "XAUT",
                    "enabled": True,
                    "primary_timeframe": "4h",
                    "confirm_timeframe": "1d",
                },
                {
                    "symbol": "BTC",
                    "enabled": False,
                    "primary_timeframe": "1h",
                    "confirm_timeframe": "4h",
                },
            ],
        }
        wl_path = os.path.join(root, "coin_whitelist.json")
        with open(wl_path, "w", encoding="utf-8") as f:
            json.dump(wl, f)
        return cfg_path, wl_path

    def test_merge_whitelist_overrides_yaml_timeframes(self):
        with tempfile.TemporaryDirectory() as root:
            cfg_path, wl_path = self._write_files(root, yaml_pairs=["XAUTUSDT"])
            with open(cfg_path, "r", encoding="utf-8") as f:
                import yaml

                config = yaml.safe_load(f)
            reg = PairRegistry(config, config_path=cfg_path, project_root=root, whitelist_path=wl_path)
            active = reg.load(client=None)
            symbols = {s.symbol for s in active}
            self.assertIn("XAUTUSDT", symbols)
            self.assertNotIn("BTCUSDT", symbols)
            xaut = reg.get("XAUTUSDT")
            self.assertIsNotNone(xaut)
            self.assertEqual(xaut.primary_timeframe, "4h")
            self.assertEqual(xaut.confirm_timeframe, "1d")

    def test_hot_reload_detects_whitelist_change(self):
        with tempfile.TemporaryDirectory() as root:
            cfg_path, wl_path = self._write_files(root)
            with open(cfg_path, "r", encoding="utf-8") as f:
                import yaml

                config = yaml.safe_load(f)
            reg = PairRegistry(config, config_path=cfg_path, project_root=root, whitelist_path=wl_path)
            reg.load(client=None)
            self.assertEqual(len(reg.active()), 1)
            wl = load_whitelist(root, wl_path)
            for a in wl["assets"]:
                if a["symbol"] == "BTC":
                    a["enabled"] = True
            with open(wl_path, "w", encoding="utf-8") as f:
                json.dump(wl, f)
            os.utime(wl_path, (os.path.getmtime(wl_path) + 10, os.path.getmtime(wl_path) + 10))
            reg._last_mtime = 0
            reg._last_reload = 0
            changed = reg.maybe_reload(client=None)
            self.assertTrue(changed)
            self.assertEqual(len(reg.active()), 2)

    def test_hot_reload_reports_same_symbol_config_change(self):
        with tempfile.TemporaryDirectory() as root:
            cfg_path, wl_path = self._write_files(root, yaml_pairs=["XAUTUSDT"])
            with open(cfg_path, "r", encoding="utf-8") as f:
                import yaml

                config = yaml.safe_load(f)
            reg = PairRegistry(config, config_path=cfg_path, project_root=root, whitelist_path=wl_path)
            reg.load(client=None)
            wl = load_whitelist(root, wl_path)
            wl["assets"][0]["primary_timeframe"] = "1h"
            with open(wl_path, "w", encoding="utf-8") as f:
                json.dump(wl, f)
            reg._last_mtime = 0
            reg._last_reload = 0
            self.assertTrue(reg.maybe_reload(client=None))
            self.assertEqual(reg.active()[0].primary_timeframe, "1h")

    def test_mark_strategy_warnings_degraded(self):
        with tempfile.TemporaryDirectory() as root:
            cfg_path, wl_path = self._write_files(
                root,
                assets=[
                    {
                        "symbol": "BTC",
                        "enabled": True,
                        "primary_timeframe": "1h",
                        "confirm_timeframe": "15m",
                    }
                ],
            )
            with open(cfg_path, "r", encoding="utf-8") as f:
                import yaml

                config = yaml.safe_load(f)
            reg = PairRegistry(config, config_path=cfg_path, project_root=root, whitelist_path=wl_path)
            reg.load(client=None)
            reg.mark_strategy_warnings(["4h", "1d"])
            btc = reg.get("BTCUSDT")
            self.assertTrue(btc.degraded)
            self.assertIn("missing TF", btc.degrade_reason)

    def test_sync_data_pairs_yaml(self):
        with tempfile.TemporaryDirectory() as root:
            cfg_path, wl_path = self._write_files(root)
            sync_data_pairs_yaml(["XAUTUSDT", "ETHUSDT"], root, cfg_path)
            with open(cfg_path, "r", encoding="utf-8") as f:
                text = f.read()
            self.assertIn("XAUTUSDT", text)
            self.assertIn("ETHUSDT", text)
            self.assertIn("pairs:", text)

    def test_sync_data_pairs_yaml_block_list_no_orphans(self):
        """Replacing a multi-line block `pairs:` must not leave orphaned items.

        Reproduces the corruption where yaml.safe_dump wrote `data.pairs` as a
        block list and sync replaced only the key line, leaving `- XAUTUSDT`
        items that make yaml.safe_load raise (breaking chart/incident/backtest).
        """
        import yaml as _yaml

        with tempfile.TemporaryDirectory() as root:
            cfg_path = os.path.join(root, "bot_config.yaml")
            with open(cfg_path, "w", encoding="utf-8") as f:
                f.write(
                    "simulate_only: true\n"
                    "data:\n"
                    "  auto_detect_held_pairs: false\n"
                    "  pairs:\n"
                    "  - XAUTUSDT\n"
                    "  - BTCUSDT\n"
                    "  portfolio_guard:\n"
                    "    held_coins_only: false\n"
                    "architecture:\n"
                    "  indicator_registry_enabled: true\n"
                )
            sync_data_pairs_yaml(["XAUTUSDT", "BTCUSDT", "ETHUSDT"], root, cfg_path)
            with open(cfg_path, "r", encoding="utf-8") as f:
                text = f.read()
            # File must still be valid YAML with the inline pairs list.
            parsed = _yaml.safe_load(text)
            self.assertEqual(
                parsed["data"]["pairs"], ["XAUTUSDT", "BTCUSDT", "ETHUSDT"]
            )
            # No orphaned block-sequence items left behind.
            self.assertNotIn("\n  - XAUTUSDT", text)
            self.assertTrue(parsed["architecture"]["indicator_registry_enabled"])

    def test_pair_config_helpers(self):
        with tempfile.TemporaryDirectory() as root:
            cfg_path, wl_path = self._write_files(root)
            sym = add_whitelist_asset("ETH", enabled=True, primary_timeframe="1h", project_root=root, path=wl_path)
            self.assertEqual(sym, "ETHUSDT")
            set_pair_timeframes("ETH", "4h", "1d", project_root=root, path=wl_path)
            wl = load_whitelist(root, wl_path)
            eth = next(a for a in wl["assets"] if a["symbol"] == "ETH")
            self.assertEqual(eth["primary_timeframe"], "4h")
            self.assertEqual(eth["confirm_timeframe"], "1d")


class TestNormalizeHelpers(unittest.TestCase):
    def test_normalize_symbol(self):
        self.assertEqual(_normalize_symbol("btc"), "BTCUSDT")
        self.assertEqual(_normalize_symbol("BTCUSDT"), "BTCUSDT")

    def test_normalize_tf(self):
        self.assertEqual(_normalize_tf("4H"), "4h")


if __name__ == "__main__":
    unittest.main()
