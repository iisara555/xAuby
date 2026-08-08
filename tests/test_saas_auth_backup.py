from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from scripts.saas_backup import create_backup, verify_backup
from xauby.saas.app import create_app
from xauby.saas.mailer import Mailer
from xauby.saas.security import totp_code
from xauby.saas.settings import SaaSSettings
from xauby.saas.store import ControlPlaneStore
from xauby.saas.supervisor import TenantSupervisor


class UnavailableMailer:
    def send(self, recipient: str, subject: str, text: str) -> None:
        raise RuntimeError("transactional email delivery is unavailable")


class SaaSAuthAndBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        project = Path(__file__).resolve().parents[1]
        self.settings = SaaSSettings(
            project_root=project, data_root=root / "data", tenant_config_root=root / "config",
            tenant_runtime_root=root / "runtime", database_path=root / "control.db",
            public_base_url="http://testserver", session_secret="test-session-secret-123456789-long",
            max_users=3, max_active_engines=2, systemctl_bin="mock", cookie_secure=False,
            dev_login_enabled=True,
        )
        self.store = ControlPlaneStore(self.settings.database_path)
        self.store.migrate()
        self.owner, _ = self.store.bootstrap_owner("iisara555@gmail.com", "owner-itsara")
        self.supervisor = TenantSupervisor(self.settings)
        self.mailer = Mailer(self.settings)
        self.app = create_app(
            self.settings, store=self.store, supervisor=self.supervisor, mailer=self.mailer
        )
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def _invite_and_accept(self, email: str = "pilot@example.com") -> tuple[TestClient, dict]:
        owner_login = self.client.post(
            "/auth/dev-login", params={"email": "iisara555@gmail.com"}
        )
        response = self.client.post(
            "/api/v1/admin/invites",
            headers={"X-CSRF-Token": owner_login.json()["csrf_token"]}, json={"email": email},
        )
        self.assertEqual(response.status_code, 201, response.text)
        link = self.mailer.outbox[-1]["text"].splitlines()[-1]
        token = urlparse(link).path.rsplit("/", 1)[-1]
        user_client = TestClient(self.app)
        accepted = user_client.post(
            "/auth/invite/accept", json={"token": token, "password": "StrongPassword123"}
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        return user_client, accepted.json()

    def test_invited_user_is_active_and_profile_compiles_safely(self):
        user_client, session = self._invite_and_accept()
        me = user_client.get("/api/v1/me").json()
        self.assertEqual(me["account_status"], "active")
        self.assertIsNotNone(me["tenant"])
        profile = user_client.put(
            "/api/v1/profile", headers={"X-CSRF-Token": session["csrf_token"]},
            json={"preset_ids": ["okx-xau-actionzone-v1"],
                  "active_preset_id": "okx-xau-actionzone-v1",
                  "risk": {"risk_pct": 0.01, "max_position_per_trade_pct": 10,
                           "max_daily_loss_pct": 3, "max_leverage": 1,
                           "max_open_positions": 1}},
        )
        self.assertEqual(profile.status_code, 200, profile.text)
        slug = me["tenant"]["slug"]
        whitelist = json.loads(
            (self.settings.tenant_config_root / slug / "coin_whitelist.json").read_text()
        )
        params = whitelist["assets"][0]["strategy_params"]
        self.assertTrue(params["disable_stop_loss"])
        self.assertAlmostEqual(params["position_pct"], 0.95)
        self.assertTrue(whitelist["assets"][0]["cdc_pure_certified"])

    def test_failed_invite_provisioning_is_repaired_by_password_login(self):
        owner_login = self.client.post(
            "/auth/dev-login", params={"email": "iisara555@gmail.com"}
        )
        invited = self.client.post(
            "/api/v1/admin/invites",
            headers={"X-CSRF-Token": owner_login.json()["csrf_token"]},
            json={"email": "retry@example.com"},
        )
        self.assertEqual(invited.status_code, 201, invited.text)
        token = urlparse(self.mailer.outbox[-1]["text"].splitlines()[-1]).path.rsplit("/", 1)[-1]
        browser = TestClient(self.app)
        try:
            with patch.object(
                self.supervisor, "provision",
                side_effect=RuntimeError("simulated privileged helper outage"),
            ):
                accepted = browser.post(
                    "/auth/invite/accept",
                    json={"token": token, "password": "StrongPassword123"},
                )

            self.assertEqual(accepted.status_code, 503, accepted.text)
            self.assertNotIn("privileged helper outage", accepted.text)
            self.assertEqual(browser.get("/api/v1/me").status_code, 401)
            user = self.store.user_by_email("retry@example.com")
            self.assertEqual(user["account_status"], "active")
            tenant = self.store.tenant_for_user(user["id"])
            self.assertEqual(tenant["status"], "provision_failed")
            self.assertFalse(self.supervisor.workspace_ready(tenant["slug"]))

            login = browser.post(
                "/auth/login",
                json={"email": "retry@example.com", "password": "StrongPassword123"},
            )
            self.assertEqual(login.status_code, 200, login.text)
            self.assertEqual(browser.get("/api/v1/me").status_code, 200)
            self.assertTrue(self.supervisor.workspace_ready(tenant["slug"]))
            self.assertEqual(self.store.tenant_for_user(user["id"])["status"], "queued")
            with self.store.connection() as conn:
                events = {
                    row[0] for row in conn.execute(
                        "SELECT event_type FROM audit_events WHERE tenant_id=?", (tenant["id"],)
                    )
                }
            self.assertIn("tenant_provision_failed", events)
            self.assertIn("tenant_provision_repaired", events)
        finally:
            browser.close()

    def test_public_signup_is_disabled(self):
        response = self.client.post(
            "/auth/signup", json={"email": "public@example.com", "password": "StrongPassword123"}
        )
        self.assertEqual(response.status_code, 404)

    def test_invite_returns_manual_link_when_email_delivery_is_unavailable(self):
        app = create_app(
            self.settings,
            store=self.store,
            supervisor=self.supervisor,
            mailer=UnavailableMailer(),
        )
        with TestClient(app) as client:
            owner_login = client.post(
                "/auth/dev-login", params={"email": "iisara555@gmail.com"}
            )
            response = client.post(
                "/api/v1/admin/invites",
                headers={"X-CSRF-Token": owner_login.json()["csrf_token"]},
                json={"email": "manual@example.com"},
            )
            self.assertEqual(response.status_code, 201, response.text)
            payload = response.json()
            self.assertEqual(payload["delivery"], "manual")
            self.assertEqual(payload["delivery_detail"], "transactional email delivery is unavailable")
            self.assertTrue(payload["invite_url"].startswith("http://testserver/invite/"))
            token = urlparse(payload["invite_url"]).path.rsplit("/", 1)[-1]
            self.assertEqual(
                client.get("/auth/invite", params={"token": token}).json()["email"],
                "manual@example.com",
            )

    def test_password_reset_is_single_use_and_revokes_sessions(self):
        user_client, _ = self._invite_and_accept("reset@example.com")
        self.client.post("/auth/forgot-password", json={"email": "reset@example.com"})
        token = parse_qs(urlparse(self.mailer.outbox[-1]["text"].splitlines()[-1]).query)["token"][0]
        changed = self.client.post(
            "/auth/reset-password", json={"token": token, "password": "AnotherStrong456"}
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(user_client.get("/api/v1/me").status_code, 401)
        self.assertEqual(self.client.post(
            "/auth/reset-password", json={"token": token, "password": "ThirdStrongPassword789"}
        ).status_code, 400)

    def test_totp_setup_and_enable(self):
        login = self.client.post("/auth/dev-login", params={"email": "iisara555@gmail.com"})
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}
        setup = self.client.post("/auth/totp/setup", headers=headers)
        code = totp_code(setup.json()["secret"])
        enabled = self.client.post("/auth/totp/enable", headers=headers, json={"code": code})
        self.assertEqual(enabled.status_code, 200, enabled.text)
        self.assertEqual(len(enabled.json()["recovery_codes"]), 8)

    def test_predeploy_backup_verifies_databases_configs_and_secrets(self):
        tenant = self.store.tenant_for_user(self.owner["id"])
        self.supervisor.provision(tenant["slug"])
        secret = self.settings.tenant_config_root / tenant["slug"] / "secrets.env"
        secret.write_text("OKX_API_KEY=test\n", encoding="utf-8")
        runtime = self.settings.tenant_runtime_root / tenant["slug"]
        runtime.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(runtime / "xauby.db")) as conn, conn:
            conn.execute("CREATE TABLE position (id INTEGER PRIMARY KEY, side TEXT)")
            conn.execute("INSERT INTO position(side) VALUES ('short')")
        archive = create_backup(
            self.settings, Path(self.temp.name) / "backups", kind="predeploy",
            release_id="test-release", retention_days=30, include_secrets=True, host_config=[],
        )
        manifest = verify_backup(archive)
        self.assertEqual(manifest["release_id"], "test-release")
        self.assertGreaterEqual(manifest["verified_databases"], 2)
        self.assertFalse(manifest["includes_secrets"])
        self.assertTrue(manifest["credentials_encrypted_in_database"])


if __name__ == "__main__":
    unittest.main()
