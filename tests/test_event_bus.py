"""Event bus dispatch tests."""
import unittest

from xauby.observability.event_bus import EventBus


class TestEventBus(unittest.TestCase):
    def test_subscriber_receives_event(self):
        bus = EventBus()
        received = []

        def handler(event_type, symbol, payload):
            received.append((event_type, symbol, payload))

        bus.subscribe("regime_changed", handler)
        bus.dispatch("regime_changed", "BTCUSDT", old_regime="A", new_regime="B")
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0], "regime_changed")
        self.assertEqual(received[0][1], "BTCUSDT")


if __name__ == "__main__":
    unittest.main()
