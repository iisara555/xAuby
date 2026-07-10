import http.client
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from base64 import b64encode
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from unittest import mock

from xauby.webui.server import (
    _sign_session,
    _verify_session,
    candles_payload,
    create_server,
    dashboard_detail_payload,
    health_payload,
    meta_payload,
    recent_events_payload,
    trades_payload,
)


class WebUIServerTest(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"XAUBY_HOME": "core"}, clear=False)
        self.env.start()
        os.environ.pop("XAUBY_INSTANCE_ID", None)
        os.environ.pop("SQLITE_DB_PATH", None)
        os.environ.pop("XAUBY_WEBUI_PASSWORD", None)
        os.environ.pop("XAUBY_WEBUI_TOKEN", None)
        os.environ.pop("XAUBY_WEBUI_ALLOW_UNAUTH_REMOTE", None)
        self.tmp = tempfile.TemporaryDirectory()
        self.project_root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()
        self.env.stop()

    def write_state(self, payload):
        path = os.path.join(self.project_root, "core", "logs", "xauby_bot_state.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return path

    def create_db(self):
        path = os.path.join(self.project_root, "core", "xauby.db")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path)
        try:
            conn.execute(
                """
                CREATE TABLE closed_trades (
                    symbol TEXT,
                    closed_at TEXT,
                    net_pnl REAL,
                    trigger TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE prices (
                    symbol TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    timeframe TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO closed_trades VALUES (?, ?, ?, ?)",
                ("XAUTUSDT", "2026-07-01T12:00:00Z", 4.2, "tp"),
            )
            conn.execute(
                "INSERT INTO closed_trades VALUES (?, ?, ?, ?)",
                ("BTCUSDT", "2026-07-01T11:00:00Z", -1.5, "sl"),
            )
            for idx in range(5):
                conn.execute(
                    "INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "XAUUSDT",
                        1000 + idx,
                        "4h",
                        4000 + idx,
                        4010 + idx,
                        3990 + idx,
                        4005 + idx,
                        10 + idx,
                    ),
                )
            conn.execute(
                "INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("XAUUSDT", 2000, "1h", 1, 2, 0.5, 1.5, 2),
            )
            conn.commit()
        finally:
            conn.close()
        return path

    def serve(self):
        server = create_server("127.0.0.1", 0, project_root=self.project_root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def cleanup():
            server.shutdown()
            thread.join(2)
            server.server_close()

        self.addCleanup(cleanup)
        host, port = server.server_address
        return f"http://{host}:{port}"

    def get_json(self, url):
        with urlopen(url, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    def get_json_with_basic_auth(self, url, username="xauby", password="pw"):
        token = b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        req = Request(url, headers={"Authorization": f"Basic {token}"})
        with urlopen(req, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_state_endpoint_reads_runtime_state(self):
        self.write_state(
            {
                "focus_symbol": "XAUTUSDT",
                "by_symbol": {
                    "XAUTUSDT": {
                        "symbol": "XAUTUSDT",
                        "current_price": 3360.5,
                    }
                },
            }
        )

        base = self.serve()
        payload = self.get_json(f"{base}/api/state")

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["state"]["focus_symbol"], "XAUTUSDT")
        self.assertIsInstance(payload["age_sec"], (int, float))

    def test_state_endpoint_redacts_secret_like_keys(self):
        self.write_state(
            {
                "focus_symbol": "XAUTUSDT",
                "api_secret": "SHOULD_NOT_LEAK",
                "nested": {"telegram_token": "ALSO_SECRET"},
            }
        )

        base = self.serve()
        payload = self.get_json(f"{base}/api/state")

        self.assertEqual(payload["state"]["api_secret"], "[REDACTED]")
        self.assertEqual(payload["state"]["nested"]["telegram_token"], "[REDACTED]")
        self.assertNotIn("SHOULD_NOT_LEAK", json.dumps(payload))

    def test_webui_requires_auth_when_password_is_configured(self):
        with mock.patch.dict(os.environ, {"XAUBY_WEBUI_PASSWORD": "pw"}, clear=False):
            base = self.serve()

            with self.assertRaises(HTTPError) as raised:
                urlopen(f"{base}/api/state", timeout=3)
            self.assertEqual(raised.exception.code, 401)

            payload = self.get_json_with_basic_auth(f"{base}/api/state", password="pw")
            self.assertFalse(payload["ok"])

    def test_webui_refuses_remote_bind_without_auth(self):
        with self.assertRaises(ValueError):
            create_server("0.0.0.0", 0, project_root=self.project_root)

    def test_webui_allows_remote_bind_with_auth(self):
        with mock.patch.dict(os.environ, {"XAUBY_WEBUI_PASSWORD": "pw"}, clear=False):
            server = create_server("0.0.0.0", 0, project_root=self.project_root)
            server.server_close()

    def test_meta_payload_rejects_unsafe_avatar_url(self):
        with open(os.path.join(self.project_root, "bot_config.yaml"), "w", encoding="utf-8") as handle:
            handle.write("cli_ui:\n  webui_avatar: javascript:alert(1)\n")

        payload = meta_payload(self.project_root)

        self.assertEqual(payload["avatar_url"], "/xau-logo.svg")

    def test_recent_events_prefers_focused_symbol_snapshot(self):
        self.write_state(
            {
                "focus_symbol": "XAUTUSDT",
                "recent_events": [{"event_type": "top"}],
                "by_symbol": {
                    "XAUTUSDT": {"recent_events": [{"event_type": "focused"}]},
                    "BTCUSDT": {"recent_events": [{"event_type": "other"}]},
                },
            }
        )

        payload = recent_events_payload(self.project_root)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["events"], [{"event_type": "focused"}])

    def test_dashboard_detail_payload_contains_operator_and_regime_sections(self):
        self.create_db()
        self.write_state(
            {
                "focus_symbol": "XAUTUSDT",
                "by_symbol": {
                    "XAUTUSDT": {
                        "symbol": "XAUTUSDT",
                        "execution_mode": "live",
                        "read_only": False,
                        "current_price": 4127.1,
                        "position": {
                            "state": "bought",
                            "position_side": "SHORT",
                            "quantity": 0.033,
                            "entry_price": 4115.3,
                            "mark_price": 4127.1,
                            "stop_loss": 4100.0,
                            "take_profit": 3950.0,
                            "estimated_total_fees": 0.13,
                            "feed_health": "OK",
                        },
                        "signal_meta": {
                            "action": "HOLD",
                            "reason": "Holding active position",
                            "checklist": [{"label": "Stop", "value": "OK", "ok": True}],
                        },
                        "risk": {"risk_pct": 0.02, "sl_atr_mult": 2.0},
                        "latency": {"api_latency_ms": 42, "ws_tick_age_ms": 100},
                        "regime": {
                            "regime": "BEAR_TREND_WEAK",
                            "trend": "BEARISH",
                            "confidence": 0.54,
                            "risk_state": "DEFENSIVE",
                            "reasons": [{"label": "Trend Bearish", "supportive": False}],
                        },
                        "macro_guard": {"enabled": False, "summary": "Disabled"},
                        "recent_events": [{"event_type": "signal_evaluated"}],
                    }
                },
            }
        )

        payload = dashboard_detail_payload(self.project_root)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["operator"]["symbol"], "XAUTUSDT")
        self.assertTrue(payload["operator"]["position"]["open"])
        self.assertEqual(payload["operator"]["position"]["side"], "SHORT")
        self.assertEqual(payload["regime_detail"]["state"], "BEAR_TREND_WEAK")
        self.assertEqual(payload["activity"]["event_count"], 1)
        self.assertEqual(payload["activity"]["trade_count"], 1)

    def test_dashboard_detail_payload_handles_missing_state(self):
        payload = dashboard_detail_payload(self.project_root)

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["operator"], {})
        self.assertEqual(payload["regime_detail"], {})
        self.assertEqual(payload["activity"], {"events": [], "trades": []})

    def test_trades_endpoint_reads_sqlite_in_read_only_mode(self):
        self.create_db()

        payload = trades_payload(self.project_root, limit=1, symbol="xaut_usdt")

        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["trades"]), 1)
        self.assertEqual(payload["trades"][0]["symbol"], "XAUTUSDT")
        self.assertEqual(payload["trades"][0]["net_pnl"], 4.2)

    def test_candles_payload_reads_recent_ohlc_in_chronological_order(self):
        self.create_db()

        payload = candles_payload(self.project_root, symbol="xau_usdt", timeframe="4h", limit=3)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["symbol"], "XAUUSDT")
        self.assertEqual(payload["timeframe"], "4h")
        self.assertEqual([row["timestamp"] for row in payload["candles"]], [1002, 1003, 1004])
        self.assertEqual(payload["candles"][-1]["close"], 4009)

    def test_candles_payload_includes_warmed_cdc_series(self):
        self.create_db()
        with open(os.path.join(self.project_root, "bot_config.yaml"), "w", encoding="utf-8") as handle:
            handle.write("strategies:\n  cdc_action_zone:\n    ap_smoothing: 2\n")
        conn = sqlite3.connect(os.path.join(self.project_root, "core", "xauby.db"))
        try:
            for idx in range(40):
                price = 100.0 + idx
                conn.execute(
                    "INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "CDCUSDT",
                        10_000 + idx,
                        "4h",
                        price - 0.5,
                        price + 1.0,
                        price - 1.0,
                        price,
                        100 + idx,
                    ),
                )
            conn.commit()
        finally:
            conn.close()

        payload = candles_payload(self.project_root, symbol="cdcusdt", timeframe="4h", limit=5)

        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["candles"]), 5)
        self.assertEqual(payload["warmup"], 40)
        latest = payload["candles"][-1]
        self.assertIn("ap", latest)
        self.assertIn("ema12", latest)
        self.assertIn("ema26", latest)
        self.assertIn("zone", latest)
        self.assertIsNotNone(latest["ema12"])
        self.assertIsNotNone(latest["ema26"])
        self.assertNotEqual(latest["zone"], "UNKNOWN")

    def test_candles_endpoint_clamps_limit(self):
        self.create_db()
        base = self.serve()

        payload = self.get_json(f"{base}/api/candles?symbol=XAUUSDT&timeframe=4h&limit=999")

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["limit"], 80)
        self.assertEqual(len(payload["candles"]), 5)

    def test_static_index_is_served(self):
        base = self.serve()

        with urlopen(f"{base}/", timeout=3) as response:
            html = response.read().decode("utf-8")

        self.assertEqual(response.status, 200)
        self.assertIn("xAuby WebUI", html)

    def test_static_server_blocks_path_traversal(self):
        base = self.serve()

        with self.assertRaises(HTTPError) as raised:
            urlopen(f"{base}/../bot_config.yaml", timeout=3)

        self.assertEqual(raised.exception.code, 404)

    def test_health_payload_reports_log_counts_without_raw_lines(self):
        log_dir = os.path.join(self.project_root, "core", "logs")
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "xauby_bot.log"), "w", encoding="utf-8") as handle:
            handle.write("2026-07-02 ERROR order rejected: secret-ish detail\n")
            handle.write("2026-07-02 WARNING funding rate spike\n")

        payload = health_payload(self.project_root)

        log_scan = payload["log_scan"]
        self.assertEqual(log_scan["errors_count"], 1)
        self.assertEqual(log_scan["warnings_count"], 1)
        self.assertNotIn("errors_found", log_scan)
        self.assertNotIn("warnings_found", log_scan)
        self.assertIn("Recent log errors: 1", payload["anomalies"])

    def test_health_payload_does_not_create_db_file(self):
        # The health probe must stay a pure reader: no core/xauby.db appearing
        # as a side effect of polling /api/health before the engine ever ran.
        health_payload(self.project_root)

        self.assertFalse(
            os.path.exists(os.path.join(self.project_root, "core", "xauby.db"))
        )

    def test_missing_state_returns_error_payload(self):
        base = self.serve()

        payload = self.get_json(f"{base}/api/state")

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["state"], {})
        self.assertIn("state file not found", payload["error"])


class WebUISessionSigningTest(unittest.TestCase):
    def test_round_trip(self):
        secret = b"secret-a"
        token = _sign_session(secret, int(time.time()) + 60)
        self.assertTrue(_verify_session(secret, token))

    def test_rejects_wrong_secret(self):
        token = _sign_session(b"secret-a", int(time.time()) + 60)
        self.assertFalse(_verify_session(b"secret-b", token))

    def test_rejects_expired(self):
        secret = b"secret-a"
        token = _sign_session(secret, int(time.time()) - 1)
        self.assertFalse(_verify_session(secret, token))

    def test_rejects_garbage(self):
        self.assertFalse(_verify_session(b"secret-a", ""))
        self.assertFalse(_verify_session(b"secret-a", "not-a-token"))
        self.assertFalse(_verify_session(b"secret-a", "123."))


class WebUILoginTest(unittest.TestCase):
    """Branded /login flow: cookie sessions for browsers, 401 kept for /api."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"XAUBY_HOME": "core"}, clear=False)
        self.env.start()
        os.environ.pop("XAUBY_WEBUI_PASSWORD", None)
        os.environ.pop("XAUBY_WEBUI_TOKEN", None)
        os.environ.pop("XAUBY_WEBUI_COOKIE_SECURE", None)
        os.environ.pop("XAUBY_WEBUI_SESSION_SECRET", None)
        self.tmp = tempfile.TemporaryDirectory()
        self.project_root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()
        self.env.stop()

    serve = WebUIServerTest.serve
    get_json_with_basic_auth = WebUIServerTest.get_json_with_basic_auth

    def request(self, base, method, path, body=None, headers=None):
        parsed = urlparse(base)
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            payload = response.read()
            return response.status, dict(response.getheaders()), payload
        finally:
            conn.close()

    def login(self, base, password):
        return self.request(
            base,
            "POST",
            "/login",
            body=urlencode({"password": password}),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    def session_cookie(self, headers):
        raw = headers.get("Set-Cookie", "")
        return raw.split(";", 1)[0] if raw else ""

    def test_login_page_served_without_auth(self):
        with mock.patch.dict(os.environ, {"XAUBY_WEBUI_PASSWORD": "pw"}, clear=False):
            base = self.serve()
            status, headers, body = self.request(base, "GET", "/login")

        self.assertEqual(status, 200)
        self.assertIn(b'action="/login"', body)
        self.assertIn("Content-Security-Policy", headers)
        self.assertNotIn("WWW-Authenticate", headers)

    def test_html_redirects_to_login_but_api_keeps_401(self):
        with mock.patch.dict(os.environ, {"XAUBY_WEBUI_PASSWORD": "pw"}, clear=False):
            base = self.serve()
            page_status, page_headers, _ = self.request(base, "GET", "/")
            api_status, api_headers, _ = self.request(base, "GET", "/api/state")

        self.assertEqual(page_status, 302)
        self.assertEqual(page_headers.get("Location"), "/login")
        self.assertNotIn("WWW-Authenticate", page_headers)
        self.assertEqual(api_status, 401)
        self.assertIn("WWW-Authenticate", api_headers)

    def test_wrong_password_redirects_with_error_and_no_cookie(self):
        with mock.patch.dict(os.environ, {"XAUBY_WEBUI_PASSWORD": "pw"}, clear=False):
            base = self.serve()
            status, headers, _ = self.login(base, "wrong")

        self.assertEqual(status, 302)
        self.assertEqual(headers.get("Location"), "/login?error=1")
        self.assertNotIn("Set-Cookie", headers)

    def test_correct_password_sets_session_cookie_that_authorizes(self):
        with mock.patch.dict(os.environ, {"XAUBY_WEBUI_PASSWORD": "pw"}, clear=False):
            base = self.serve()
            status, headers, _ = self.login(base, "pw")
            self.assertEqual(status, 302)
            self.assertEqual(headers.get("Location"), "/")
            raw_cookie = headers.get("Set-Cookie", "")
            self.assertIn("HttpOnly", raw_cookie)
            self.assertIn("SameSite=Lax", raw_cookie)
            self.assertNotIn("Secure", raw_cookie)

            cookie = self.session_cookie(headers)
            page_status, _, _ = self.request(base, "GET", "/", headers={"Cookie": cookie})
            api_status, _, _ = self.request(
                base, "GET", "/api/state", headers={"Cookie": cookie}
            )

        self.assertEqual(page_status, 200)
        self.assertEqual(api_status, 200)

    def test_tampered_cookie_still_redirects(self):
        with mock.patch.dict(os.environ, {"XAUBY_WEBUI_PASSWORD": "pw"}, clear=False):
            base = self.serve()
            status, headers, _ = self.request(
                base, "GET", "/", headers={"Cookie": "xauby_session=999999.deadbeef"}
            )

        self.assertEqual(status, 302)
        self.assertEqual(headers.get("Location"), "/login")

    def test_logout_expires_cookie(self):
        with mock.patch.dict(os.environ, {"XAUBY_WEBUI_PASSWORD": "pw"}, clear=False):
            base = self.serve()
            status, headers, _ = self.request(base, "GET", "/logout")

        self.assertEqual(status, 302)
        self.assertEqual(headers.get("Location"), "/login")
        self.assertIn("Max-Age=0", headers.get("Set-Cookie", ""))

    def test_preauth_allowlist_is_exact(self):
        with mock.patch.dict(os.environ, {"XAUBY_WEBUI_PASSWORD": "pw"}, clear=False):
            base = self.serve()
            for path in ("/style.css", "/xau-logo.svg", "/login.js"):
                status, _, _ = self.request(base, "GET", path)
                self.assertEqual(status, 200, path)
            for path in ("/app.js", "/avatar-default.svg", "/index.html"):
                status, headers, _ = self.request(base, "GET", path)
                self.assertEqual(status, 302, path)
                self.assertEqual(headers.get("Location"), "/login", path)

    def test_post_to_other_paths_is_404(self):
        with mock.patch.dict(os.environ, {"XAUBY_WEBUI_PASSWORD": "pw"}, clear=False):
            base = self.serve()
            status, _, _ = self.request(base, "POST", "/api/state")

        self.assertEqual(status, 404)

    def test_no_password_mode_keeps_zero_friction(self):
        base = self.serve()
        page_status, _, _ = self.request(base, "GET", "/")
        login_status, login_headers, _ = self.request(base, "GET", "/login")

        self.assertEqual(page_status, 200)
        self.assertEqual(login_status, 302)
        self.assertEqual(login_headers.get("Location"), "/")

    def test_cookie_secure_flag_opt_in(self):
        env = {"XAUBY_WEBUI_PASSWORD": "pw", "XAUBY_WEBUI_COOKIE_SECURE": "1"}
        with mock.patch.dict(os.environ, env, clear=False):
            base = self.serve()
            _, headers, _ = self.login(base, "pw")

        self.assertIn("Secure", headers.get("Set-Cookie", ""))

    def test_basic_auth_still_accepted_for_api(self):
        with mock.patch.dict(os.environ, {"XAUBY_WEBUI_PASSWORD": "pw"}, clear=False):
            base = self.serve()
            payload = self.get_json_with_basic_auth(f"{base}/api/state", password="pw")

        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main()
