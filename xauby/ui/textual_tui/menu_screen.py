"""Textual launcher menu screen.

Native, best-practice rebuild: a focusable ``OptionList`` for the menu, a
live-refreshing status line, and the reused xAuby banner — all styled via
``styles.tcss``. The launcher action contract is unchanged: selecting an item
sets ``XAUBY_MENU_ACTION`` and exits the app (or switches screen for
dashboard/backtest); ``menu_actions.run_menu_action`` + ``app.main`` handle the
rest (including the ``os.execv`` restart).
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Dict, List, Tuple

from rich.text import Text
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from textual.screen import Screen

from xauby.ui.menu import check_engine_status
from xauby.ui.textual_tui.banner import render_launcher_banner_lines
from xauby.ui.textual_tui.capabilities import attached_tenant, is_read_only_mode
from xauby.ui.textual_tui.widgets import AppHeader, AppFooter
from xauby.utils.colors import (
    C_BOLD,
    C_GREEN,
    C_MUTED,
    C_PRIMARY,
    C_RED,
    C_RESET,
    C_GEMINI_CYAN,
    C_GEMINI_BLUE,
)
from xauby.utils.common import TH_TZ


# (digit, label, action id). Order within groups is defined by _MENU_GROUPS.
MENU_OPTIONS: List[Tuple[str, str, str]] = [
    ("1", "Run Engine — Simulated", "run_sim"),
    ("2", "Run Engine — Live", "run_live"),
    ("3", "Open Dashboard", "dashboard"),
    ("4", "Quick Configuration Editor", "config"),
    ("5", "System Configuration Check", "system_check"),
    ("6", "Database Diagnostics & Backup", "db_tools"),
    ("7", "Open Backtest Page", "backtest"),
    ("8", "Restart Bot Engine Service", "restart_service"),
    ("9", "Exit Launcher", "exit"),
    ("0", "Exit Launcher", "exit"),
]

_MENU_GROUPS: List[Tuple[str, List[str]]] = [
    ("ENGINE", ["1", "2"]),
    ("INTERFACE", ["3", "7"]),
    ("TOOLS", ["4", "5", "6", "8"]),
    ("EXIT", ["9"]),
]

# digit -> action, and digit -> label (lookups for accelerators / prompts).
_ACTION_BY_DIGIT: Dict[str, str] = {num: action for num, _, action in MENU_OPTIONS}
_LABEL_BY_DIGIT: Dict[str, str] = {num: label for num, label, _ in MENU_OPTIONS}

_READ_ONLY_MENU_OPTIONS: List[Tuple[str, str, str]] = [
    ("1", "Open Dashboard", "dashboard"),
    ("2", "Open Trade Log", "tradelog"),
    ("3", "Open Incidents", "incidents"),
    ("9", "Exit TUI", "exit"),
]
_READ_ONLY_MENU_GROUPS: List[Tuple[str, List[str]]] = [
    ("READ-ONLY VIEWS", ["1", "2", "3"]),
    ("EXIT", ["9"]),
]


def _active_menu() -> tuple[List[Tuple[str, str, str]], List[Tuple[str, List[str]]]]:
    if is_read_only_mode():
        return _READ_ONLY_MENU_OPTIONS, _READ_ONLY_MENU_GROUPS
    return MENU_OPTIONS, _MENU_GROUPS


def _option_prompt(num: str, label: str, action: str) -> Text:
    """Colored prompt for one menu row, e.g. ``[1] ▸  Run Engine — Simulated``."""
    icon = "■" if action == "exit" else "▸"
    if action == "run_live":
        color = C_RED
    elif action == "run_sim":
        color = C_GREEN
    elif action in ("dashboard", "backtest"):
        color = C_GEMINI_CYAN
    elif action == "exit":
        color = C_MUTED
    else:
        color = C_GEMINI_BLUE
    label_color = C_MUTED if action == "exit" else f"{C_BOLD}{C_PRIMARY}"
    raw = f"{color}[{num}]{C_RESET}  {color}{icon}{C_RESET}  {label_color}{label}{C_RESET}"
    return Text.from_ansi(raw)


class LauncherBanner(Static):
    """xAuby ASCII/gradient logo; full width so the wide logo can render."""

    def on_mount(self) -> None:
        self._render_banner()

    def on_resize(self, event) -> None:  # noqa: ARG002 - event unused
        self._render_banner()

    def _render_banner(self) -> None:
        width = self.size.width or getattr(self.app, "size", None) and self.app.size.width or 80
        lines = render_launcher_banner_lines(int(width))
        self.update(Text.from_ansi("\n".join(lines)))


class LauncherStatus(Static):
    """Live one-line system status (engine, DB, pairs, clock)."""

    def on_mount(self) -> None:
        self._refresh()
        self.set_interval(2.0, self._refresh)

    def _refresh(self) -> None:
        try:
            eng, is_sim = check_engine_status()
        except Exception:
            eng, is_sim = "OFFLINE", None
        if eng == "RUNNING":
            engine = f"{C_GREEN}● RUNNING ({'SIM' if is_sim else 'LIVE'}){C_RESET}"
        else:
            engine = f"{C_MUTED}○ OFFLINE{C_RESET}"

        try:
            from xauby.database.db import resolve_db_path
            db_ok = os.path.exists(resolve_db_path())
        except Exception:
            db_ok = False
        db = f"{C_GREEN}DB ✓{C_RESET}" if db_ok else f"{C_RED}DB ✗{C_RESET}"

        clock = datetime.now(TH_TZ).strftime("%H:%M:%S")
        sep = f"{C_MUTED}  ·  {C_RESET}"
        line = sep.join([
            f"Engine {engine}",
            db,
            (
                f"{C_PRIMARY}RO · {attached_tenant()}{C_RESET}"
                if is_read_only_mode()
                else f"{C_PRIMARY}XAUT · BTC{C_RESET}"
            ),
            f"{C_MUTED}{clock} ICT{C_RESET}",
        ])
        self.update(Text.from_ansi(line))


class LauncherMenu(OptionList):
    """Grouped, focusable launcher menu. Option ids carry the action id."""

    def on_mount(self) -> None:
        self.clear_options()
        options, active_groups = _active_menu()
        action_by_digit = {num: action for num, _, action in options}
        label_by_digit = {num: label for num, label, _ in options}
        groups = list(active_groups)
        first_action = None
        for gi, (group_name, nums) in enumerate(groups):
            self.add_option(Option(Text.from_ansi(f"{C_MUTED}{C_BOLD}{group_name}{C_RESET}"), disabled=True))
            for num in nums:
                action = action_by_digit.get(num, "")
                label = label_by_digit.get(num, "")
                if action:
                    self.add_option(Option(_option_prompt(num, label, action), id=action))
                    if first_action is None:
                        first_action = action
            if gi != len(groups) - 1:
                self.add_option(None)  # separator
        if first_action is not None:
            try:
                self.highlighted = self.get_option_index(first_action)
            except Exception:
                pass


class MenuScreen(Screen):
    """Launcher menu as a native Textual screen.

    Inherits plain ``Screen`` so the focused OptionList owns arrow/Enter/mouse
    navigation natively; digit keys 1-9/0 are screen-level priority accelerators.
    """

    BINDINGS = [
        Binding("escape,d", "show_dashboard", "Dashboard", show=False),
        Binding("t", "show_tradelog", "Log", show=False),
        Binding("i", "show_incidents", "Incidents", show=False),
        Binding("ctrl+m", "show_dashboard", "Dashboard", show=False),
        Binding("q", "quit_app", "Quit", show=False),
        Binding("1", "pick('1')", priority=True, show=False),
        Binding("2", "pick('2')", priority=True, show=False),
        Binding("3", "pick('3')", priority=True, show=False),
        Binding("4", "pick('4')", priority=True, show=False),
        Binding("5", "pick('5')", priority=True, show=False),
        Binding("6", "pick('6')", priority=True, show=False),
        Binding("7", "pick('7')", priority=True, show=False),
        Binding("8", "pick('8')", priority=True, show=False),
        Binding("9", "pick('9')", priority=True, show=False),
        Binding("0", "pick('0')", priority=True, show=False),
    ]

    def __init__(self, db, **kwargs) -> None:
        super().__init__(**kwargs)
        self.db = db

    def compose(self):
        yield AppHeader()
        with ScrollableContainer(id="menu-scroll"):
            yield LauncherBanner(id="launcher-banner")
            with Vertical(id="menu-body"):
                yield LauncherStatus(id="launcher-status")
                yield LauncherMenu(id="launcher-menu")
                yield Static(
                    Text.from_ansi(
                        f"{C_MUTED}↑/↓ move  ·  Enter select  ·  1-9 jump  ·  q quit{C_RESET}"
                    ),
                    id="menu-help",
                )
        yield AppFooter()

    def on_mount(self) -> None:
        self.call_after_refresh(self._focus_menu)

    def _focus_menu(self) -> None:
        try:
            self.query_one(LauncherMenu).focus()
        except Exception:
            pass

    # --- action dispatch (contract preserved) ---
    def _run_action(self, action: str) -> None:
        if is_read_only_mode():
            if action in ("dashboard", "tradelog", "incidents"):
                self.app.switch_screen(action)
            elif action == "exit":
                os.environ["XAUBY_MENU_ACTION"] = "exit"
                self.app.exit()
            else:
                self.notify("Action disabled in read-only tenant attach", severity="warning")
            return
        if action in ("dashboard", "backtest"):
            self.app.switch_screen(action)
            return
        if action in ("config", "system_check", "db_tools"):
            self.app.push_screen(
                {"config": "quick_config"}.get(action, action))
            return
        os.environ["XAUBY_MENU_ACTION"] = action
        self.app.exit()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list is self.query_one(LauncherMenu) and event.option.id:
            self._run_action(event.option.id)

    def action_pick(self, digit: str) -> None:
        options, _ = _active_menu()
        action = {num: item_action for num, _, item_action in options}.get(digit)
        if action:
            self._run_action(action)

    def action_show_dashboard(self) -> None:
        self.app.switch_screen("dashboard")

    def action_show_tradelog(self) -> None:
        self.app.switch_screen("tradelog")

    def action_show_incidents(self) -> None:
        self.app.switch_screen("incidents")

    def action_quit_app(self) -> None:
        os.environ["XAUBY_MENU_ACTION"] = "exit"
        self.app.exit()
