import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import requests
import yaml
from fastapi.testclient import TestClient

from xauby.saas.app import create_app
from xauby.saas.settings import SaaSSettings
from xauby.saas.store import ControlPlaneStore
from xauby.saas.supervisor import TenantSupervisor

# Deliberately one literal with an obvious "xxxx" run: scripts/scan_secrets.py
# captures only the first quoted chunk, so splitting this with + would hide
# the run that marks it a placeholder and trip the tracked-file scan.
FAKE_TOKEN = "123456789:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
FAKE_CHAT = "123456789"


def _response(status: int, payload: dict):
    stub = unittest.mock.Mock()
    stub.status_code = status
    stub.ok = 200 <= status < 300
    stub.json.return_value = payload
    return stub


def _ok_get_me(username: str = "my_xauby_bot"):
    return _response(200, {"ok": True, "result": {"username": username}})


class TelegramWorkspaceTests(unittest.TestCase):
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
        self.tenant = self.store.tenant_for_user(
            str(self.store.user_by_email("owner@example.com")["id"])
        )

    def tearDown(self):
        self.temp.cleanup()

    # --- helpers ---------------------------------------------------------
    def connect(self, chat_id: str = FAKE_CHAT, token: str = FAKE_TOKEN):
        return self.client.post(
            "/api/v1/telegram/connect", headers=self.headers,
            json={"bot_token": token, "chat_id": chat_id},
        )

    def tenant_config(self) -> dict:
        path = self.settings.tenant_config_root / self.tenant["slug"] / "bot_config.yaml"
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    # --- connect ---------------------------------------------------------
    def test_connect_stores_encrypted_token_and_never_returns_it(self):
        response = self.connect()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(FAKE_TOKEN, response.text)
        self.assertNotIn("credential_blob", response.text)
        connection = response.json()["connection"]
        self.assertEqual(connection["token_last4"], "xxxx")
        self.assertEqual(connection["status"], "stored")

        with closing(sqlite3.connect(str(self.settings.database_path))) as conn:
            blob = conn.execute(
                "SELECT credential_blob FROM telegram_connections WHERE tenant_id=?",
                (self.tenant["id"],),
            ).fetchone()[0]
        self.assertTrue(blob)
        self.assertNotIn(FAKE_TOKEN, blob)

    def test_connect_rejects_malformed_token_and_chat_id(self):
        self.assertEqual(self.connect(token="nocolon").status_code, 422)
        self.assertEqual(self.connect(token="123456789:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx").status_code, 422)
        self.assertEqual(self.connect(chat_id="not-a-chat").status_code, 422)

    def test_connect_rejects_line_breaks(self):
        # The regex alone would pass this: Python's $ matches before a
        # trailing newline, and the value lands in a 0600 env file.
        response = self.client.post(
            "/api/v1/telegram/connect", headers=self.headers,
            json={"bot_token": FAKE_TOKEN + "\n", "chat_id": FAKE_CHAT},
        )
        self.assertEqual(response.status_code, 422)

    def test_channel_chat_id_disables_command_polling(self):
        self.assertEqual(self.connect(chat_id="@mychannel").status_code, 200)
        notifications = self.tenant_config()["notifications"]
        self.assertFalse(notifications["telegram_command_polling_enabled"])

        self.assertEqual(self.connect(chat_id=FAKE_CHAT).status_code, 200)
        notifications = self.tenant_config()["notifications"]
        self.assertTrue(notifications["telegram_command_polling_enabled"])

    def test_connect_forces_telegram_alert_channel(self):
        # "console" short-circuits delivery entirely in the engine.
        self.supervisor.update_notification_config(
            self.tenant["slug"], {"alert_channel": "console"}
        )
        self.assertEqual(self.connect().status_code, 200)
        self.assertEqual(self.tenant_config()["notifications"]["alert_channel"], "telegram")

    # --- test endpoint ---------------------------------------------------
    def test_test_endpoint_sends_message_and_marks_tested(self):
        self.connect()
        with patch("xauby.saas.app.requests.get", return_value=_ok_get_me()) as get, \
             patch("xauby.saas.app.requests.post",
                   return_value=_response(200, {"ok": True})) as post:
            response = self.client.post("/api/v1/telegram/test", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        connection = response.json()["connection"]
        self.assertEqual(connection["status"], "tested")
        self.assertEqual(connection["bot_username"], "my_xauby_bot")
        self.assertIsNotNone(connection["tested_at"])
        # The token belongs in the URL path, server-side only.
        self.assertIn(FAKE_TOKEN, get.call_args[0][0])
        self.assertIn(FAKE_TOKEN, post.call_args[0][0])
        self.assertEqual(post.call_args.kwargs["json"]["chat_id"], FAKE_CHAT)

    def test_test_endpoint_failure_does_not_leak_token_in_detail(self):
        self.connect()
        leaky = requests.ConnectionError(
            f"HTTPSConnectionPool: /bot{FAKE_TOKEN}/sendMessage failed"
        )
        with patch("xauby.saas.app.requests.get", return_value=_ok_get_me()), \
             patch("xauby.saas.app.requests.post", side_effect=leaky):
            response = self.client.post("/api/v1/telegram/test", headers=self.headers)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(FAKE_TOKEN, response.text)
        self.assertNotIn("x" * 35, response.text)
        self.assertEqual(
            self.store.telegram_connection(self.tenant["id"])["status"], "failed"
        )

    def test_test_endpoint_maps_bad_token_to_fixed_message(self):
        self.connect()
        with patch("xauby.saas.app.requests.get",
                   return_value=_response(401, {"ok": False, "description": "Unauthorized"})):
            response = self.client.post("/api/v1/telegram/test", headers=self.headers)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"], "Telegram rejected this bot token.")

    def test_test_endpoint_maps_chat_not_found(self):
        self.connect()
        with patch("xauby.saas.app.requests.get", return_value=_ok_get_me()), \
             patch("xauby.saas.app.requests.post",
                   return_value=_response(400, {"ok": False,
                                                "description": "Bad Request: chat not found"})):
            response = self.client.post("/api/v1/telegram/test", headers=self.headers)
        self.assertEqual(response.status_code, 502)
        self.assertIn("could not find that chat id", response.json()["detail"].lower())

    def test_test_endpoint_is_throttled(self):
        self.connect()
        with patch("xauby.saas.app.requests.get",
                   return_value=_response(401, {"ok": False})):
            statuses = [
                self.client.post("/api/v1/telegram/test", headers=self.headers).status_code
                for _ in range(7)
            ]
        self.assertIn(429, statuses)

    def test_test_endpoint_requires_a_connection(self):
        self.assertEqual(
            self.client.post("/api/v1/telegram/test", headers=self.headers).status_code, 409
        )

    # --- preferences -----------------------------------------------------
    def test_preferences_patch_writes_all_four_blocks(self):
        self.connect()
        response = self.client.patch(
            "/api/v1/telegram/preferences", headers=self.headers,
            json={"trade_lifecycle": False, "risk_safety": False,
                  "system_health": False, "periodic_reports": False},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["restart_required"])
        cfg = self.tenant_config()
        self.assertFalse(cfg["notifications"]["notify_position_updates"])
        self.assertFalse(cfg["notifications"]["notify_guard_blocks"])
        self.assertFalse(cfg["notifications"]["notify_regime_changes"])
        self.assertEqual(cfg["monitoring"]["heartbeat_interval_minutes"], 0)
        self.assertFalse(cfg["weekly_review"]["send_telegram"])
        self.assertFalse(cfg["daily_digest"]["send_telegram"])

        self.client.patch(
            "/api/v1/telegram/preferences", headers=self.headers,
            json={"system_health": True, "periodic_reports": True},
        )
        cfg = self.tenant_config()
        self.assertEqual(
            cfg["monitoring"]["heartbeat_interval_minutes"],
            TenantSupervisor.HEARTBEAT_ON_MINUTES,
        )
        self.assertTrue(cfg["weekly_review"]["send_telegram"])

    def test_preferences_rejects_unknown_key(self):
        self.connect()
        response = self.client.patch(
            "/api/v1/telegram/preferences", headers=self.headers,
            json={"trade_lifecycle": True, "wat": True},
        )
        # Unknown keys are dropped by the pydantic model, so the request is
        # still valid; an empty change set is what gets rejected.
        self.assertEqual(response.status_code, 200)
        empty = self.client.patch(
            "/api/v1/telegram/preferences", headers=self.headers, json={},
        )
        self.assertEqual(empty.status_code, 422)

    def test_supervisor_rejects_unsupported_setting(self):
        with self.assertRaises(ValueError):
            self.supervisor.update_notification_config(self.tenant["slug"], {"nope": True})

    def test_preferences_get_reflects_written_config(self):
        self.connect()
        self.client.patch(
            "/api/v1/telegram/preferences", headers=self.headers,
            json={"trade_lifecycle": False},
        )
        response = self.client.get("/api/v1/telegram/preferences")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["trade_lifecycle"])

    def test_apply_profile_preserves_notification_block(self):
        """A trading-profile change must not silently reset alert settings."""
        self.connect()
        self.client.patch(
            "/api/v1/telegram/preferences", headers=self.headers,
            json={"trade_lifecycle": False, "periodic_reports": False},
        )
        catalog = self.client.get("/api/v1/catalog").json()
        preset = catalog["presets"][0]
        saved = self.client.put(
            "/api/v1/profile", headers=self.headers,
            json={"preset_ids": [preset["id"]], "active_preset_id": preset["id"],
                  "risk": {"risk_pct": 0.005}},
        )
        self.assertEqual(saved.status_code, 200)
        cfg = self.tenant_config()
        self.assertFalse(cfg["notifications"]["notify_position_updates"])
        self.assertFalse(cfg["weekly_review"]["send_telegram"])
        self.assertEqual(cfg["notifications"]["alert_channel"], "telegram")

    # --- disconnect + surfacing -----------------------------------------
    def test_disconnect_removes_row_and_disables_alerts(self):
        self.connect()
        response = self.client.delete("/api/v1/telegram", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.store.telegram_connection(self.tenant["id"]))
        self.assertFalse(self.tenant_config()["notifications"]["send_alerts"])

    def test_bot_endpoint_exposes_masked_telegram_connection(self):
        self.connect()
        response = self.client.get("/api/v1/bot")
        self.assertEqual(response.status_code, 200)
        connection = response.json()["telegram_connection"]
        self.assertEqual(connection["token_last4"], "xxxx")
        self.assertEqual(connection["chat_id"], FAKE_CHAT)
        self.assertNotIn(FAKE_TOKEN, response.text)
        self.assertNotIn("credential_blob", response.text)

    def test_audit_events_carry_no_secrets(self):
        self.connect()
        with closing(sqlite3.connect(str(self.settings.database_path))) as conn:
            rows = conn.execute(
                "SELECT event_type,payload_json FROM audit_events "
                "WHERE event_type LIKE 'telegram%'"
            ).fetchall()
        self.assertTrue(rows)
        for event_type, payload_json in rows:
            self.assertNotIn(FAKE_TOKEN, payload_json)
            self.assertNotIn(FAKE_CHAT, payload_json)
            self.assertNotIn("xxxx", payload_json)
            json.loads(payload_json)

    # --- restart (applies settings the engine reads only at startup) ------
    def test_restart_rematerializes_credentials_and_audits(self):
        self.connect()
        calls: list[str] = []
        original = self.supervisor.materialize_credentials
        self.supervisor.materialize_credentials = lambda slug: (
            calls.append(slug) or original(slug)
        )
        response = self.client.post("/api/v1/bot/restart", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        # The restart must rebuild the env file, otherwise the engine would come
        # back without the token that was just saved.
        self.assertEqual(calls, [self.tenant["slug"]])

        with closing(sqlite3.connect(str(self.settings.database_path))) as conn:
            events = [row[0] for row in conn.execute(
                "SELECT event_type FROM audit_events WHERE tenant_id=?",
                (self.tenant["id"],),
            )]
        self.assertIn("engine_restarted", events)

    def test_restart_does_not_consume_an_engine_slot(self):
        self.connect()
        for _ in range(3):
            self.assertEqual(
                self.client.post("/api/v1/bot/restart", headers=self.headers).status_code,
                200,
            )

    def test_restart_of_stopped_tenant_respects_capacity(self):
        self.connect()
        with patch.object(self.store, "reserve_engine_slot", return_value=False) as reserve, \
                patch.object(self.supervisor, "restart") as restart:
            response = self.client.post("/api/v1/bot/restart", headers=self.headers)
        self.assertEqual(response.status_code, 409, response.text)
        reserve.assert_called_once_with(self.tenant["id"], self.settings.max_active_engines)
        restart.assert_not_called()

    def test_restart_is_csrf_protected(self):
        self.assertEqual(self.client.post("/api/v1/bot/restart").status_code, 403)

    def test_writes_are_csrf_protected(self):
        self.assertEqual(
            self.client.post(
                "/api/v1/telegram/connect",
                json={"bot_token": FAKE_TOKEN, "chat_id": FAKE_CHAT},
            ).status_code,
            403,
        )


if __name__ == "__main__":
    unittest.main()
