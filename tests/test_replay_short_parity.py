"""Short-side replay parity (roadmap P0.5).

Replay could not be used as evidence for the short half of live exposure, for
four separate reasons. XAU now runs a config whose entire thesis is its short
side, so each of them is pinned here.

1. `signal_evaluated` carried only `action`. BUY/SELL are ambiguous: open_short
   and a long exit are both SELL; close_short and a long entry are both BUY.
2. The comparison matched on `action` alone, so "live opened a short, replay
   closed a long" scored as a MATCH.
3. `ReplayValidationState` never tracked which side was open, and the rebuilt
   MarketContext never received `position_side`, so the strategy replayed every
   short tick as though a long were open.
4. The stop-loss breach test was `price <= stop_loss` — correct for a long,
   exactly backwards for a short, whose stop sits above entry.
"""
from __future__ import annotations

import unittest

from xauby.observability.replay_validation import (
    ReplayValidationState,
    _describe,
)
from xauby.strategies.signal import buy, close_short, open_short, sell


class TestSignalVocabularyIsAmbiguous(unittest.TestCase):
    """The premise: action alone cannot identify the decision."""

    def test_open_short_and_long_exit_share_an_action(self):
        self.assertEqual(open_short("x").action, sell("y").action)

    def test_close_short_and_long_entry_share_an_action(self):
        self.assertEqual(close_short("x").action, buy("y").action)

    def test_intent_and_side_disambiguate_them(self):
        os_, se = open_short("x"), sell("y")
        self.assertNotEqual(
            (os_.action, os_.intent, os_.position_side),
            (se.action, se.intent, se.position_side),
        )
        cs, bu = close_short("x"), buy("y")
        self.assertNotEqual(
            (cs.action, cs.intent, cs.position_side),
            (bu.action, bu.intent, bu.position_side),
        )


class TestPositionSideTracking(unittest.TestCase):
    def _open(self, side=None):
        payload = {"stop_loss": 100.0}
        if side is not None:
            payload["position_side"] = side
        return {"event_type": "position_opened", "payload": payload}

    def test_short_open_is_recorded(self):
        st = ReplayValidationState()
        st.apply(self._open("SHORT"))
        self.assertTrue(st.has_position)
        self.assertEqual(st.position_side, "SHORT")

    def test_restored_short_seeds_a_new_run(self):
        st = ReplayValidationState()
        st.apply({
            "event_type": "position_restored",
            "payload": {"position_side": "SHORT", "stop_loss": 106.0},
        })
        self.assertTrue(st.has_position)
        self.assertEqual(st.position_side, "SHORT")
        self.assertEqual(st.stop_loss, 106.0)

    def test_long_open_is_recorded(self):
        st = ReplayValidationState()
        st.apply(self._open("LONG"))
        self.assertEqual(st.position_side, "LONG")

    def test_legacy_event_without_side_defaults_long(self):
        # Long opens historically omitted position_side; those runs must still
        # replay as longs rather than as an unknown side.
        st = ReplayValidationState()
        st.apply(self._open(None))
        self.assertEqual(st.position_side, "LONG")

    def test_close_clears_the_side(self):
        st = ReplayValidationState()
        st.apply(self._open("SHORT"))
        st.apply({"event_type": "position_closed", "payload": {}})
        self.assertFalse(st.has_position)
        self.assertIsNone(st.position_side)


class TestStopBreachIsSideAware(unittest.TestCase):
    """A short's stop is above entry; it breaches when price RISES."""

    def _state(self, side, stop=100.0):
        st = ReplayValidationState()
        st.apply({
            "event_type": "position_opened",
            "payload": {"position_side": side, "stop_loss": stop},
        })
        return st

    def _tick(self, st, price):
        st.apply({"event_type": "tick"}, tick_price=price)

    def test_short_breaches_when_price_rises(self):
        st = self._state("SHORT")
        self._tick(st, 101.0)
        self.assertEqual(st.sl_breach_count, 1)

    def test_short_does_not_breach_when_price_falls(self):
        # The old code counted this as a breach — a short in profit was being
        # reported as stopped out.
        st = self._state("SHORT")
        self._tick(st, 99.0)
        self.assertEqual(st.sl_breach_count, 0)

    def test_long_breaches_when_price_falls(self):
        st = self._state("LONG")
        self._tick(st, 99.0)
        self.assertEqual(st.sl_breach_count, 1)

    def test_long_does_not_breach_when_price_rises(self):
        st = self._state("LONG")
        self._tick(st, 101.0)
        self.assertEqual(st.sl_breach_count, 0)

    def test_breach_count_accumulates_then_resets(self):
        st = self._state("SHORT")
        self._tick(st, 101.0)
        self._tick(st, 102.0)
        self.assertEqual(st.sl_breach_count, 2)
        self._tick(st, 99.0)
        self.assertEqual(st.sl_breach_count, 0)

    def test_no_breach_without_a_position(self):
        st = ReplayValidationState()
        self._tick(st, 1.0)
        self.assertEqual(st.sl_breach_count, 0)


class TestSemanticComparison(unittest.TestCase):
    """Mirror the matching logic in validate_run."""

    @staticmethod
    def _match(live, replay):
        la, li, lsd = live
        ra, ri, rsd = replay
        ok = la == ra
        if ok and li and ri:
            ok = li == ri
        if ok and lsd and rsd:
            ok = lsd == rsd
        return ok

    def test_open_short_vs_long_exit_is_a_mismatch(self):
        # The headline bug: both are SELL, and this used to score as a match.
        live = ("SELL", "OPEN", "SHORT")
        replay = ("SELL", "CLOSE", "LONG")
        self.assertFalse(self._match(live, replay))

    def test_close_short_vs_long_entry_is_a_mismatch(self):
        self.assertFalse(self._match(("BUY", "CLOSE", "SHORT"), ("BUY", "OPEN", "LONG")))

    def test_identical_short_decisions_match(self):
        self.assertTrue(self._match(("SELL", "OPEN", "SHORT"), ("SELL", "OPEN", "SHORT")))

    def test_identical_long_decisions_match(self):
        self.assertTrue(self._match(("BUY", "OPEN", "LONG"), ("BUY", "OPEN", "LONG")))

    def test_legacy_events_degrade_to_action_only(self):
        # Runs recorded before intent/position_side existed must not report a
        # mismatch on every signal just because the fields are absent.
        self.assertTrue(self._match(("SELL", "", ""), ("SELL", "OPEN", "SHORT")))

    def test_differing_actions_still_mismatch(self):
        self.assertFalse(self._match(("BUY", "OPEN", "LONG"), ("SELL", "CLOSE", "LONG")))


class TestMismatchRendering(unittest.TestCase):
    def test_short_is_distinguishable_in_a_report(self):
        self.assertEqual(_describe("SELL", "OPEN", "SHORT"), "SELL(OPEN/SHORT)")
        self.assertEqual(_describe("SELL", "CLOSE", "LONG"), "SELL(CLOSE/LONG)")
        self.assertNotEqual(
            _describe("SELL", "OPEN", "SHORT"), _describe("SELL", "CLOSE", "LONG")
        )

    def test_legacy_events_render_as_plain_action(self):
        self.assertEqual(_describe("SELL", "", ""), "SELL")


if __name__ == "__main__":
    unittest.main()
