"""Hub + generic submenu screens for native Textual Quick Config."""

from __future__ import annotations

import os
from typing import Any, Dict

from rich.text import Text
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from textual.screen import Screen

from xauby.ui.menu import check_engine_status
from xauby.ui.textual_tui.widgets import AppHeader, AppFooter
from xauby.ui.textual_tui.quick_config.modals import (
    ChoiceModal,
    ConfirmModal,
    NumberModal,
    TextModal,
)
from xauby.ui.textual_tui.quick_config.schema import (
    Ctx,
    Field,
    HUB_GROUPS,
    SUBMENUS,
    build_ctx,
)
from xauby.utils.colors import (
    C_BOLD,
    C_GEMINI_BLUE,
    C_GEMINI_CYAN,
    C_GREEN,
    C_MUTED,
    C_PRIMARY,
    C_RED,
    C_RESET,
)


def _field_prompt(field: Field, value: Any, ctx: Ctx, disabled: bool) -> Text:
    disp = field.display(value, ctx)
    if disabled:
        return Text.from_ansi(f"{C_MUTED}▸  {field.label}: {disp}  (inactive){C_RESET}")
    if field.kind == "toggle":
        if field.color == "live":
            vcol = C_RED if value else C_GREEN
        else:
            vcol = C_GREEN if value else C_MUTED
    elif field.color == "action" or field.kind in ("choice", "action"):
        vcol = C_GEMINI_CYAN
    elif field.color == "live":
        vcol = C_RED
    else:
        vcol = C_PRIMARY
    return Text.from_ansi(
        f"{C_GEMINI_BLUE}▸{C_RESET}  {C_BOLD}{field.label}{C_RESET}: {vcol}{disp}{C_RESET}"
    )


class _QCBaseScreen(Screen):
    """Shared base for the OptionList config screens.

    Inherits plain ``Screen`` (not ``BaseTUIScreen``) so the focused ``OptionList``
    owns arrow/Enter navigation natively — ``BaseTUIScreen``'s pair-cycling key
    handling otherwise swallows the arrow keys.
    """

    BINDINGS = [
        Binding("escape", "back", "Back", show=False),
        Binding("0", "back", "Back", show=False),
        Binding("q", "quit_app", "Quit", show=False),
        Binding("ctrl+m", "to_menu", "Menu", show=False),
    ]

    def __init__(self, db, **kwargs) -> None:
        super().__init__(**kwargs)
        self.db = db

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_to_menu(self) -> None:
        self.app.switch_screen("menu")

    def action_quit_app(self) -> None:
        os.environ["XAUBY_MENU_ACTION"] = "exit"
        self.app.exit()


class QuickConfigScreen(_QCBaseScreen):
    """Generic submenu screen rendered from a schema."""

    def __init__(self, db, submenu_id: str, **kwargs) -> None:
        super().__init__(db, **kwargs)
        self.submenu_id = submenu_id
        self._fields: Dict[str, Field] = {}

    def compose(self):
        yield AppHeader()
        with ScrollableContainer(id="qc-scroll"):
            with Vertical(id="qc-body"):
                yield Static(id="qc-title")
                yield OptionList(id="qc-list", classes="qc-list")
        yield AppFooter()

    def on_mount(self) -> None:
        self._build_rows()
        self.call_after_refresh(self._focus_list)

    def on_screen_resume(self) -> None:
        self._build_rows()

    def _focus_list(self) -> None:
        try:
            self.query_one("#qc-list", OptionList).focus()
        except Exception:
            pass

    def _build_rows(self) -> None:
        sub = SUBMENUS[self.submenu_id]
        ctx = build_ctx()
        self.query_one("#qc-title", Static).update(
            Text.from_ansi(f"{C_GEMINI_CYAN}Quick Config ›{C_RESET} {C_BOLD}{sub.title}{C_RESET}")
        )
        ol = self.query_one("#qc-list", OptionList)
        highlighted = ol.highlighted
        ol.clear_options()
        self._fields = {}
        first_enabled = None
        for idx, f in enumerate(sub.fields(ctx)):
            disabled = not f.enabled(ctx)
            if not disabled and first_enabled is None:
                first_enabled = idx
            ol.add_option(Option(_field_prompt(f, f.get(ctx), ctx, disabled),
                                  id=f.key, disabled=disabled))
            self._fields[f.key] = f
        if highlighted is not None and highlighted < ol.option_count:
            ol.highlighted = highlighted
        elif first_enabled is not None:
            ol.highlighted = first_enabled

    # ── dispatch ──────────────────────────────────────────────────────────
    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        field = self._fields.get(event.option.id or "")
        if field is None:
            return
        if field.kind == "toggle":
            self._toggle(field)
        elif field.kind == "number":
            self._edit_number(field)
        elif field.kind == "choice":
            self._edit_choice(field)
        elif field.kind == "text":
            self._edit_text(field)
        elif field.kind == "action" and field.run is not None:
            field.run(self, build_ctx())

    def _edit_text(self, field: Field) -> None:
        ctx = build_ctx()

        def done(value) -> None:
            if value is None or field.set is None:
                return
            self._after_write(bool(field.set(value, build_ctx())), field)

        self.app.push_screen(
            TextModal(field.label, field.display(field.get(ctx), ctx), secret=field.secret),
            done,
        )

    def _toggle(self, field: Field) -> None:
        ctx = build_ctx()
        ok = bool(field.set(not bool(field.get(ctx)), ctx)) if field.set else False
        self._after_write(ok, field)

    def _edit_number(self, field: Field) -> None:
        ctx = build_ctx()

        def done(value) -> None:
            if value is None or field.set is None:
                return
            ctx2 = build_ctx()
            if field.validate is not None:
                err = field.validate(value, ctx2)
                if err:
                    self.notify(err, severity="error")
                    return
            self._after_write(bool(field.set(value, ctx2)), field)

        self.app.push_screen(
            NumberModal(field.label, field.get(ctx), minimum=field.minimum,
                        maximum=field.maximum, is_int=field.is_int, unit=field.unit),
            done,
        )

    def _edit_choice(self, field: Field) -> None:
        ctx = build_ctx()
        choices = field.choices(ctx) if field.choices else []

        def done(value) -> None:
            if value is None or field.set is None:
                return
            self._after_write(bool(field.set(value, build_ctx())), field)

        self.app.push_screen(ChoiceModal(field.label, choices, field.get(ctx)), done)

    def _after_write(self, ok: bool, field: Field) -> None:
        if not ok:
            self.notify("Update failed", severity="error")
            return
        self.notify(f"{field.label} updated")
        self._build_rows()
        if field.needs_restart:
            self._maybe_offer_restart()

    def _maybe_offer_restart(self) -> None:
        try:
            eng, _ = check_engine_status()
        except Exception:
            eng = "OFFLINE"
        if eng != "RUNNING":
            return

        def done(yes) -> None:
            if yes:
                os.environ["XAUBY_MENU_ACTION"] = "restart_service"
                self.app.exit()

        self.app.push_screen(
            ConfirmModal("Change saved. Restart bot engine now?",
                         confirm_label="Restart now", cancel_label="Later"),
            done,
        )


class QuickConfigHubScreen(_QCBaseScreen):
    """Category hub; mirrors the main menu's grouped OptionList look."""

    def compose(self):
        yield AppHeader()
        with ScrollableContainer(id="qc-scroll"):
            with Vertical(id="qc-body"):
                yield Static(
                    Text.from_ansi(f"{C_BOLD}{C_GEMINI_CYAN}QUICK CONFIGURATION{C_RESET}"),
                    id="qc-title",
                )
                yield OptionList(id="qc-hub-list", classes="qc-list")
        yield AppFooter()

    def on_mount(self) -> None:
        ol = self.query_one("#qc-hub-list", OptionList)
        groups = list(HUB_GROUPS)
        first_sid = None
        for gi, (group_name, items) in enumerate(groups):
            ol.add_option(Option(Text.from_ansi(f"{C_MUTED}{C_BOLD}{group_name}{C_RESET}"),
                                 disabled=True))
            for sid, label in items:
                available = sid in SUBMENUS
                if available:
                    prompt = Text.from_ansi(f"{C_GEMINI_BLUE}▸{C_RESET}  {C_BOLD}{label}{C_RESET}")
                    if first_sid is None:
                        first_sid = sid
                else:
                    prompt = Text.from_ansi(f"{C_MUTED}▸  {label}  (soon){C_RESET}")
                ol.add_option(Option(prompt, id=sid, disabled=not available))
            if gi != len(groups) - 1:
                ol.add_option(None)
        if first_sid is not None:
            try:
                ol.highlighted = ol.get_option_index(first_sid)
            except Exception:
                pass
        self.call_after_refresh(lambda: ol.focus())

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        sid = event.option.id
        if sid and sid in SUBMENUS:
            self.app.push_screen(QuickConfigScreen(self.db, sid))
