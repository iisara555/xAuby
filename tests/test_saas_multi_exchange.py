import json
import tempfile
import unittest
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from xauby.runtime.exchange_config import credential_env_names
from xauby.saas.app import create_app
from xauby.saas.catalog import (
    CATALOG,
    EXCHANGE_PROFILES,
    MAX_CONFIGURED_PAIRS,
    TARGETS,
    validate_profile,
)
from xauby.saas.settings import SaaSSettings
from xauby.saas.store import ControlPlaneStore
from xauby.saas.supervisor import TenantSupervisor


class CatalogMultiExchangeTests(unittest.TestCase):
    def test_targets_limits_and_exchange_profiles_cover_each_other(self):
        target_ids = [target["id"] for target in TARGETS]
        self.assertEqual(
            target_ids,
            ["okx-swap", "binance-global-futures", "binance-global-spot", "binance-th-spot"],
        )
        self.assertEqual(set(EXCHANGE_PROFILES), set(target_ids))
        self.assertEqual(CATALOG["limits"], {"configured_pairs": 3, "active_pairs": 3})
        for target_id in target_ids:
            presets = [p for p in CATALOG["presets"] if p["target_id"] == target_id]
            self.assertGreaterEqual(len(presets), 2, target_id)

        targets = {target["id"]: target for target in TARGETS}
        self.assertTrue(targets["okx-swap"]["manual_short_live_certified"])
        self.assertTrue(
            targets["binance-global-futures"]["manual_short_live_certified"]
        )
        self.assertFalse(targets["binance-global-spot"]["manual_short_live_certified"])
        self.assertFalse(targets["binance-th-spot"]["manual_short_live_certified"])

    def test_certified_okx_presets_publish_pair_sizing_metadata(self):
        presets = {item["id"]: item for item in CATALOG["presets"]}
        self.assertEqual(presets["okx-xau-actionzone-v1"]["allocation_pct"], 65.0)
        self.assertEqual(
            presets["okx-xau-actionzone-v1"]["max_position_per_trade_pct"], 25.0
        )
        self.assertEqual(presets["okx-btc-supertrend-v1"]["allocation_pct"], 30.0)
        self.assertEqual(
            CATALOG["risk"]["max_daily_loss_pct"],
            {"default": 6.0, "min": 1.0, "max": 6.0},
        )

    def test_fee_units_stay_consistent_per_target(self):
        # exchange.fee_pct is a fraction; whitelist sim_fee_pct is a percent.
        for target_id, profile in EXCHANGE_PROFILES.items():
            self.assertAlmostEqual(
                profile["whitelist"]["sim_fee_pct"] / 100.0,
                profile["exchange"]["fee_pct"],
                msg=target_id,
            )

    def test_binance_th_uses_the_native_client_not_ccxt(self):
        exchange = EXCHANGE_PROFILES["binance-th-spot"]["exchange"]
        self.assertEqual(exchange["provider"], "binance")
        self.assertNotIn("ccxt_id", exchange)
        self.assertEqual(exchange["base_url"], "https://api.binance.th")
        self.assertEqual(exchange["quote_asset"], "THB")

    def test_validate_profile_multi_pair_rules(self):
        result = validate_profile(
            ["okx-xau-actionzone-v1", "okx-btc-supertrend-v1"],
            "okx-xau-actionzone-v1",
            {},
        )
        self.assertEqual(result["risk"]["max_open_positions"], 2)
        self.assertEqual(result["target_id"], "okx-swap")
        self.assertFalse(result["risk"]["stop_loss_required"])

        with self.assertRaisesRegex(ValueError, "same exchange"):
            validate_profile(
                ["okx-xau-actionzone-v1", "binance-btc-supertrend-v1"],
                "okx-xau-actionzone-v1",
                {},
            )
        with self.assertRaisesRegex(ValueError, "between 1 and"):
            validate_profile([], "okx-xau-actionzone-v1", {})
        with self.assertRaisesRegex(ValueError, "one of the selected"):
            validate_profile(
                ["okx-xau-actionzone-v1"], "okx-btc-supertrend-v1", {}
            )
        with self.assertRaisesRegex(ValueError, "unknown certified preset"):
            validate_profile(["not-a-preset"], "not-a-preset", {})
        self.assertLessEqual(MAX_CONFIGURED_PAIRS, 3)

    def test_max_open_positions_is_derived_not_user_controlled(self):
        result = validate_profile(
            ["okx-xau-actionzone-v1"],
            "okx-xau-actionzone-v1",
            {"max_open_positions": 3},
        )
        self.assertEqual(result["risk"]["max_open_positions"], 1)


class MultiExchangeControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        project = Path(__file__).resolve().parents[1]
        self.settings = SaaSSettings(
            project_root=project,
            data_root=root / "data",
            tenant_config_root=root / "config",
            tenant_runtime_root=root / "runtime",
            database_path=root / "control.db",
            public_base_url="http://testserver",
            session_secret="test-session-secret-that-is-long-enough",
            max_active_engines=3,
            systemctl_bin="mock",
            cookie_secure=False,
            dev_login_enabled=True,
            live_activation_enabled=True,
        )
        self.store = ControlPlaneStore(self.settings.database_path)
        self.store.migrate()
        self.store.bootstrap_owner("owner@example.com", "owner-itsara")
        self.supervisor = TenantSupervisor(self.settings)
        self.app = create_app(self.settings, store=self.store, supervisor=self.supervisor)
        self.client = TestClient(self.app)
        response = self.client.post("/auth/dev-login", params={"email": "owner@example.com"})
        self.assertEqual(response.status_code, 200)
        self.headers = {"X-CSRF-Token": response.json()["csrf_token"]}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def _tenant(self):
        return self.client.get("/api/v1/me").json()["tenant"]

    def _save_profile(self, preset_ids, active=None):
        return self.client.put(
            "/api/v1/profile", headers=self.headers,
            json={"preset_ids": preset_ids, "active_preset_id": active or preset_ids[0],
                  "risk": {}},
        )

    def _config(self, slug):
        config_dir = self.supervisor.config_dir(slug)
        cfg = yaml.safe_load((config_dir / "bot_config.yaml").read_text(encoding="utf-8"))
        whitelist = json.loads((config_dir / "coin_whitelist.json").read_text(encoding="utf-8"))
        return cfg, whitelist

    def test_two_pair_profile_writes_multi_pair_whitelist(self):
        tenant = self._tenant()
        saved = self._save_profile(
            ["okx-xau-actionzone-v1", "okx-btc-supertrend-v1"], "okx-xau-actionzone-v1"
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        cfg, whitelist = self._config(tenant["slug"])
        self.assertEqual(cfg["exchange"]["ccxt_id"], "okx")
        self.assertEqual(cfg["trading"]["max_open_positions"], 2)
        self.assertEqual(cfg["trading"]["trading_pair"], "XAUUSDT")
        assets = {asset["symbol"]: asset for asset in whitelist["assets"]}
        self.assertEqual(set(assets), {"XAU", "BTC"})
        self.assertTrue(all(asset["enabled"] for asset in assets.values()))
        self.assertTrue(all(asset["mode"] == "sim" for asset in assets.values()))
        self.assertTrue(assets["XAU"]["live_certified"])
        self.assertTrue(assets["BTC"]["live_certified"])
        self.assertEqual(assets["XAU"]["sim_fee_pct"], 0.05)
        self.assertEqual(assets["XAU"]["allowed_sides"], ["long", "short"])
        self.assertEqual(assets["BTC"]["allowed_sides"], ["long", "short"])
        self.assertEqual(
            assets["BTC"]["strategy_params"]["supertrend_mult"], 4.0
        )
        self.assertEqual(cfg["portfolio"]["symbols"]["XAUUSDT"]["allocation_pct"], 65.0)
        self.assertEqual(cfg["portfolio"]["symbols"]["BTCUSDT"]["allocation_pct"], 30.0)
        self.assertEqual(
            cfg["portfolio"]["symbols"]["BTCUSDT"]["position_sizing"]["risk_pct"],
            0.02,
        )

    def test_exchange_switch_rewrites_exchange_block_and_flags_reconnect(self):
        tenant = self._tenant()
        first = self._save_profile(["okx-xau-actionzone-v1"])
        self.assertEqual(first.status_code, 200, first.text)
        switched = self._save_profile(
            ["binance-th-btc-supertrend-v1", "binance-th-eth-supertrend-v1"]
        )
        self.assertEqual(switched.status_code, 200, switched.text)
        payload = switched.json()
        self.assertTrue(payload["exchange_switched"])
        self.assertTrue(payload["reconnect_required"])
        cfg, whitelist = self._config(tenant["slug"])
        self.assertEqual(cfg["exchange"]["provider"], "binance")
        self.assertNotIn("ccxt_id", cfg["exchange"])
        self.assertEqual(cfg["exchange"]["base_url"], "https://api.binance.th")
        self.assertNotIn("params", cfg["exchange"])
        self.assertEqual(cfg["derivatives"]["market_type"], "spot")
        self.assertEqual(whitelist["quote_asset"], "THB")
        self.assertEqual(
            {asset["sim_fee_pct"] for asset in whitelist["assets"]}, {0.25}
        )
        me = self.client.get("/api/v1/me").json()
        self.assertEqual(me["tenant"]["exchange_id"], "binance_th")
        self.assertEqual(me["tenant"]["market_type"], "spot")

    def test_connect_rejects_keys_for_a_different_exchange_than_the_profile(self):
        first = self._save_profile(["okx-xau-actionzone-v1"])
        self.assertEqual(first.status_code, 200, first.text)
        response = self.client.post(
            "/api/v1/exchange/connect", headers=self.headers,
            json={"target_id": "binance-global-futures", "api_key": "key-1234",
                  "api_secret": "secret-1234", "passphrase": "",
                  "withdraw_disabled_attested": True},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("save a trading profile", response.json()["detail"])

    def test_set_live_mode_enables_both_certified_okx_pairs(self):
        tenant = self._tenant()
        saved = self._save_profile(
            ["okx-xau-actionzone-v1", "okx-btc-supertrend-v1"], "okx-xau-actionzone-v1"
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.supervisor.set_live_mode(tenant["slug"], "okx-swap")
        cfg, whitelist = self._config(tenant["slug"])
        assets = {asset["symbol"]: asset for asset in whitelist["assets"]}
        self.assertEqual(assets["XAU"]["mode"], "live")
        self.assertEqual(assets["BTC"]["mode"], "live")
        self.assertEqual(cfg["trading"]["max_open_positions"], 2)
        self.assertEqual(cfg["execution"]["live_strategy_mode"], "cdc_pure")
        self.assertFalse(cfg["risk"]["stop_loss_required"])
        self.assertFalse(cfg["simulate_only"])
        self.assertEqual(assets["XAU"]["manual_allowed_sides"], ["long", "short"])
        self.assertTrue(assets["XAU"]["manual_short_live_enabled"])
        self.assertTrue(assets["BTC"]["manual_short_live_enabled"])
        self.assertTrue(assets["BTC"]["short_live_enabled"])

    def test_certified_btc_pair_can_run_live_without_xau(self):
        tenant = self._tenant()
        saved = self._save_profile(["okx-btc-supertrend-v1"])
        self.assertEqual(saved.status_code, 200, saved.text)
        self.supervisor.set_live_mode(tenant["slug"], "okx-swap")
        cfg, whitelist = self._config(tenant["slug"])
        self.assertTrue(cfg["risk"]["stop_loss_required"])
        self.assertEqual(whitelist["assets"][0]["mode"], "live")
        self.assertEqual(whitelist["assets"][0]["allowed_sides"], ["long", "short"])

    def test_sim_only_exchange_target_cannot_go_live(self):
        tenant = self._tenant()
        saved = self._save_profile(["binance-spot-btc-supertrend-v1"])
        self.assertEqual(saved.status_code, 200, saved.text)
        with self.assertRaisesRegex(ValueError, "not live certified"):
            self.supervisor.set_live_mode(tenant["slug"], "binance-global-spot")

    def test_live_request_accepts_the_certified_btc_preset(self):
        tenant = self._tenant()
        saved = self._save_profile(["okx-btc-supertrend-v1"])
        self.assertEqual(saved.status_code, 200, saved.text)
        self.store.set_exchange_connection(
            tenant["id"], "okx", "1234", target_id="okx-swap", status="tested",
        )
        response = self.client.post("/api/v1/live/request", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "requested")

    def test_credential_env_names_align_with_engine_resolver(self):
        tenant = self._tenant()
        for preset_ids, target_id, expected_key in [
            (["okx-xau-actionzone-v1"], "okx-swap", "OKX_API_KEY"),
            (["binance-th-btc-supertrend-v1"], "binance-th-spot", "BINANCE_TH_API_KEY"),
            (["binance-btc-supertrend-v1"], "binance-global-futures", "BINANCEUSDM_API_KEY"),
        ]:
            saved = self._save_profile(preset_ids)
            self.assertEqual(saved.status_code, 200, saved.text)
            cfg, _ = self._config(tenant["slug"])
            engine_key, engine_secret, _base = credential_env_names(cfg)
            self.assertEqual(engine_key, expected_key)
            self.supervisor.set_credential_loader(
                lambda slug, tid=target_id: (tid, {
                    "api_key": "k-value", "api_secret": "s-value", "passphrase": "p-value",
                })
            )
            env_path = self.supervisor.materialize_credentials(tenant["slug"])
            body = env_path.read_text(encoding="utf-8")
            self.assertIn(f'{expected_key}="k-value"', body)
            self.assertIn(f'{engine_secret}="s-value"', body)
            if target_id == "okx-swap":
                self.assertIn('OKX_API_PASSPHRASE="p-value"', body)

    def test_telegram_env_names_align_with_engine_reader(self):
        """The three names the engine reads at xauby/engine/base.py:302-304.

        A rename on either side must fail the build rather than silently stop
        delivering alerts.
        """
        source = (Path(__file__).resolve().parents[1]
                  / "xauby" / "engine" / "base.py").read_text(encoding="utf-8")
        for name in ("TELEGRAM_ENABLED", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
            self.assertIn(f'os.environ.get("{name}"', source)

        tenant = self._tenant()
        self.assertEqual(self._save_profile(["okx-xau-actionzone-v1"]).status_code, 200)
        self.supervisor.set_telegram_loader(
            lambda slug: {"bot_token": "fixture-token", "chat_id": "-1009999"}
        )
        body = self.supervisor.materialize_credentials(tenant["slug"]).read_text(encoding="utf-8")
        self.assertIn('TELEGRAM_ENABLED="true"', body)
        self.assertIn('TELEGRAM_BOT_TOKEN="fixture-token"', body)
        self.assertIn('TELEGRAM_CHAT_ID="-1009999"', body)

    def test_materialize_credentials_writes_telegram_without_exchange_keys(self):
        """A sim-only tenant with no exchange keys still gets an env file."""
        tenant = self._tenant()
        self.assertEqual(self._save_profile(["okx-xau-actionzone-v1"]).status_code, 200)
        self.supervisor.set_credential_loader(lambda slug: None)
        self.supervisor.set_telegram_loader(
            lambda slug: {"bot_token": "fixture-token", "chat_id": "42"}
        )
        env_path = self.supervisor.materialize_credentials(tenant["slug"])
        self.assertIsNotNone(env_path)
        body = env_path.read_text(encoding="utf-8")
        self.assertIn('TELEGRAM_BOT_TOKEN="fixture-token"', body)
        self.assertIn("LIVE_TRADING=false", body)
        self.assertNotIn("OKX_API_KEY", body)

    def test_materialize_credentials_marks_telegram_disabled_when_absent(self):
        tenant = self._tenant()
        self.assertEqual(self._save_profile(["okx-xau-actionzone-v1"]).status_code, 200)
        self.supervisor.set_credential_loader(
            lambda slug: ("okx-swap", {"api_key": "k", "api_secret": "s"})
        )
        self.supervisor.set_telegram_loader(lambda slug: None)
        body = self.supervisor.materialize_credentials(tenant["slug"]).read_text(encoding="utf-8")
        self.assertIn('TELEGRAM_ENABLED="false"', body)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", body)

    def test_materialize_credentials_returns_none_without_either_source(self):
        tenant = self._tenant()
        self.assertEqual(self._save_profile(["okx-xau-actionzone-v1"]).status_code, 200)
        self.supervisor.set_credential_loader(lambda slug: None)
        self.supervisor.set_telegram_loader(lambda slug: None)
        self.assertIsNone(self.supervisor.materialize_credentials(tenant["slug"]))


if __name__ == "__main__":
    unittest.main()
