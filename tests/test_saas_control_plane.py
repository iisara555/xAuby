import json
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

    def test_browser_shell_contains_owner_and_manual_controls(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="start"', response.text)
        self.assertIn('id="trade-form"', response.text)
        self.assertIn('id="admin-tab"', response.text)

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

    def test_manual_order_requires_pin_and_is_idempotent(self):
        pin = self.client.post(
            "/api/v1/trade-pin", headers=self.headers, json={"pin": "12345678"}
        )
        self.assertEqual(pin.status_code, 200)
        preview = self.client.post(
            "/api/v1/orders/preview", headers=self.headers,
            json={"symbol": "XAUUSDT", "intent": "OPEN_LONG"},
        )
        self.assertEqual(preview.status_code, 200)
        challenge_id = preview.json()["challenge_id"]
        confirmed = self.client.post(
            f"/api/v1/orders/{challenge_id}/confirm", headers=self.headers,
            json={"trade_pin": "12345678", "idempotency_key": "order-12345678"},
        )
        self.assertEqual(confirmed.status_code, 200)
        repeated = self.client.post(
            f"/api/v1/orders/{challenge_id}/confirm", headers=self.headers,
            json={"trade_pin": "12345678", "idempotency_key": "order-12345678"},
        )
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(
            confirmed.json()["command"]["request_id"], repeated.json()["command"]["request_id"]
        )

    def test_expired_challenge_does_not_enqueue(self):
        self.client.post("/api/v1/trade-pin", headers=self.headers, json={"pin": "12345678"})
        user = self.store.session(self.client.cookies.get("xauby_saas_session"))
        tenant = self.store.tenant_for_user(user["id"])
        challenge = self.store.create_challenge(
            tenant["id"], user["id"], {"symbol": "XAUUSDT", "intent": "OPEN_LONG"},
            ttl_seconds=-1,
        )
        response = self.client.post(
            f"/api/v1/orders/{challenge['id']}/confirm", headers=self.headers,
            json={"trade_pin": "12345678", "idempotency_key": "order-expired-1"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.supervisor.queue_path(tenant["slug"]).exists())

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

    def test_live_approval_enables_all_hosted_gates(self):
        me = self.client.get("/api/v1/me").json()
        tenant = me["tenant"]
        self.supervisor.provision(tenant["slug"])
        self.store.set_exchange_connection(
            tenant["id"], "okx", "1234", status="tested", capabilities={"swap": True}
        )
        self.store.request_live(tenant["id"], me["id"])
        response = self.client.post(
            f"/api/v1/admin/tenants/{tenant['id']}/approve-live", headers=self.headers
        )
        self.assertEqual(response.status_code, 200)
        config_dir = self.supervisor.config_dir(tenant["slug"])
        config = yaml.safe_load((config_dir / "bot_config.yaml").read_text(encoding="utf-8"))
        whitelist = json.loads((config_dir / "coin_whitelist.json").read_text(encoding="utf-8"))
        secrets = (config_dir / "secrets.env").read_text(encoding="utf-8")
        self.assertFalse(config["simulate_only"])
        self.assertTrue(all(asset["mode"] == "live" for asset in whitelist["assets"]))
        self.assertIn("LIVE_TRADING=true", secrets)
        self.assertIn("SIMULATE_ONLY=false", secrets)

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
