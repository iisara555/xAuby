import json
import tempfile
import unittest
from pathlib import Path

import requests

from xauby.saas.runtime import RuntimeGateway
from xauby.saas.settings import SaaSSettings
from xauby.saas.supervisor import TenantSupervisor


class _Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def iter_content(self, chunk_size=65536):
        del chunk_size
        yield json.dumps(self.payload).encode("utf-8")


class RuntimeGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = SaaSSettings(
            project_root=root,
            data_root=root / "data",
            tenant_config_root=root / "config",
            tenant_runtime_root=root / "runtime",
            database_path=root / "control.db",
            public_base_url="http://testserver",
            session_secret="test_session_secret",
            systemctl_bin="mock",
            cookie_secure=False,
            dev_login_enabled=True,
            legacy_owner_slug="owner-itsara",
            legacy_webui_url="http://100.64.0.10:8787",
            legacy_webui_token="test_runtime_token",
        )
        self.supervisor = TenantSupervisor(self.settings)

    def tearDown(self):
        self.temp.cleanup()

    def test_legacy_bridge_normalizes_payload_and_keeps_token_server_side(self):
        calls = []

        def fake_get(url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/api/state"):
                return _Response({"ok": True, "state": {"focus_symbol": "XAUUSDT"}, "age_sec": 1})
            if url.endswith("/api/dashboard-detail"):
                return _Response({"ok": True, "operator": {"mode": "live"}})
            raise AssertionError(url)

        gateway = RuntimeGateway(self.settings, self.supervisor, http_get=fake_get)
        payload = gateway.snapshot("owner-itsara")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["source"], "legacy_webui")
        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["state"]["focus_symbol"], "XAUUSDT")
        self.assertNotIn("test_runtime_token", json.dumps(payload))
        self.assertEqual(
            calls[0][1]["headers"]["Authorization"], "Bearer test_runtime_token"
        )
        self.assertFalse(calls[0][1]["allow_redirects"])

    def test_legacy_bridge_failure_is_degraded_without_internal_details(self):
        def failed_get(*args, **kwargs):
            del args, kwargs
            raise requests.ConnectionError("private address and token must not leak")

        gateway = RuntimeGateway(self.settings, self.supervisor, http_get=failed_get)
        payload = gateway.snapshot("owner-itsara")
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["stale"])
        self.assertEqual(payload["error"], "runtime data is temporarily unavailable")
        self.assertNotIn("private address", json.dumps(payload))

    def test_non_owner_never_uses_legacy_bridge(self):
        gateway = RuntimeGateway(self.settings, self.supervisor, http_get=lambda *a, **k: None)
        payload = gateway.snapshot("customer-one")
        self.assertEqual(payload["source"], "tenant_engine")
        self.assertFalse(payload["read_only"])


if __name__ == "__main__":
    unittest.main()
