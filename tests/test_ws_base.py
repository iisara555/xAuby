import json
import unittest
from typing import Any, Dict, Optional

from xauby.api.ws_base import ThreadedWebSocketBase, build_tick


class FakeWebSocket(ThreadedWebSocketBase):
    """Minimal subclass exercising the shared base machinery."""

    @staticmethod
    def default_url() -> str:
        return "wss://example/stream"

    def _stream_url(self) -> str:
        streams = [f"{s.lower()}@t" for s in self.symbols]
        return f"{self.ws_url}?streams={'/'.join(streams)}"

    def _parse_message(self, message: str) -> Optional[Dict[str, Any]]:
        payload = json.loads(message)
        last = float(payload.get("c") or 0.0)
        if last <= 0:
            return None
        return build_tick(payload.get("s", ""), last, payload.get("b"), payload.get("a"), payload.get("P"))


class TestThreadedWebSocketBase(unittest.TestCase):
    def setUp(self):
        self.ticks = []
        self.statuses = []
        self.ws = FakeWebSocket(
            symbols=["btc_usdt", "ethusdt"],
            on_tick=self.ticks.append,
            on_status=self.statuses.append,
            config={},
        )

    def test_default_url_and_stream_url(self):
        self.assertEqual(self.ws.ws_url, "wss://example/stream")
        url = self.ws._stream_url()
        self.assertIn("btcusdt@t", url)
        self.assertIn("ethusdt@t", url)

    def test_on_message_emits_tick_and_caches(self):
        msg = json.dumps({"s": "BTCUSDT", "c": "50000", "b": "49990", "a": "50010", "P": "1.5"})
        self.ws._on_message(None, msg)

        self.assertEqual(len(self.ticks), 1)
        tick = self.ticks[0]
        self.assertEqual(tick["symbol"], "BTCUSDT")
        self.assertEqual(tick["last"], 50000.0)
        self.assertEqual(tick["bid"], 49990.0)
        self.assertEqual(tick["ask"], 50010.0)
        self.assertEqual(tick["percent_change_24h"], 1.5)
        self.assertIn("timestamp", tick)
        self.assertIn("monotonic_ts", tick)
        self.assertEqual(self.ws.get_latest_price("BTCUSDT"), 50000.0)

    def test_zero_price_message_skipped(self):
        self.ws._on_message(None, json.dumps({"s": "BTCUSDT", "c": "0"}))
        self.assertEqual(self.ticks, [])

    def test_status_emitted_on_close(self):
        self.ws._on_close(None, 1006, "abnormal")
        self.assertEqual(len(self.statuses), 1)
        self.assertEqual(self.statuses[0]["event"], "ws_disconnected")

    def test_reconnect_on_open_resets_delay(self):
        self.ws.reconnect_delay = 8.0
        self.ws._on_open(None)
        self.assertEqual(self.ws.reconnect_delay, 1.0)

    def test_trim_price_cache_drops_unsubscribed(self):
        self.ws._on_message(None, json.dumps({"s": "BTCUSDT", "c": "50000"}))
        self.ws._on_message(None, json.dumps({"s": "DOGEUSDT", "c": "0.1"}))
        removed = self.ws.trim_price_cache(["BTCUSDT"])
        self.assertEqual(removed, 1)
        self.assertIsNone(self.ws.get_latest_price("DOGEUSDT"))
        self.assertEqual(self.ws.get_latest_price("BTCUSDT"), 50000.0)


class TestBuildTick(unittest.TestCase):
    def test_non_positive_price_returns_none(self):
        self.assertIsNone(build_tick("BTCUSDT", 0.0))
        self.assertIsNone(build_tick("BTCUSDT", -1.0))

    def test_normalizes_symbol(self):
        tick = build_tick("btc_usdt", 100.0)
        self.assertEqual(tick["symbol"], "BTCUSDT")


if __name__ == "__main__":
    unittest.main()
