import json
import base64
import tempfile
import unittest
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from xauby.saas.app import create_app
from xauby.saas.settings import SaaSSettings
from xauby.saas.store import ControlPlaneStore
from xauby.saas.supervisor import TenantSupervisor


class SaaSControlPlaneTests(unittest.TestCase):
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
        self.csrf = response.json()["csrf_token"]
        self.headers = {"X-CSRF-Token": self.csrf}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_owner_has_admin_and_personal_tenant(self):
        response = self.client.get("/api/v1/me")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["role"], "platform_admin")
        self.assertEqual(payload["tenant"]["slug"], "owner-itsara")

    def test_profile_appearance_updates_name_and_private_avatar(self):
        png = base64.b64encode(
            b"\x89PNG\r\n\x1a\n" + b"profile-image"
        ).decode("ascii")
        updated = self.client.patch(
            "/api/v1/profile/appearance",
            headers=self.headers,
            json={
                "display_name": "Itsara Pilot",
                "avatar_data_url": f"data:image/png;base64,{png}",
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        me = self.client.get("/api/v1/me").json()
        self.assertEqual(me["display_name"], "Itsara Pilot")
        self.assertTrue(me["avatar_url"].startswith("/api/v1/profile/avatar?v="))
        avatar = self.client.get(me["avatar_url"])
        self.assertEqual(avatar.status_code, 200)
        self.assertEqual(avatar.headers["content-type"], "image/png")

    def test_catalog_exposes_backtest_evidence_without_inventing_missing_scores(self):
        presets = {
            item["id"]: item for item in self.client.get("/api/v1/catalog").json()["presets"]
        }
        xau = presets["okx-xau-actionzone-v1"]["backtest"]
        self.assertEqual(xau["score_label"], "PF 2.06")
        self.assertEqual(xau["duration"], "5.9 years")
        self.assertEqual(presets["binance-btc-supertrend-v1"]["backtest"]["status"], "insufficient")

    def test_runtime_snapshot_is_tenant_scoped_and_fail_closed(self):
        response = self.client.get("/api/v1/runtime/snapshot")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["source"], "tenant_engine")
        self.assertTrue(payload["stale"])
        self.assertNotIn("path", payload)

        runtime = self.supervisor.runtime_dir("owner-itsara") / "logs"
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "xauby_bot_state.json").write_text(
            json.dumps({"focus_symbol": "XAUUSDT", "position_side": "FLAT"}),
            encoding="utf-8",
        )
        fresh = self.client.get("/api/v1/runtime/snapshot")
        self.assertEqual(fresh.status_code, 200)
        self.assertTrue(fresh.json()["ok"])
        self.assertEqual(fresh.json()["state"]["focus_symbol"], "XAUUSDT")

    def test_runtime_queries_validate_public_inputs(self):
        bad_symbol = self.client.get(
            "/api/v1/runtime/candles", params={"symbol": "../secret", "timeframe": "4h"}
        )
        self.assertEqual(bad_symbol.status_code, 422)
        bad_timeframe = self.client.get(
            "/api/v1/runtime/candles", params={"symbol": "XAUUSDT", "timeframe": "99h"}
        )
        self.assertEqual(bad_timeframe.status_code, 422)

    def test_backend_root_exposes_service_metadata_only(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["service"], "xauby-control")

    def test_write_endpoint_requires_csrf(self):
        response = self.client.post("/api/v1/bot/start")
        self.assertEqual(response.status_code, 403)
        response = self.client.post("/api/v1/bot/start", headers=self.headers)
        self.assertEqual(response.status_code, 200)

    def test_curated_config_is_bounded_and_revisioned(self):
        updated = self.client.patch(
            "/api/v1/bot/config", headers=self.headers,
            json={"risk_pct": 0.01, "max_position_per_trade_pct": 10,
                  "max_daily_loss_pct": 3},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["revision"], 1)
        denied = self.client.patch(
            "/api/v1/bot/config", headers=self.headers, json={"risk_pct": 0.5}
        )
        self.assertEqual(denied.status_code, 422)

    def test_manual_trading_is_not_part_of_the_pilot(self):
        preview = self.client.post(
            "/api/v1/orders/preview", headers=self.headers,
            json={"symbol": "XAUUSDT", "intent": "OPEN_LONG"},
        )
        self.assertEqual(preview.status_code, 404)
        self.assertFalse(self.client.get("/api/v1/catalog").json()["features"]["manual_trading"])

    def test_fourth_active_tenant_is_queued(self):
        self.store.update_tenant(
            self.store.tenant_for_user(self.store.session(self.client.cookies.get("xauby_saas_session"))["id"])["id"],
            status="running",
        )
        for index in range(2):
            user, _ = self.store.upsert_google_user(f"u{index}@example.com", f"sub-{index}")
            tenant, _ = self.store.ensure_tenant(user["id"], f"user-{index}")
            self.store.update_tenant(tenant["id"], status="running")
        user, _ = self.store.upsert_google_user("queued@example.com", "sub-queued")
        tenant, _ = self.store.ensure_tenant(user["id"], "queued-user")
        self.assertEqual(self.store.active_count(), 3)
        self.assertEqual(tenant["status"], "queued")

    def test_live_approval_requires_tested_connection(self):
        me = self.client.get("/api/v1/me").json()
        tenant_id = me["tenant"]["id"]
        self.store.request_live(tenant_id, me["id"])
        response = self.client.post(
            f"/api/v1/admin/tenants/{tenant_id}/approve-live", headers=self.headers
        )
        self.assertEqual(response.status_code, 409)

    def test_self_service_live_materializes_credentials_only_while_running(self):
        me = self.client.get("/api/v1/me").json()
        tenant = me["tenant"]
        connected = self.client.post(
            "/api/v1/exchange/connect", headers=self.headers,
            json={"target_id": "okx-swap", "api_key": "test-key-1234",
                  "api_secret": "test-secret-value", "passphrase": "test-passphrase",
                  "withdraw_disabled_attested": True},
        )
        self.assertEqual(connected.status_code, 200, connected.text)
        with self.store.connection() as conn:
            envelope = conn.execute(
                "SELECT credential_blob FROM exchange_connections WHERE tenant_id=?",
                (tenant["id"],),
            ).fetchone()[0]
        self.assertNotIn("test-secret-value", envelope)
        profile = self.client.put(
            "/api/v1/profile", headers=self.headers,
            json={"preset_ids": ["okx-xau-actionzone-v1"],
                  "active_preset_id": "okx-xau-actionzone-v1", "risk": {}},
        )
        self.assertEqual(profile.status_code, 200, profile.text)
        connection = self.store.exchange_connection(tenant["id"])
        self.store.set_exchange_connection(
            tenant["id"], "okx", connection["key_last4"], target_id="okx-swap",
            status="tested", capabilities={"swap": True},
        )
        self.client.post("/api/v1/trade-pin", headers=self.headers, json={"pin": "12345678"})
        response = self.client.post(
            "/api/v1/live/activate", headers=self.headers,
            json={"trade_pin": "12345678", "risk_acknowledged": True},
        )
        self.assertEqual(response.status_code, 200)
        config_dir = self.supervisor.config_dir(tenant["slug"])
        config = yaml.safe_load((config_dir / "bot_config.yaml").read_text(encoding="utf-8"))
        whitelist = json.loads((config_dir / "coin_whitelist.json").read_text(encoding="utf-8"))
        self.assertFalse(config["simulate_only"])
        self.assertTrue(all(asset["mode"] == "live" for asset in whitelist["assets"]))
        self.assertFalse((config_dir / "secrets.env").exists())
        ephemeral = self.supervisor.credential_path(tenant["slug"])
        self.assertIn("test-secret-value", ephemeral.read_text(encoding="utf-8"))
        stopped = self.client.post("/api/v1/live/deactivate", headers=self.headers)
        self.assertEqual(stopped.status_code, 200)
        self.assertFalse(ephemeral.exists())

    def test_replacing_trade_pin_requires_current_pin(self):
        first = self.client.post(
            "/api/v1/trade-pin", headers=self.headers, json={"pin": "12345678"}
        )
        self.assertEqual(first.status_code, 200)
        denied = self.client.post(
            "/api/v1/trade-pin", headers=self.headers, json={"pin": "87654321"}
        )
        self.assertEqual(denied.status_code, 403)
        replaced = self.client.post(
            "/api/v1/trade-pin", headers=self.headers,
            json={"pin": "87654321", "current_pin": "12345678"},
        )
        self.assertEqual(replaced.status_code, 200)


if __name__ == "__main__":
    unittest.main()
