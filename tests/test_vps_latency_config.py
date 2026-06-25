import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from xauby.api.client import LiteBinanceClient
from xauby.api.websocket import LiteBinanceWebSocket
from xauby.observability.emitter import EventEmitter
from xauby.observability.state import StateExporter


class TestVpsLatencyConfig(unittest.TestCase):
    def test_binance_client_uses_api_config(self):
        with patch("xauby.api.client.ClockSync.sync", return_value=0.0):
            client = LiteBinanceClient(
                "",
                "",
                "https://api.example.test",
                config={
                    "api": {
                        "connect_timeout_seconds": 2,
                        "read_timeout_seconds": 7,
                        "max_attempts": 4,
                        "get_retry_backoff_seconds": 0.25,
                        "recv_window_ms": 7000,
                        "pool_maxsize": 3,
                    }
                },
            )
        self.assertEqual(client.timeout, (2.0, 7.0))
        self.assertEqual(client.max_attempts, 4)
        self.assertEqual(client.get_retry_backoff_seconds, 0.25)
        self.assertEqual(client.recv_window_ms, 7000)

    def test_websocket_uses_configured_ping_and_reconnect(self):
        ws = LiteBinanceWebSocket(
            ["BTCUSDT"],
            lambda tick: None,
            config={
                "websocket": {
                    "ping_interval": 25,
                    "ping_timeout": 8,
                    "reconnect_max_delay": 30,
                    "reconnect_multiplier": 1.2,
                }
            },
        )
        self.assertEqual(ws.ping_interval, 25)
        self.assertEqual(ws.ping_timeout, 8)
        self.assertEqual(ws.reconnect_max_delay, 30.0)
        self.assertEqual(ws.reconnect_multiplier, 1.2)

    def test_event_emitter_can_keep_high_frequency_events_in_memory_only(self):
        store = Mock()
        emitter = EventEmitter(
            store=store,
            symbol="BTCUSDT",
            run_id="run-test",
            config={
                "observability": {
                    "durable_high_frequency_events": False,
                    "high_frequency_event_types": ["tick"],
                }
            },
        )
        emitter.emit("tick", price=100.0)
        emitter.emit("order_filled", side="BUY")
        self.assertEqual(store.append.call_count, 1)
        self.assertEqual(store.append.call_args.kwargs["event_type"], "order_filled")
        self.assertEqual(len(emitter.recent(limit=10)), 2)

    def test_state_exporter_supports_compact_json(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "state.json")
            exporter = StateExporter(path=path, indent=None)
            exporter.write({"a": 1, "b": {"c": 2}})
            raw = open(path, "r", encoding="utf-8").read()
            self.assertNotIn("\n", raw)
            self.assertEqual(json.loads(raw)["b"]["c"], 2)


if __name__ == "__main__":
    unittest.main()
