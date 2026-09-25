"""Desktop geometry checks for the Textual screens and their compact fallback."""

import unittest
from unittest.mock import patch

from xauby.ui.textual_tui.app import XAubyTextualApp
from xauby.ui.textual_tui.quick_config.maintenance_screens import SystemCheckScreen
from xauby.ui.textual_tui.quick_config.modals import NumberModal
from xauby.ui.textual_tui.quick_config.schema import SUBMENUS
from xauby.ui.textual_tui.quick_config.screens import QuickConfigScreen


class TestDesktopLayout(unittest.IsolatedAsyncioTestCase):
    async def test_all_screens_fit_desktop_and_trade_log_uses_available_height(self):
        app = XAubyTextualApp()
        with patch.object(SystemCheckScreen, "_load", lambda self: None):
            async with app.run_test(size=(160, 45)) as pilot:
                for name in (
                    "menu", "dashboard", "tradelog", "incidents", "backtest",
                    "quick_config", "system_check", "db_tools",
                ):
                    await app.push_screen(name)
                    await pilot.pause()
                    self.assertEqual(app.screen.region.width, 160, name)
                    self.assertEqual(app.screen.region.height, 45, name)

                await app.push_screen("menu")
                await pilot.pause()
                menu = app.screen.query_one("#menu-shell")
                self.assertGreater(menu.region.x, 0)
                self.assertAlmostEqual(menu.region.x, (160 - menu.region.width) // 2, delta=1)

                await app.push_screen("tradelog")
                await pilot.pause()
                left = app.screen.query_one("#tradelog-left-col")
                table = app.screen.query_one("#tl-datatable")
                self.assertLess(left.region.width, 160 // 2)
                self.assertGreater(table.region.height, 30)

                await app.push_screen("incidents")
                await pilot.pause()
                self.assertGreater(app.screen.query_one("#incident-left-col").region.width, 38)

                for submenu_id in SUBMENUS:
                    await app.push_screen(QuickConfigScreen(app.db, submenu_id))
                    await pilot.pause()
                    body = app.screen.query_one("#qc-body")
                    self.assertGreater(body.region.x, 0, submenu_id)
                    self.assertLessEqual(body.region.right, 160, submenu_id)

                await app.push_screen(NumberModal("Risk", 2, minimum=0, maximum=10))
                await pilot.pause()
                modal = app.screen.query_one("#qc-modal-box")
                self.assertGreater(modal.region.x, 0)
                self.assertLessEqual(modal.region.right, 160)

    async def test_stacked_trade_log_after_terminal_resize(self):
        app = XAubyTextualApp()
        async with app.run_test(size=(160, 45)) as pilot:
            await app.push_screen("tradelog")
            await pilot.pause()
            await pilot.resize_terminal(100, 36)
            await pilot.pause()
            left = app.screen.query_one("#tradelog-left-col")
            right = app.screen.query_one("#tradelog-right-col")
            table = app.screen.query_one("#tl-datatable")
            self.assertEqual(left.region.width, right.region.width)
            self.assertGreaterEqual(right.region.y, left.region.bottom)
            self.assertLessEqual(table.region.height, 20)
