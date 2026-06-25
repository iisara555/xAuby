"""Native Textual System Check and DB Tools screens."""

from __future__ import annotations

from rich.text import Text
from textual.containers import ScrollableContainer, Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from xauby.ui.textual_tui.widgets import AppHeader, AppFooter
from xauby.ui.textual_tui.quick_config.modals import ChoiceModal, ConfirmModal
from xauby.ui.textual_tui.quick_config.screens import _QCBaseScreen
from xauby.utils.colors import C_BOLD, C_GEMINI_BLUE, C_GEMINI_CYAN, C_MUTED, C_RESET


class SystemCheckScreen(_QCBaseScreen):
    """Read-only configuration/health report (run in a worker)."""

    def compose(self):
        yield AppHeader()
        with ScrollableContainer(id="qc-scroll"):
            with Vertical(id="qc-body"):
                yield Static(
                    Text.from_ansi(f"{C_BOLD}{C_GEMINI_CYAN}SYSTEM CONFIGURATION CHECK{C_RESET}"),
                    id="qc-title")
                yield Static(Text.from_ansi(f"{C_MUTED}Running checks…{C_RESET}"),
                             id="syscheck-output")
        yield AppFooter()

    def on_mount(self) -> None:
        self.run_worker(self._load, thread=True)

    def _load(self) -> None:
        from xauby.launcher.maintenance import collect_config_report

        report = collect_config_report()
        self.app.call_from_thread(
            self.query_one("#syscheck-output", Static).update, Text.from_ansi(report))


class DBToolsScreen(_QCBaseScreen):
    """Backup / restore / prune+vacuum the SQLite DB, with in-app feedback."""

    _ACTIONS = [
        ("backup", "Backup active database"),
        ("restore", "Restore from backup"),
        ("vacuum", "Prune old candles & VACUUM"),
    ]

    def compose(self):
        yield AppHeader()
        with ScrollableContainer(id="qc-scroll"):
            with Vertical(id="qc-body"):
                yield Static(id="qc-title")
                yield OptionList(id="qc-list", classes="qc-list")
                yield Static("", id="dbtools-output")
        yield AppFooter()

    def on_mount(self) -> None:
        ol = self.query_one("#qc-list", OptionList)
        for aid, label in self._ACTIONS:
            ol.add_option(Option(Text.from_ansi(f"{C_GEMINI_BLUE}▸{C_RESET}  {C_BOLD}{label}{C_RESET}"), id=aid))
        ol.highlighted = 0
        self._refresh_title()
        self.call_after_refresh(lambda: ol.focus())

    def _refresh_title(self) -> None:
        from xauby.launcher.maintenance import db_size_mb

        size = db_size_mb()
        size_str = f"{size:.2f} MB" if size is not None else "NOT FOUND"
        self.query_one("#qc-title", Static).update(
            Text.from_ansi(f"{C_BOLD}{C_GEMINI_CYAN}DATABASE TOOLS{C_RESET}  "
                           f"{C_MUTED}· active DB: {size_str}{C_RESET}"))

    def _output(self, message: str, ok: bool = True) -> None:
        color = C_GEMINI_CYAN if ok else "\033[91m"
        self.query_one("#dbtools-output", Static).update(Text.from_ansi(f"{color}{message}{C_RESET}"))
        self._refresh_title()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id == "backup":
            self._run(lambda: __import__("xauby.launcher.maintenance",
                                         fromlist=["do_backup"]).do_backup())
        elif event.option.id == "restore":
            self._restore_flow()
        elif event.option.id == "vacuum":
            self._vacuum_flow()

    def _run(self, fn) -> None:
        def work():
            ok, msg = fn()
            self.app.call_from_thread(self._output, msg, ok)
            self.app.call_from_thread(self.notify, msg,
                                      severity="information" if ok else "error")

        self.run_worker(work, thread=True)

    def _restore_flow(self) -> None:
        from xauby.launcher.maintenance import list_backups, do_restore

        backups = list_backups()
        if not backups:
            self._output("No backups found in backups/.", ok=False)
            return
        choices = [(name, f"{name}  ({size:.2f} MB)") for name, size in backups]

        def picked(name) -> None:
            if not name:
                return

            def confirmed(yes) -> None:
                if yes:
                    self._run(lambda n=name: do_restore(n))

            self.app.push_screen(
                ConfirmModal(f"Overwrite the ACTIVE database with {name}?",
                             confirm_label="Restore", cancel_label="Cancel"),
                confirmed)

        self.app.push_screen(ChoiceModal("Select a backup to restore", choices), picked)

    def _vacuum_flow(self) -> None:
        from xauby.launcher.maintenance import do_db_maintenance

        def confirmed(yes) -> None:
            if yes:
                self._run(do_db_maintenance)

        self.app.push_screen(
            ConfirmModal("Prune old candles and VACUUM the database now?",
                         confirm_label="Run", cancel_label="Cancel"),
            confirmed)
