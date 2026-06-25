"""Native Textual widgets for the Trade Log screen (replaces RichScreenPanel)."""

from __future__ import annotations

from typing import List

from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import DataTable, Static
from textual.widget import Widget

from xauby.storage.interface import IDatabaseRepository
from xauby.ui.textual_tui.layout import is_phone_layout, is_stacked_layout
from xauby.ui.textual_tui.state_sync import fallback_bot_state, load_bot_state_from_disk, state_file_mtime
from xauby.ui.tradelog_presenter import TRADE_FILTERS, TradeLogViewModel, TradeTableRow, build_tradelog_view_model


class _Section(Static):
    """A titled section panel that renders ANSI lines correctly."""

    def __init__(self, title: str, **kwargs):
        super().__init__("", **kwargs)
        self.border_title = title
        self.styles.border = ("round", "#3f3f46")
        self.styles.padding = (0, 1)

    def update_ansi(self, lines: list[str]) -> None:
        from rich.text import Text
        text = Text.from_ansi("\n".join(lines)) if lines else Text("")
        self.update(text)


class TradeDataTable(DataTable):
    """DataTable for trade logs with border title and cursor."""

    def __init__(self, **kwargs):
        super().__init__(
            show_header=True,
            show_cursor=True,
            show_row_labels=False,
            cursor_type="row",
            **kwargs,
        )
        self.styles.border = ("solid", "#27272a")
        self.styles.height = "1fr"
        self.border_title = "TRADE LOGS"
        self._phone_mode = False

    def on_mount(self) -> None:
        self._set_columns()

    def set_phone_mode(self, phone: bool) -> None:
        if phone == self._phone_mode:
            return
        self._phone_mode = phone
        self.clear(columns=True)
        self._set_columns()

    def _set_columns(self) -> None:
        if self._phone_mode:
            self.add_columns("Date", "S", "PnL")
        else:
            self.add_columns("Date", "Side", "Qty", "Entry->Exit", "PnL", "Why")


class TradeLogNativeBody(Widget):
    """Trade Log screen built from native Textual widgets."""

    state = reactive({})
    focus_symbol = reactive("")
    _last_refresh_key: tuple = ()
    _sort_column: int = 4
    _sort_reverse: bool = True
    _cached_rows: List[TradeTableRow] = []
    _scope_all: bool = False
    _trade_filter_index: int = 0

    BINDINGS = [
        Binding("a", "toggle_scope", "All pairs", show=False),
        Binding("f", "cycle_trade_filter", "Filter", show=False),
        Binding("alt+1", "sort_col_0", "Sort Date", show=False),
        Binding("alt+2", "sort_col_1", "Sort Side", show=False),
        Binding("alt+3", "sort_col_2", "Sort Size", show=False),
        Binding("alt+4", "sort_col_3", "Sort Prices", show=False),
        Binding("alt+5", "sort_col_4", "Sort PnL", show=False),
        Binding("alt+6", "sort_col_5", "Sort Trigger", show=False),
        Binding("r", "sort_reverse", "Reverse", show=False),
    ]

    def __init__(self, db: IDatabaseRepository, **kwargs):
        super().__init__(**kwargs)
        self.db = db
        self._refreshing = False
        self._needs_refresh = False
        self._force_next = False

    def compose(self):
        with Horizontal(id="tradelog-split"):
            with Vertical(id="tradelog-left-col"):
                yield _Section("MARKET REGIME", id="tl-regime")
                yield _Section("PERFORMANCE", id="tl-perf")
                yield _Section("PEAK / CURVE", id="tl-peak")
            with Vertical(id="tradelog-right-col"):
                yield _Section("RECENT TRADES", id="tl-cards")
                yield TradeDataTable(id="tl-datatable")

    def on_mount(self) -> None:
        self._sync_layout(self.size.width)
        self.run_worker(self._refresh())

    def on_resize(self, event) -> None:
        self._sync_layout(event.size.width)

    def _sync_layout(self, width: int) -> None:
        split = self.query_one("#tradelog-split")
        if is_stacked_layout(width):
            split.add_class("stacked")
        else:
            split.remove_class("stacked")
        if is_phone_layout(width):
            split.add_class("phone-layout")
        else:
            split.remove_class("phone-layout")
        try:
            self.query_one("#tl-cards").display = is_phone_layout(width)
            self.query_one("#tl-datatable").display = not is_phone_layout(width)
        except Exception:
            pass

    def watch_state(self, _state: dict) -> None:
        self.run_worker(self._refresh())

    def watch_focus_symbol(self, _sym: str) -> None:
        self.run_worker(self._refresh())

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
        if self.size.width < 20:
            return
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
            # Use a large height (1000) for build_tradelog_view_model so that
            # scrollable TUI panels are not pruned based on short terminal screen heights.
            h = 1000
            filter_mode = TRADE_FILTERS[self._trade_filter_index % len(TRADE_FILTERS)]
            key = (
                w,
                h,
                state.get("symbol"),
                bool(state.get("simulate_only")),
                bool(self._scope_all),
                filter_mode,
                state_file_mtime(),
            )
            if not eff_force and key == self._last_refresh_key:
                return
            self._last_refresh_key = key

            import asyncio
            try:
                vm = await asyncio.to_thread(
                    build_tradelog_view_model,
                    self.db,
                    state,
                    w,
                    h,
                    focus_symbol="ALL" if self._scope_all else state.get("symbol"),
                    is_multi=self._scope_all,
                    trade_filter=filter_mode,
                )
            except Exception as exc:
                self._set_error(str(exc))
                return

            self._cached_rows = list(vm.table_rows)
            phone = w < 75
            self._update_regime(vm)
            if phone:
                self._update_perf_compact(vm, w)
            else:
                self._update_perf(vm, w)
            self._update_peak(vm, w)
            self._update_trade_cards(vm)
            self._update_table(vm, w)
        finally:
            self._refreshing = False
            if getattr(self, "_needs_refresh", False):
                self.run_worker(self._refresh(force=getattr(self, "_force_next", False)))



    def _set_error(self, msg: str) -> None:
        for node_id in ("tl-regime", "tl-perf", "tl-peak", "tl-cards", "tl-datatable"):
            try:
                self.query_one(f"#{node_id}").update(f"[red]Error: {msg}[/]")
            except Exception:
                pass

    def _update_regime(self, vm: TradeLogViewModel) -> None:
        node = self.query_one("#tl-regime", _Section)
        lines: List[str] = []
        lines.extend(vm.regime_lines)
        if vm.analytics_lines:
            lines.append("")
            lines.extend(vm.analytics_lines)
        if vm.track_lines:
            lines.append("")
            lines.extend(vm.track_lines)
        if not lines:
            fp = ("",)
            if getattr(self, "_last_regime_fp", None) == fp:
                return
            self._last_regime_fp = fp
            node.update("[dim]No regime data.[/]")
            return

        fp = tuple(lines)
        if getattr(self, "_last_regime_fp", None) == fp:
            return
        self._last_regime_fp = fp
        node.update_ansi(lines)

    def _update_perf(self, vm: TradeLogViewModel, width: int) -> None:
        node = self.query_one("#tl-perf", _Section)
        if not vm.performance_lines:
            fp = ("",)
            if getattr(self, "_last_perf_fp", None) == fp:
                return
            self._last_perf_fp = fp
            from xauby.ui.copy import format_monitoring_empty
            node.update_ansi([format_monitoring_empty(compact=False, with_subline=True)])
            return

        fp = tuple(vm.performance_lines)
        if getattr(self, "_last_perf_fp", None) == fp:
            return
        self._last_perf_fp = fp
        node.update_ansi(vm.performance_lines)

    def _update_perf_compact(self, vm: TradeLogViewModel, width: int) -> None:
        """Phone-mode: show only a tight performance summary."""
        node = self.query_one("#tl-perf", _Section)
        stats = vm.stats
        if not stats:
            fp = ("",)
            if getattr(self, "_last_perf_fp", None) == fp:
                return
            self._last_perf_fp = fp
            from xauby.ui.copy import format_monitoring_empty
            node.update_ansi([format_monitoring_empty(compact=True, with_subline=False)])
            return
        net_pnl = stats.get("net_pnl", 0)
        pnl_color = "\033[92m" if net_pnl >= 0 else "\033[91m"
        thb_str = ""
        try:
            from xauby.utils.currency import usdt_to_thb, format_thb
            if net_pnl != 0:
                thb_str = f" \033[2m{format_thb(usdt_to_thb(net_pnl))}\033[0m"
        except Exception:
            pass
        lines = [
            f"Trades: {stats.get('wins', 0)}W/{stats.get('losses', 0)}L  WinRate: {stats.get('win_rate', 0):.1f}%",
            f"Net PnL: {pnl_color}{net_pnl:+.4f} USDT\033[0m{thb_str}  PF: {stats.get('profit_factor_str', '-')}",
        ]
        fp = tuple(lines)
        if getattr(self, "_last_perf_fp", None) == fp:
            return
        self._last_perf_fp = fp
        node.update_ansi(lines)

    def _update_peak(self, vm: TradeLogViewModel, width: int) -> None:
        node = self.query_one("#tl-peak", _Section)
        lines: List[str] = []
        if vm.peak_lines:
            lines.extend(vm.peak_lines)
        if vm.curve_lines:
            lines.append("")
            lines.extend(vm.curve_lines)
        if not lines:
            fp = ("",)
            if getattr(self, "_last_peak_fp", None) == fp:
                return
            self._last_peak_fp = fp
            node.update("[dim]No peak/curve data.[/]")
            return

        fp = tuple(lines)
        if getattr(self, "_last_peak_fp", None) == fp:
            return
        self._last_peak_fp = fp
        node.update_ansi(lines)

    def _update_trade_cards(self, vm: TradeLogViewModel) -> None:
        node = self.query_one("#tl-cards", _Section)
        node.border_title = f"RECENT POSITIONS - {vm.scope_label} / {vm.filter_label}"
        lines = list(vm.trade_card_lines or [])
        if not lines:
            lines = ["\033[90mNo closed trades yet.\033[0m"]
        fp = tuple(lines)
        if getattr(self, "_last_cards_fp", None) == fp:
            return
        self._last_cards_fp = fp
        node.update_ansi(lines)

    def _update_table(self, vm: TradeLogViewModel, width: int) -> None:
        table = self.query_one("#tl-datatable", TradeDataTable)
        table.border_title = f"TRADE LOGS - {vm.scope_label} / {vm.filter_label}"
        phone = width < 75
        
        rows = self._sorted_rows(vm.table_rows) if vm.table_rows else []
        current_fp = (
            phone,
            [
                (
                    r.date_ict,
                    r.side,
                    r.mode,
                    r.size,
                    r.prices,
                    r.pnl_text,
                    r.trigger,
                    r.strategy,
                    r.duration,
                )
                for r in rows
            ],
            self._sort_column,
            self._sort_reverse,
        )
        if getattr(self, "_last_table_fp", None) == current_fp:
            return
        self._last_table_fp = current_fp

        table.set_phone_mode(phone)
        table.clear()
        if not rows:
            from xauby.ui.copy import MONITORING_LABEL, HISTORY_EMPTY
            empty_label = f"{MONITORING_LABEL} · {HISTORY_EMPTY}"
            if phone:
                table.add_row(empty_label, "", "")
            else:
                table.add_row(empty_label, "", "", "", "", "")
            return

        for row in rows:
            style = "green" if row.pnl_value >= 0 else "red"
            if phone:
                table.add_row(
                    row.date_ict,
                    row.side[:1],
                    f"[{style}]{row.pnl_text}[/{style}]",
                )
            else:
                table.add_row(
                    row.date_ict,
                    f"{row.side} {row.mode}".strip(),
                    row.size,
                    row.prices,
                    f"[{style}]{row.pnl_text}[/{style}]",
                    row.trigger,
                )

    def _sorted_rows(self, rows: List[TradeTableRow]) -> List[TradeTableRow]:
        col = self._sort_column
        reverse = self._sort_reverse
        getters = [
            lambda r: r.date_ict,
            lambda r: r.side,
            lambda r: float(r.size or 0),
            lambda r: r.prices,
            lambda r: r.pnl_value,
            lambda r: r.trigger,
        ]
        if 0 <= col < len(getters):
            try:
                return sorted(rows, key=getters[col], reverse=reverse)
            except Exception:
                pass
        return rows

    # ── Sort actions ──
    def action_sort_col_0(self) -> None:
        self._apply_sort(0)

    def action_sort_col_1(self) -> None:
        self._apply_sort(1)

    def action_sort_col_2(self) -> None:
        self._apply_sort(2)

    def action_sort_col_3(self) -> None:
        self._apply_sort(3)

    def action_sort_col_4(self) -> None:
        self._apply_sort(4)

    def action_sort_col_5(self) -> None:
        self._apply_sort(5)

    def action_sort_reverse(self) -> None:
        self._sort_reverse = not self._sort_reverse
        self._rebuild_table()

    def action_toggle_scope(self) -> None:
        self._scope_all = not self._scope_all
        self._last_refresh_key = ()
        self.run_worker(self._refresh(force=True))

    def action_cycle_trade_filter(self) -> None:
        self._trade_filter_index = (self._trade_filter_index + 1) % len(TRADE_FILTERS)
        self._last_refresh_key = ()
        self.run_worker(self._refresh(force=True))

    def _apply_sort(self, col: int) -> None:
        if self._sort_column == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = col
            self._sort_reverse = False
        self._rebuild_table()

    def _rebuild_table(self) -> None:
        if not self._cached_rows:
            return
        table = self.query_one("#tl-datatable", TradeDataTable)
        phone = self.size.width < 75
        rows = self._sorted_rows(self._cached_rows)
        
        current_fp = (
            phone,
            [
                (
                    r.date_ict,
                    r.side,
                    r.mode,
                    r.size,
                    r.prices,
                    r.pnl_text,
                    r.trigger,
                    r.strategy,
                    r.duration,
                )
                for r in rows
            ],
            self._sort_column,
            self._sort_reverse,
        )
        self._last_table_fp = current_fp
        
        table.set_phone_mode(phone)
        table.clear()
        for row in rows:
            style = "green" if row.pnl_value >= 0 else "red"
            if phone:
                table.add_row(
                    row.date_ict,
                    row.side[:1],
                    f"[{style}]{row.pnl_text}[/{style}]",
                )
            else:
                table.add_row(
                    row.date_ict,
                    f"{row.side} {row.mode}".strip(),
                    row.size,
                    row.prices,
                    f"[{style}]{row.pnl_text}[/{style}]",
                    row.trigger,
                )
