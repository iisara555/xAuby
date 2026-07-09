import json
import os
import sqlite3
import tempfile
import threading
import unittest
from base64 import b64encode
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest import mock

from xauby.webui.server import (
    candles_payload,
    create_server,
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


if __name__ == "__main__":
    unittest.main()
