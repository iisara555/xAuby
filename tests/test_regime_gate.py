"""Candle-boundary gating for the regime router."""
import unittest

from xauby.engine.regime_gate import should_route_on_candle


class TestRegimeGate(unittest.TestCase):
    def test_first_closed_candle_routes(self):
        self.assertTrue(should_route_on_candle(1000, 0))

    def test_same_candle_does_not_reroute(self):
        # Within one candle the router must advance exactly once, no matter how
        # many ticks fire — this is what makes debounce_candles count candles.
        self.assertFalse(should_route_on_candle(1000, 1000))

    def test_new_candle_routes_again(self):
        self.assertTrue(should_route_on_candle(2000, 1000))

    def test_unknown_candle_never_routes(self):
        self.assertFalse(should_route_on_candle(0, 0))
        self.assertFalse(should_route_on_candle(0, 1000))

    def test_three_candle_confirmation_needs_three_distinct_candles(self):
        # Simulate the loop gate over many ticks across 3 candles: the router is
        # invoked only on candle advance, so 3 candles => 3 advances (not 3 ticks).
        routed = 0
        last_routed = 0
        ticks_per_candle = 60
        for candle_ts in (1000, 2000, 3000):
            for _tick in range(ticks_per_candle):
                if should_route_on_candle(candle_ts, last_routed):
                    routed += 1
                    last_routed = candle_ts
        self.assertEqual(routed, 3)


if __name__ == "__main__":
    unittest.main()
