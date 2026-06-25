import unittest

from xauby.observability.incidents import (
    filter_timeline_events,
    format_filter_status,
    is_trade_focus_event,
)
from xauby.ui.incident_presenter import (
    IncidentExplorerState,
    format_timeline_display_lines,
    handle_explorer_action,
    render_incident_summary_lines,
)


class TestIncidentExplorerState(unittest.TestCase):
    def setUp(self):
        self.state = IncidentExplorerState(
            runs=[
                {"run_id": "a"},
                {"run_id": "b"},
                {"run_id": "c"},
            ]
        )

    def test_move_selection_down(self):
        self.assertTrue(handle_explorer_action(self.state, "j"))
        self.assertEqual(self.state.selected_idx, 1)

    def test_move_selection_up(self):
        self.state.selected_idx = 2
        self.assertTrue(handle_explorer_action(self.state, "k"))
        self.assertEqual(self.state.selected_idx, 1)

    def test_cycle_timeline_filter(self):
        self.assertEqual(self.state.timeline_filter, "notable")
        handle_explorer_action(self.state, "a")
        self.assertEqual(self.state.timeline_filter, "trade")
        handle_explorer_action(self.state, "a")
        self.assertEqual(self.state.timeline_filter, "all")
        handle_explorer_action(self.state, "a")
        self.assertEqual(self.state.timeline_filter, "notable")

    def test_trade_focus_event_filter(self):
        events = [
            {"event_type": "tick", "seq": 1},
            {"event_type": "signal_evaluated", "payload": {"action": "HOLD"}, "seq": 2},
            {"event_type": "signal_evaluated", "payload": {"action": "BUY"}, "seq": 3},
            {"event_type": "order_filled", "seq": 4},
            {"event_type": "signal_evaluated", "payload": {"action": "SELL"}, "seq": 5},
        ]
        self.assertTrue(is_trade_focus_event(events[2]))
        self.assertTrue(is_trade_focus_event(events[3]))
        self.assertFalse(is_trade_focus_event(events[0]))
        self.assertFalse(is_trade_focus_event(events[1]))
        filtered = filter_timeline_events(events, "trade")
        self.assertEqual(len(filtered), 3)
        self.assertEqual(
            format_filter_status("trade", shown=3, total=5),
            "Filter: Trade (BUY, SELL, ORDER_FILLED) · 3 of 5 event(s)",
        )

    def test_scroll_bounds(self):
        handle_explorer_action(self.state, "-")
        self.assertGreaterEqual(self.state.scroll, 0)

    def test_scroll_down_increases_offset(self):
        self.state.scroll = 0
        self.assertTrue(handle_explorer_action(self.state, "down"))
        self.assertGreater(self.state.scroll, 0)

    def test_timeline_displays_newest_event_first(self):
        events = [
            {"seq": 1, "ts": "2026-01-01T10:00:00", "event_type": "tick"},
            {"seq": 99, "ts": "2026-01-01T12:00:00", "event_type": "order_filled"},
        ]
        newest_first = list(reversed(events))
        lines, _ = format_timeline_display_lines(
            newest_first, width=80, scroll=0, max_rows=5, is_mobile=True
        )
        self.assertTrue(lines)
        self.assertIn("99", lines[0])
        self.assertNotEqual(lines[0].find("99"), -1)

    def test_summary_recommends_action_for_step_size_errors(self):
        events = [
            {
                "seq": 1,
                "ts": "2026-01-01T10:00:00",
                "event_type": "engine_started",
                "symbol": "XAUTUSDT",
            },
            {
                "seq": 2,
                "ts": "2026-01-01T10:01:00",
                "event_type": "order_failed",
                "symbol": "XAUTUSDT",
                "payload": {
                    "error": "LIVE BUY API Error: Qty not increased by step size.",
                    "code": -4023,
                },
            },
            {
                "seq": 3,
                "ts": "2026-01-01T10:02:00",
                "event_type": "order_failed",
                "symbol": "XAUTUSDT",
                "payload": {
                    "error": "LIVE BUY API Error: Qty not increased by step size.",
                    "code": -4023,
                },
            },
            {
                "seq": 4,
                "ts": "2026-01-01T10:03:00",
                "event_type": "order_failed",
                "symbol": "XAUTUSDT",
                "payload": {
                    "error": "LIVE BUY API Error: Qty not increased by step size.",
                    "code": -4023,
                },
            },
        ]
        lines = render_incident_summary_lines(
            events,
            {
                "total": 4,
                "notable": 4,
                "types": {"engine_started": 1, "order_failed": 3},
                "started_at": "2026-01-01T10:00:00",
                "ended_at": "2026-01-01T10:03:00",
                "symbol": "XAUTUSDT",
            },
            90,
        )
        text = "\n".join(lines)
        self.assertIn("CRITICAL", text)
        self.assertIn("Fix order quantity filters", text)
        self.assertIn("order_failed x3", text)


if __name__ == "__main__":
    unittest.main()
