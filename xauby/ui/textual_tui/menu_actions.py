"""Dispatch launcher menu actions after exiting the Textual TUI."""

from __future__ import annotations

import os
import time


def run_menu_action(action: str) -> bool:
    """Run a launcher menu action. Returns True if the Textual TUI should restart."""
    action = (action or "").strip().lower()
    if not action or action in ("dashboard", "menu"):
        return True

    if action == "exit":
        return False

    import launcher as launcher_mod
    from xauby.ui.menu import check_engine_status

    if action == "run_sim":
        eng, _ = check_engine_status()
        if eng == "RUNNING":
            print("\nEngine already running — open Dashboard (key 3).")
            time.sleep(2)
            return True
        launcher_mod.run_engine_with_tui(live_mode=False)
        return False

    if action == "run_live":
        eng, _ = check_engine_status()
        if eng == "RUNNING":
            print("\nEngine already running — open Dashboard (key 3).")
            time.sleep(2)
            return True
        print("\n[WARNING] LIVE TRADING — type CONFIRM to proceed.")
        if input(" Type 'CONFIRM': ").strip().upper() != "CONFIRM":
            print("Cancelled.")
            time.sleep(1.5)
            return True
        launcher_mod.run_engine_with_tui(live_mode=True)
        return False

    # config / system_check / db_tools are now in-app Textual screens (pushed
    # from MenuScreen); they no longer exit to the terminal.

    if action == "backtest":
        os.environ["XAUBY_START_SCREEN"] = "backtest"
        return True

    if action == "restart_service":
        from xauby.ui.textual_tui.terminal import restore_terminal

        try:
            launcher_mod.restart_bot_service(pause=False, clear_screen=False)
        finally:
            restore_terminal()
        print("\nPress Enter to return to the menu...")
        try:
            input()
        except (KeyboardInterrupt, EOFError):
            pass
        restore_terminal()
        return True

    return True
