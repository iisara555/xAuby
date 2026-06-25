"""Navigation wiring tests for Backtest screen (source-level, no Textual runtime)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCREENS_SRC = (ROOT / "xauby/ui/textual_tui/screens.py").read_text(encoding="utf-8")
APP_SRC = (ROOT / "xauby/ui/textual_tui/app.py").read_text(encoding="utf-8")
MENU_SRC = (ROOT / "xauby/ui/textual_tui/menu_screen.py").read_text(encoding="utf-8")
BACKTEST_NATIVE_SRC = (ROOT / "xauby/ui/textual_tui/backtest_native.py").read_text(encoding="utf-8")
WIDGETS_SRC = (ROOT / "xauby/ui/textual_tui/widgets.py").read_text(encoding="utf-8")


class TestBacktestNavWiring(unittest.TestCase):
    def test_nav_bindings_include_backtest(self):
        self.assertIn('"b,4", "show_backtest"', SCREENS_SRC)

    def test_prev_next_cycle_includes_backtest(self):
        self.assertIn('view == "backtest"', SCREENS_SRC)
        self.assertIn('switch_screen("backtest")', SCREENS_SRC)

    def test_app_registers_backtest_screen(self):
        self.assertIn("BacktestScreen", APP_SRC)
        self.assertIn('install_screen(BacktestScreen', APP_SRC)
        self.assertIn('"backtest"', APP_SRC)

    def test_menu_seven_opens_backtest_page(self):
        # The menu routes the "backtest" action through _run_action, which
        # switch_screen()s (instead of exiting) for dashboard/backtest.
        self.assertIn('"backtest"', MENU_SRC)
        self.assertIn('("dashboard", "backtest")', MENU_SRC)
        self.assertIn("switch_screen(action)", MENU_SRC)
        self.assertIn("Backtest", MENU_SRC)

    def test_backtest_screen_has_r_binding(self):
        self.assertIn('"r,R,shift+r", "backtest_run"', SCREENS_SRC)
        self.assertIn("def action_backtest_run", SCREENS_SRC)
        self.assertIn("self.run_worker(body._run_backtest()", SCREENS_SRC)

    def test_backtest_screen_has_g_binding(self):
        self.assertIn('"g,G", "backtest_optimize"', SCREENS_SRC)
        self.assertIn("def action_backtest_optimize", SCREENS_SRC)
        self.assertIn("self.run_worker(body._run_optimize()", SCREENS_SRC)

    def test_backtest_screen_has_o_binding(self):
        self.assertIn('"o,O", "backtest_apply_best"', SCREENS_SRC)
        self.assertIn("def action_backtest_apply_best", SCREENS_SRC)

    def test_backtest_screen_handles_r_in_on_key(self):
        self.assertIn('key = event.key.lower()', SCREENS_SRC)
        self.assertIn("def on_key(self, event)", SCREENS_SRC.split("class BacktestScreen")[1])

    def test_backtest_body_has_no_widget_level_r_binding(self):
        self.assertNotIn('Binding("r"', BACKTEST_NATIVE_SRC)

    def test_footer_shows_run_hint_on_backtest_view(self):
        self.assertIn('active_view == "backtest"', WIDGETS_SRC)
        self.assertIn("[R] Run", WIDGETS_SRC)
        self.assertIn("[G] Optimize", WIDGETS_SRC)

    def test_footer_surfaces_tradelog_and_incident_hotkeys(self):
        # Discoverability: screen-specific hotkeys must be exposed in the footer.
        self.assertIn('active_view == "tradelog"', WIDGETS_SRC)
        self.assertIn("Sort", WIDGETS_SRC)
        self.assertIn("Scope", WIDGETS_SRC)
        self.assertIn("[V] Validate", WIDGETS_SRC)
        self.assertIn("Refresh", WIDGETS_SRC)


if __name__ == "__main__":
    unittest.main()
