import unittest
from unittest.mock import MagicMock

from xauby.ui.textual_tui.layout import (
    STACKED_BREAKPOINT,
    PHONE_BREAKPOINT,
    is_stacked_layout,
    is_phone_layout,
    sync_dashboard_layout,
)


class TestTextualLayout(unittest.TestCase):
    def test_breakpoints(self):
        self.assertEqual(STACKED_BREAKPOINT, 110)
        self.assertEqual(PHONE_BREAKPOINT, 75)

    def test_stacked_layout(self):
        self.assertFalse(is_stacked_layout(110))
        self.assertTrue(is_stacked_layout(109))
        self.assertTrue(is_stacked_layout(80))
        self.assertTrue(is_stacked_layout(40))

    def test_phone_layout(self):
        self.assertFalse(is_phone_layout(75))
        self.assertTrue(is_phone_layout(74))
        self.assertTrue(is_phone_layout(40))

    def test_sync_dashboard_layout_wide(self):
        node = MagicMock()
        sync_dashboard_layout(node, 120)
        node.remove_class.assert_any_call("stacked-layout")
        node.remove_class.assert_any_call("phone-layout")
        node.add_class.assert_any_call("wide-layout")

    def test_sync_dashboard_layout_tablet(self):
        node = MagicMock()
        sync_dashboard_layout(node, 90)
        node.add_class.assert_any_call("stacked-layout")
        node.remove_class.assert_any_call("phone-layout")
        node.remove_class.assert_any_call("wide-layout")

    def test_sync_dashboard_layout_phone(self):
        node = MagicMock()
        sync_dashboard_layout(node, 60)
        node.add_class.assert_any_call("stacked-layout")
        node.add_class.assert_any_call("phone-layout")
        node.remove_class.assert_any_call("wide-layout")


class TestTextualFocusKeys(unittest.TestCase):
    def test_bracket_keys_use_textual_names(self):
        from xauby.ui.textual_tui.layout import FOCUS_PREV_KEYS, FOCUS_NEXT_KEYS

        self.assertIn("left_square_bracket", FOCUS_PREV_KEYS)
        self.assertIn("right_square_bracket", FOCUS_NEXT_KEYS)
        self.assertIn("0", FOCUS_PREV_KEYS)
        self.assertIn("9", FOCUS_NEXT_KEYS)
        self.assertIn("comma", FOCUS_PREV_KEYS)
        self.assertIn("period", FOCUS_NEXT_KEYS)


if __name__ == "__main__":
    unittest.main()
