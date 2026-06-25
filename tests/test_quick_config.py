import os
import tempfile
import unittest
from unittest import mock

import yaml

import launcher
from xauby.launcher import config_io, quick_config

SAMPLE_CONFIG = """\
# === TOP SENTINEL COMMENT (must survive edits) ===
simulate_only: true
trading:
  # risk per trade (inline sentinel)
  risk_pct: 0.03
  max_open_positions: 2
  timeframe: 4h
risk:
  max_risk_per_trade_pct: 3.0
  max_daily_loss_pct: 10.0
  drawdown_guard:
    enabled: true
    max_drawdown_pct: 20.0
portfolio:
  initial_balance: 1000.0
  position_sizing:
    risk_pct: 0.03
  symbols:
    BTCUSDT:
      allocation_pct: 50.0
      position_sizing:
        risk_pct: 0.03
strategy:
  active: cdc_action_zone
  config:
    cdc_action_zone: {}
  symbols: {}
architecture:
  regime_router_enabled: true
regime_router:
  confidence_threshold: 0.7
  mapping:
    SIDEWAYS_CHOP: bbrsi_mean_reversion
weekly_review:
  enabled: true
  hour_utc: 17
notifications:
  regime_score_threshold: 10
"""


class _TempConfig:
    """Context manager: run inside a temp dir holding a bot_config.yaml."""

    def __enter__(self):
        self._prev = os.getcwd()
        self._dir = tempfile.mkdtemp(prefix="qc_")
        with open(os.path.join(self._dir, "bot_config.yaml"), "w", encoding="utf-8") as f:
            f.write(SAMPLE_CONFIG)
        os.chdir(self._dir)
        return self

    def text(self) -> str:
        with open(os.path.join(self._dir, "bot_config.yaml"), "r", encoding="utf-8") as f:
            return f.read()

    def loaded(self) -> dict:
        return yaml.safe_load(self.text())

    def __exit__(self, *exc):
        os.chdir(self._prev)


class TestQuickConfigWrite(unittest.TestCase):
    def tearDown(self):
        quick_config._QUICK_CONFIG_TARGET_SYMBOL = ""

    def test_set_yaml_path_preserves_comments(self):
        with _TempConfig() as cfg:
            ok = launcher._set_yaml_path("trading.max_open_positions", 5)
            self.assertTrue(ok)
            text = cfg.text()
            self.assertIn("# === TOP SENTINEL COMMENT", text)
            self.assertIn("# risk per trade (inline sentinel)", text)
            self.assertEqual(cfg.loaded()["trading"]["max_open_positions"], 5)

    def test_set_yaml_path_existing_flag_toggle(self):
        with _TempConfig() as cfg:
            launcher._set_yaml_path("architecture.regime_router_enabled", False)
            self.assertIs(cfg.loaded()["architecture"]["regime_router_enabled"], False)
            self.assertIn("# === TOP SENTINEL COMMENT", cfg.text())

    def test_set_yaml_path_creates_missing_leaf(self):
        with _TempConfig() as cfg:
            launcher._set_yaml_path("architecture.exchange_plugin_registry_enabled", True)
            self.assertIs(cfg.loaded()["architecture"]["exchange_plugin_registry_enabled"], True)

    def test_set_yaml_path_creates_deep_branch(self):
        with _TempConfig() as cfg:
            launcher._set_yaml_path("portfolio.symbols.ETHUSDT.allocation_pct", 25.0)
            self.assertEqual(cfg.loaded()["portfolio"]["symbols"]["ETHUSDT"]["allocation_pct"], 25.0)
            # Existing sibling untouched + comments intact.
            self.assertEqual(cfg.loaded()["portfolio"]["symbols"]["BTCUSDT"]["allocation_pct"], 50.0)
            self.assertIn("# === TOP SENTINEL COMMENT", cfg.text())

    def test_drawdown_guard_nested_edit(self):
        with _TempConfig() as cfg:
            launcher._set_yaml_path("risk.drawdown_guard.max_drawdown_pct", 12.5)
            self.assertEqual(cfg.loaded()["risk"]["drawdown_guard"]["max_drawdown_pct"], 12.5)

    def test_update_yaml_config_risk_alignment(self):
        with _TempConfig() as cfg:
            launcher.update_yaml_config(max_risk=0.05, symbol="BTCUSDT")
            doc = cfg.loaded()
            self.assertEqual(doc["trading"]["risk_pct"], 0.05)
            # Fraction → percent for the legacy field.
            self.assertEqual(doc["risk"]["max_risk_per_trade_pct"], 5.0)
            self.assertEqual(doc["portfolio"]["position_sizing"]["risk_pct"], 0.05)
            self.assertEqual(doc["portfolio"]["symbols"]["BTCUSDT"]["position_sizing"]["risk_pct"], 0.05)
            self.assertIn("# === TOP SENTINEL COMMENT", cfg.text())
            # The written config must still pass the startup risk guard (the legacy
            # percent field at 5.0 must not be misread as a fraction and rejected).
            from xauby.runtime.trading_config import validate_risk_config
            validate_risk_config(doc)

    def test_set_regime_mapping_and_strategy_param(self):
        with _TempConfig() as cfg:
            launcher._set_yaml_path("regime_router.mapping.SIDEWAYS_CHOP", "donchian_trend")
            launcher._set_yaml_path("regime_router.mapping.PANIC_SELL", None)
            launcher._set_yaml_path("strategy.symbols.BTCUSDT.trailing_atr_mult", 2.5)
            doc = cfg.loaded()
            self.assertEqual(doc["regime_router"]["mapping"]["SIDEWAYS_CHOP"], "donchian_trend")
            self.assertIsNone(doc["regime_router"]["mapping"]["PANIC_SELL"])
            self.assertEqual(doc["strategy"]["symbols"]["BTCUSDT"]["trailing_atr_mult"], 2.5)
            self.assertIn("# === TOP SENTINEL COMMENT", cfg.text())

    def test_set_notification_schedule(self):
        with _TempConfig() as cfg:
            launcher._set_yaml_path("weekly_review.hour_utc", 9)
            launcher._set_yaml_path("daily_digest.enabled", True)
            launcher._set_yaml_path("notifications.regime_score_threshold", 25)
            doc = cfg.loaded()
            self.assertEqual(doc["weekly_review"]["hour_utc"], 9)
            self.assertIs(doc["daily_digest"]["enabled"], True)
            self.assertEqual(doc["notifications"]["regime_score_threshold"], 25)

    def test_quick_config_values_survive_empty_pair_configuration(self):
        with _TempConfig():
            with mock.patch.object(config_io, "_active_symbols_from_config", return_value=[]), \
                 mock.patch.object(config_io, "default_runtime_symbol", return_value="BTCUSDT"):
                values = launcher._quick_config_load_values()
            self.assertEqual(values["symbols"], ["BTCUSDT"])
            self.assertEqual(values["focus_symbol"], "BTCUSDT")

    def test_quick_config_target_symbol_persists_without_dashboard_side_effect(self):
        with _TempConfig():
            with mock.patch.object(config_io, "_active_symbols_from_config", return_value=["BTCUSDT", "ETHUSDT"]):
                quick_config._QUICK_CONFIG_TARGET_SYMBOL = "ETHUSDT"
                values = launcher._quick_config_load_values()
            self.assertEqual(values["focus_symbol"], "ETHUSDT")

    def test_invalid_best_params_are_not_offered(self):
        with mock.patch("xauby.backtest.best_params.load_best_params", return_value={"net_profit_pct": 1.0}):
            self.assertIsNone(launcher._quick_config_load_opt_params("BTCUSDT"))

    def test_best_params_are_loaded_and_applied_per_symbol(self):
        params = {
            "net_profit_pct": 4.2,
            "sl_atr_mult": 2.5,
            "rsi_min": 40.0,
            "rsi_max": 70.0,
            "vol_min_ratio": 1.1,
        }
        with mock.patch("xauby.backtest.best_params.load_best_params", return_value=params) as load:
            loaded = launcher._quick_config_load_opt_params("ethusdt")
        load.assert_called_once_with("ETHUSDT")
        self.assertEqual(loaded["_symbol"], "ETHUSDT")

        with mock.patch.object(quick_config, "_set_pair_strategy_values", return_value=True) as update, \
             mock.patch.object(quick_config, "restart_bot_service"), \
             mock.patch.object(quick_config.time, "sleep"), \
             mock.patch("builtins.input", side_effect=["y", "n"]):
            launcher.quick_config_submenu_backtest(loaded)
        self.assertEqual(update.call_args.args[1], "ETHUSDT")

    def test_strict_whitelist_strategy_values_use_whitelist_source(self):
        with _TempConfig() as temp:
            path = os.path.join(temp._dir, "coin_whitelist.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"quote_asset":"USDT","assets":[{"symbol":"BTC","enabled":true,"strategy":"old"}]}')
            cfg = temp.loaded()
            cfg.setdefault("architecture", {})["whitelist_strict"] = True
            ok = launcher._set_pair_strategy_values(
                cfg, "BTCUSDT", strategy="cdc_action_zone", params={"rsi_min": 42.0}
            )
            self.assertTrue(ok)
            with open(path, "r", encoding="utf-8") as f:
                saved = __import__("json").load(f)
            self.assertEqual(saved["assets"][0]["strategy"], "cdc_action_zone")
            self.assertEqual(saved["assets"][0]["strategy_params"]["rsi_min"], 42.0)
            self.assertNotIn("BTCUSDT", (temp.loaded()["strategy"].get("symbols") or {}))


class TestExchangeHelpers(unittest.TestCase):
    def test_credential_targets_per_provider(self):
        kraken = launcher._exchange_credential_targets(
            {"exchange": {"provider": "ccxt", "ccxt_id": "kraken"}}
        )
        self.assertEqual(kraken[0], "KRAKEN_API_KEY")
        self.assertEqual(kraken[1], "KRAKEN_API_SECRET")
        binance = launcher._exchange_credential_targets({"exchange": {"provider": "binance"}})
        self.assertEqual(binance[0], "BINANCE_API_KEY")

    def test_connection_check_ok(self):
        cfg = {"exchange": {"api_key_env": "QC_K", "api_secret_env": "QC_S"}}

        class FakeClient:
            def get_balances(self):
                return {"USDT": {}, "BTC": {}}

            def close(self):
                pass

        with mock.patch.dict(os.environ, {"QC_K": "k", "QC_S": "s"}):
            ok, msg = launcher.exchange_connection_check(cfg, client_factory=lambda *a: FakeClient())
        self.assertTrue(ok)
        self.assertIn("2 asset", msg)

    def test_connection_check_missing_creds(self):
        cfg = {"exchange": {"api_key_env": "QC_UNSET_K", "api_secret_env": "QC_UNSET_S"}}
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("QC_UNSET_K", None)
            os.environ.pop("QC_UNSET_S", None)
            ok, msg = launcher.exchange_connection_check(cfg)
        self.assertFalse(ok)
        self.assertIn("missing", msg)

    def test_connection_check_error_is_reported(self):
        cfg = {"exchange": {"api_key_env": "QC_K", "api_secret_env": "QC_S"}}

        def boom(*a):
            raise RuntimeError("nope")

        with mock.patch.dict(os.environ, {"QC_K": "k", "QC_S": "s"}):
            ok, msg = launcher.exchange_connection_check(cfg, client_factory=boom)
        self.assertFalse(ok)
        self.assertIn("nope", msg)


if __name__ == "__main__":
    unittest.main()
