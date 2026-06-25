import json
import os
import tempfile
import unittest

from xauby.runtime.config_error import ConfigError
from xauby.runtime.whitelist_validator import validate_whitelist, validate_whitelist_schema


class TestWhitelistValidator(unittest.TestCase):
    def test_enabled_assets_required(self):
        wl = {"version": 1, "quote_asset": "USDT", "assets": []}
        with self.assertRaises(ConfigError):
            validate_whitelist_schema(wl, strict=False)

    def test_strict_requires_strategy(self):
        wl = {
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
        }
        with self.assertRaises(ConfigError):
            validate_whitelist_schema(wl, strict=True)

    def test_strict_valid_whitelist(self):
        wl = {
            "version": 1,
            "quote_asset": "USDT",
            "assets": [
                {
                    "symbol": "XAUT",
                    "enabled": True,
                    "primary_timeframe": "4h",
                    "confirm_timeframe": "1d",
                    "strategy": "cdc_action_zone",
                }
            ],
        }
        enabled = validate_whitelist(wl, strict=True, require_supported_market=False)
        self.assertEqual(enabled, ["XAUTUSDT"])

    def test_exchange_support_strict(self):
        wl = {
            "version": 1,
            "quote_asset": "USDT",
            "assets": [
                {
                    "symbol": "XAUT",
                    "enabled": True,
                    "primary_timeframe": "4h",
                    "confirm_timeframe": "1d",
                    "strategy": "cdc_action_zone",
                }
            ],
        }
        with self.assertRaises(ConfigError):
            validate_whitelist(
                wl,
                strict=True,
                supported={"BTCUSDT"},
                require_supported_market=True,
            )

    def test_strict_rejects_short_strategy_without_sim_or_live_gate(self):
        wl = {
            "version": 1,
            "quote_asset": "USDT",
            "assets": [
                {
                    "symbol": "BTC",
                    "enabled": True,
                    "primary_timeframe": "1h",
                    "strategy": "supertrend_short",
                }
            ],
        }
        with self.assertRaisesRegex(ConfigError, "short strategy"):
            validate_whitelist_schema(wl, strict=True)

    def test_strict_allows_short_strategy_in_explicit_sim_mode(self):
        wl = {
            "version": 1,
            "quote_asset": "USDT",
            "assets": [{
                "symbol": "BTC", "enabled": True, "primary_timeframe": "1h",
                "strategy": "supertrend_short", "mode": "sim",
                "allowed_sides": ["short"], "short_live_enabled": False,
            }],
        }
        self.assertEqual(validate_whitelist_schema(wl, strict=True), ["BTCUSDT"])

    def test_missing_whitelist_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "missing.json")
            with self.assertRaises(ConfigError):
                validate_whitelist({}, whitelist_path=path, strict=True)
