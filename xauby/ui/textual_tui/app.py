import os
import sys

from textual.app import App

from xauby.database.db import LiteDB
from xauby.ui.textual_tui.screens import (
    DashboardScreen,
    TradeLogScreen,
    IncidentExplorerScreen,
    BacktestScreen,
)
from xauby.ui.textual_tui.menu_screen import MenuScreen

from xauby.meta import PRODUCT_NAME
from xauby.ui.textual_tui.capabilities import attached_tenant, is_read_only_mode


class XAubyTextualApp(App):
    """The Main Textual TUI Application for xAuby."""

    TITLE = f"{PRODUCT_NAME} Trading Dashboard"
    CSS_PATH = "styles.tcss"
    ENABLE_COMMAND_PALETTE = False

    def __init__(self, **kwargs):
        super().__init__(ansi_color=True, **kwargs)
        self.db = LiteDB(readonly=True)

    def on_resize(self, event) -> None:
        from xauby.ui.textual_tui.layout import is_phone_layout
        if is_phone_layout(event.size.width):
            self.add_class("phone-mode")
        else:
            self.remove_class("phone-mode")

    def on_mount(self) -> None:
        self.install_screen(DashboardScreen(self.db), "dashboard")
        self.install_screen(TradeLogScreen(self.db), "tradelog")
        self.install_screen(IncidentExplorerScreen(self.db), "incidents")
        self.install_screen(MenuScreen(self.db), "menu")
        if not is_read_only_mode():
            # Quick Config imports the legacy launcher helpers. Keep that whole
            # control surface out of hosted observer processes.
            from xauby.ui.textual_tui.quick_config import (
                DBToolsScreen,
                QuickConfigHubScreen,
                SystemCheckScreen,
            )

            self.install_screen(BacktestScreen(self.db), "backtest")
            self.install_screen(QuickConfigHubScreen(self.db), "quick_config")
            self.install_screen(SystemCheckScreen(self.db), "system_check")
            self.install_screen(DBToolsScreen(self.db), "db_tools")

        start = os.environ.pop("XAUBY_START_SCREEN", None)
        if start is None:
            start = "menu" if os.environ.get("FROM_LAUNCHER") == "true" else "dashboard"
        allowed = {"dashboard", "menu", "tradelog", "incidents"}
        if not is_read_only_mode():
            allowed.update({"backtest", "quick_config", "system_check", "db_tools"})
        if start not in allowed:
            start = "dashboard"
        tenant = attached_tenant()
        if tenant:
            self.title = f"{PRODUCT_NAME} · {tenant} · READ-ONLY"
        self.push_screen(start)


def main():
    """Run the Textual TUI Application."""
    # Ensure working directory is the project root
    script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    if script_dir and os.path.isfile(os.path.join(script_dir, "launcher.py")):
        os.chdir(script_dir)

    # Parent shells (IDE, CI, some SSH clients) may export NO_COLOR=1 which forces
    # Textual's Monochrome filter and strips the dashboard palette.
    os.environ.pop("NO_COLOR", None)
    os.environ.pop("FORCE_COLOR", None)
    os.environ.setdefault("COLORTERM", "truecolor")

    from xauby.ui.textual_tui.terminal import restore_terminal

    app = XAubyTextualApp()
    app.run(mouse=True)
    restore_terminal()

    # Hosted attach is a terminal observer, never a launcher.  Do not dispatch
    # inherited/menu actions and never exec launcher.py after it exits.
    if is_read_only_mode():
        return

    from xauby.ui.textual_tui.menu_actions import run_menu_action

    action = os.environ.pop("XAUBY_MENU_ACTION", None)
    if action:
        restart_tui = run_menu_action(action)
        if restart_tui:
            restore_terminal()
            launcher_path = os.path.join(os.getcwd(), "launcher.py")
            if os.path.isfile(launcher_path):
                os.environ["FROM_LAUNCHER"] = "true"
                if "XAUBY_START_SCREEN" not in os.environ:
                    os.environ["XAUBY_START_SCREEN"] = "dashboard"
                os.execv(sys.executable, [sys.executable, launcher_path])
            # Fallback: run TUI directly if launcher.py is missing
            app = XAubyTextualApp()
            app.run(mouse=True)
            restore_terminal()
        return

    if os.environ.get("FROM_LAUNCHER") != "true":
        launcher_path = os.path.join(os.getcwd(), "launcher.py")
        if os.path.isfile(launcher_path):
            os.execv(sys.executable, [sys.executable, launcher_path])


if __name__ == "__main__":
    main()
