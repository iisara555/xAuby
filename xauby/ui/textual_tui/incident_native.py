"""Native Textual widgets for the Incident Explorer screen."""

from __future__ import annotations

from typing import Any, List

from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Label, ListItem, ListView, Static
from textual.widget import Widget

from xauby.storage.interface import IDatabaseRepository
from xauby.ui.incident_presenter import (
    build_incident_view_model,
    format_run_label,
    format_timeline_display_lines,
    get_explorer_state,
)
from xauby.utils.colors import C_MUTED, C_RESET
from xauby.ui.textual_tui.layout import is_phone_layout, is_stacked_layout
from xauby.ui.textual_tui.state_sync import fallback_bot_state, load_bot_state_from_disk


class _Section(Static):
    """A titled section panel; ANSI via Rich, never Rich markup brackets."""

    def __init__(self, title: str, **kwargs):
        super().__init__("", markup=False, **kwargs)
        if title:
            self.border_title = title

    def update_plain(self, text: str) -> None:
        self.update(text or "")

    def update_ansi(self, lines: list[str]) -> None:
        from rich.text import Text

        text = Text.from_ansi("\n".join(lines)) if lines else Text("")
        self.update(text)


class _EngineRunsPanel(Widget):
    """Single bordered list of runs — avoids per-line Static/Rich fragmentation."""

    def compose(self):
        yield ListView(id="inc-runs-list")

    def on_mount(self) -> None:
        self.border_title = "ENGINE RUNS"

    def sync_runs(self, vm: Any, width: int) -> None:
        lv = self.query_one("#inc-runs-list", ListView)
        lv.clear()
        if not vm.runs:
            lv.append(ListItem(Label("No engine runs recorded yet.", markup=False)))
            return

        phone = width < 75
        compact = width < 110
        live_id = str(vm.live_run_id or "")
        for i, row in enumerate(vm.runs):
            rid = str(row.get("run_id", "?"))
            text = format_run_label(
                row,
                selected=i == vm.state.selected_idx,
                is_live_run=rid == live_id,
                narrow=phone or compact,
            )
            lv.append(ListItem(Label(text, markup=False)))

        idx = vm.state.selected_idx
        if 0 <= idx < len(vm.runs):
            lv.index = idx


class IncidentNativeBody(Widget):
    """Incident Explorer built from native Textual widgets."""

    state = reactive({})
    focus_symbol = reactive("")
    _last_refresh_key: tuple = ()

    def __init__(self, db: IDatabaseRepository, **kwargs):
        super().__init__(**kwargs)
        self.db = db
        self._refreshing = False
        self._needs_refresh = False
        self._force_next = False

    def compose(self):
        with Horizontal(id="incident-split"):
            with Vertical(id="incident-left-col"):
                yield _EngineRunsPanel(id="inc-runs-panel")
            with Vertical(id="incident-right-col"):
                yield _Section("SUMMARY", id="inc-summary")
                yield _Section("VALIDATION", id="inc-validation")
                yield _Section("TIMELINE", id="inc-timeline")

    def on_mount(self) -> None:
        self._sync_layout(self.size.width)
        self.run_worker(self._refresh())

    def on_resize(self, event) -> None:
        self._sync_layout(event.size.width)

    def _sync_layout(self, width: int) -> None:
        split = self.query_one("#incident-split")
        if is_stacked_layout(width):
            split.add_class("stacked")
        else:
            split.remove_class("stacked")
        if is_phone_layout(width):
            split.add_class("phone-layout")
        else:
            split.remove_class("phone-layout")

    def watch_state(self, _state: dict) -> None:
        self.run_worker(self._refresh())

    def watch_focus_symbol(self, _sym: str) -> None:
        self.run_worker(self._refresh())

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "inc-runs-list":
            return
        st = get_explorer_state()
        if event.index is None or event.index == st.selected_idx:
            return
        st.selected_idx = int(event.index)
        st.scroll = 0
        self.run_worker(self._refresh(force=True))

    def _effective_state(self) -> dict:
        if self.state:
            return self.state
        state, _, focus, _ = load_bot_state_from_disk()
        if state:
            if focus and not self.focus_symbol:
                self.focus_symbol = focus
            return state
        return fallback_bot_state(focus_symbol=str(self.focus_symbol or ""))

    async def _refresh(self, *, force: bool = False) -> None:
        if getattr(self, "_refreshing", False):
            self._needs_refresh = True
            if force:
                self._force_next = True
            return

        self._refreshing = True
        self._needs_refresh = False
        eff_force = force or getattr(self, "_force_next", False)
        self._force_next = False

        try:
            state = self._effective_state()
            if focus := str(self.focus_symbol or ""):
                state = {**state, "symbol": focus.upper().replace("_", "")}
            w = max(38, self.size.width)
            h = max(28, self.size.height)
            st = get_explorer_state()
            key = (
                w,
                h,
                st.selected_idx,
                st.timeline_filter,
                st.scroll,
                st.selected_run_id(),
            )
            if not eff_force and key == self._last_refresh_key:
                return
            self._last_refresh_key = key

            import asyncio
            try:
                vm = await asyncio.to_thread(
                    build_incident_view_model,
                    self.db,
                    state,
                    w,
                    h,
                    st,
                    focus_symbol=str(self.focus_symbol or ""),
                    is_multi=bool(state.get("multi_pair")),
                )
            except Exception as exc:
                self._set_error(str(exc))
                return

            self._update_summary(vm, w)
            self._update_runs(vm, w)
            self._update_validation(vm, w)
            self._update_timeline(vm, w, h)
        finally:
            self._refreshing = False
            if getattr(self, "_needs_refresh", False):
                self.run_worker(self._refresh(force=getattr(self, "_force_next", False)))

    def _set_error(self, msg: str) -> None:
        err = f"Error: {msg}"
        for node_id in ("inc-summary", "inc-validation", "inc-timeline"):
            try:
                self.query_one(f"#{node_id}", _Section).update_plain(err)
            except Exception:
                pass
        try:
            lv = self.query_one("#inc-runs-list", ListView)
            lv.clear()
            lv.append(ListItem(Label(err, markup=False)))
        except Exception:
            pass

    def _update_runs(self, vm: Any, width: int) -> None:
        self.query_one("#inc-runs-panel", _EngineRunsPanel).sync_runs(vm, width)

    def _update_summary(self, vm: Any, width: int) -> None:
        node = self.query_one("#inc-summary", _Section)
        lines: List[str] = []
        if vm.header_lines:
            lines.extend(vm.header_lines)
        if vm.summary_lines:
            if lines:
                lines.append("")
            lines.extend(vm.summary_lines)
        if vm.help_line:
            lines.append(f"{C_MUTED}{vm.help_line}{C_RESET}")
        if not lines:
            node.update_plain("No data — press r to refresh.")
            return
        node.update_ansi(lines)

    def _update_validation(self, vm: Any, width: int) -> None:
        node = self.query_one("#inc-validation", _Section)
        if not vm.validation_lines:
            node.update_plain("Press 'v' to run replay validation.")
            return
        node.update_ansi(vm.validation_lines)

    def _update_timeline(self, vm: Any, width: int, height: int) -> None:
        node = self.query_one("#inc-timeline", _Section)
        filt = getattr(vm, "filter_status_line", "") or ""
        node.border_title = "TIMELINE"
        if filt:
            short = filt if len(filt) <= 42 else filt[:39] + "..."
            node.border_title = f"TIMELINE · {short}"

        if not vm.timeline_events:
            if vm.timeline_total_count and vm.timeline_filtered_count == 0:
                msg = (
                    f"{filt}\n"
                    "No events match this filter. Press 'a' to cycle "
                    "(Notable → Trade → All)."
                )
            else:
                msg = "No events in selected run."
            node.update_plain(msg)
            return
        max_rows = max(4, height - 24)
        lines, footer = format_timeline_display_lines(
            vm.timeline_events,
            width,
            vm.scroll,
            max_rows,
            is_mobile=is_phone_layout(width),
        )
        out: List[str] = []
        if filt:
            out.append(filt)
        out.extend(lines)
        if footer:
            out.append(footer)
        node.update_plain("\n".join(out) if out else "No events.")
