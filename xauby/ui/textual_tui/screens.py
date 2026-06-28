import os

from textual.screen import Screen
from textual.binding import Binding
from textual.containers import Container, ScrollableContainer, Horizontal, Vertical

from xauby.database.db import LiteDB
from xauby.ui.incident_presenter import (
    get_explorer_state,
    handle_explorer_action,
    prepare_incident_screen,
)
from xauby.ui.state_view import cycle_focus, write_focus_request
from xauby.ui.textual_tui.layout import sync_dashboard_layout, FOCUS_PREV_KEYS, FOCUS_NEXT_KEYS
from xauby.ui.textual_tui.state_sync import (
    bot_state_fingerprint,
    chart_panel_fingerprint,
    checklist_panel_fingerprint,
    header_fingerprint,
    load_bot_state_from_disk,
    portfolio_panel_fingerprint,
    positions_panel_fingerprint,
    regime_panel_fingerprint,
    state_file_mtime,
    tui_refresh_interval,
)
from xauby.ui.textual_tui.tradelog_native import TradeLogNativeBody
from xauby.ui.textual_tui.incident_native import IncidentNativeBody
from xauby.ui.textual_tui.backtest_native import BacktestNativeBody
from xauby.ui.textual_tui.quick_config.modals import ChoiceModal, ConfirmModal
from xauby.runtime.manual_orders import write_manual_order_request
from xauby.ui.textual_tui.widgets import (
    AppHeader, AppFooter,
    ANSIChartWidget, ChartLegendBar, StrategyChecklistPanel,
    PortfolioPanel, PositionsPanel, RegimePanelWidget,
)


# ──────────────────────────────────────────────────────────────
# Shared key bindings (split so Incidents does not steal ↑↓ / +/-)
# ──────────────────────────────────────────────────────────────

_NAV_BINDINGS = [
    Binding("d,1", "show_dashboard", "Dashboard", show=False),
    Binding("t,2", "show_tradelog", "Log", show=False),
    Binding("i,3", "show_incidents", "Incidents", show=False),
    Binding("b,4", "show_backtest", "Backtest", show=False),
    Binding("ctrl+m", "show_menu", "Menu", show=False),
    Binding("left", "prev_page", "Prev Page", show=False),
    Binding("right", "next_page", "Next Page", show=False),
    Binding("q", "quit_app", "Quit", show=False),
]

_PAIR_BINDINGS = [
    Binding("up", "focus_prev", "Prev pair", show=False),
    Binding("down", "focus_next", "Next pair", show=False),
    Binding(
        "left_square_bracket,0,comma,minus",
        "focus_prev",
        "Prev pair",
        show=False,
    ),
    Binding(
        "right_square_bracket,9,period,equals,plus",
        "focus_next",
        "Next pair",
        show=False,
    ),
]

# Pair switch on Incidents only via brackets (not ↑↓ or +/- — those scroll timeline)
_INCIDENT_PAIR_BINDINGS = [
    Binding("left_square_bracket,0,comma", "focus_prev", "Prev pair", show=False),
    Binding("right_square_bracket,9,period", "focus_next", "Next pair", show=False),
]

_INCIDENT_BINDINGS = [
    Binding("j,n", "incident_down", show=False),
    Binding("k,p", "incident_up", show=False),
    Binding("down,pagedown", "incident_scroll_down", show=False),
    Binding("up,pageup", "incident_scroll_up", show=False),
    Binding("a", "incident_toggle_filter", show=False),
    Binding("r", "incident_refresh", show=False),
    Binding("v", "incident_validate", show=False),
    Binding("plus,equals", "incident_scroll_up", show=False),
    Binding("minus,underscore", "incident_scroll_down", show=False),
]


# ──────────────────────────────────────────────────────────────
# Base Screen with shared state loading & navigation
# ──────────────────────────────────────────────────────────────

class BaseTUIScreen(Screen):
    """Base Screen class with app-level hotkey navigation and state management."""

    BINDINGS = list(_NAV_BINDINGS)
    
    def __init__(self, db: LiteDB, **kwargs):
        super().__init__(**kwargs)
        self.db = db
        from xauby.runtime.paths import bot_state_path
        self.state_file = os.path.join(os.getcwd(), bot_state_path())
        self._last_state = {}
        self._last_envelope = {}
        self._last_focus = ""
        self._last_pairs = []
        self._state_fp: tuple = ()
        self._header_fp: tuple = ()
        self._layout_width: int = 0
        self._last_state_mtime: float = 0.0
        
    def on_mount(self) -> None:
        self.set_interval(tui_refresh_interval(), self._tick)
        
    def _tick(self) -> None:
        """Periodic refresh: poll state every tick; update widgets when data changes."""
        app = getattr(self, "app", None)
        if app is not None:
            try:
                if app.screen is not self:
                    return
            except Exception:
                # Catch ScreenStackError or other setup/teardown issues in tests
                return
        self._load_state()
        self._last_state_mtime = state_file_mtime()

        bot_fp = bot_state_fingerprint(self._last_state, self._last_envelope, self._last_focus)
        if bot_fp != self._state_fp:
            self._state_fp = bot_fp
            self._header_fp = ()
            self._push_state()
            self._header_fp = header_fingerprint(self._last_state, self.size.width)
        else:
            self._sync_header(force=True)
            self._header_fp = header_fingerprint(self._last_state, self.size.width)
            self._sync_regime_countdown()
        
    def _load_state(self) -> None:
        """Read the bot state JSON file and resolve focus."""
        state, envelope, focus, pairs = load_bot_state_from_disk()
        if state:
            self._last_state = state
            self._last_envelope = envelope
            self._last_focus = focus
            self._last_pairs = pairs
            app = getattr(self, "app", None)
            if app is not None:
                app._xauby_state = state
                app._xauby_envelope = envelope
                app._xauby_focus = focus
                app._xauby_pairs = pairs
        else:
            self._last_state = {}
            self._last_envelope = {}
            self._last_focus = ""
            self._last_pairs = []
            app = getattr(self, "app", None)
            if app is not None:
                app._xauby_state = {}
                app._xauby_envelope = {}
                app._xauby_focus = ""
                app._xauby_pairs = []

    def _hydrate_from_app_cache(self) -> None:
        """Copy last-known state from the app when this screen was inactive."""
        app = getattr(self, "app", None)
        if app is None:
            return
        cached = getattr(app, "_xauby_state", None)
        if cached:
            self._last_state = app._xauby_state
            self._last_envelope = getattr(app, "_xauby_envelope", {})
            self._last_focus = getattr(app, "_xauby_focus", "")
            self._last_pairs = getattr(app, "_xauby_pairs", [])

    def on_screen_resume(self) -> None:
        self._hydrate_from_app_cache()
        if not self._last_state:
            self._load_state()
        self._last_state_mtime = state_file_mtime()
        self._state_fp = ()
        self._header_fp = ()
        self._push_state()

    def _sync_header(self, *, force: bool = False) -> None:
        try:
            header = self.query_one(AppHeader)
            header.sync_from_state(
                self._last_state,
                self._last_envelope,
                self.size.width,
                force=force,
            )
        except Exception:
            pass

    def _sync_regime_countdown(self) -> None:
        """Refresh regime panel when the candle countdown minute changes."""
        if self._get_view_name() != "dashboard" or not self._last_state:
            return
        w = max(38, self.size.width)
        regime_fp = regime_panel_fingerprint(self._last_state, w)
        stored = getattr(self, "_regime_fp", ())
        if regime_fp == stored:
            return
        self._regime_fp = regime_fp
        try:
            self.query_one(RegimePanelWidget).sync_from_state(self._last_state, w)
        except Exception:
            pass
            
    def _push_state(self) -> None:
        """Push loaded state to child widgets. Override in subclasses."""
        self._sync_header()
        try:
            footer = self.query_one(AppFooter)
            view = self._get_view_name()
            if footer.active_view != view:
                footer.active_view = view
        except Exception:
            pass
    
    def _get_view_name(self) -> str:
        return "dashboard"

    def _cycle_pair_focus(self, direction: int) -> None:
        if len(self._last_pairs) < 2:
            return
        new_focus = cycle_focus(self._last_pairs, self._last_focus, direction)
        write_focus_request(new_focus)
        self._load_state()
        self._state_fp = ()
        self._push_state()

    def action_show_dashboard(self) -> None:
        self.app.switch_screen("dashboard")

    def action_show_tradelog(self) -> None:
        self._load_state()
        self.app.switch_screen("tradelog")

    def action_show_incidents(self) -> None:
        self._load_state()
        prepare_incident_screen()
        self.app.switch_screen("incidents")

    def action_show_backtest(self) -> None:
        self._load_state()
        self.app.switch_screen("backtest")

    def action_show_menu(self) -> None:
        self.app.switch_screen("menu")

    def action_quit_app(self) -> None:
        os.environ["XAUBY_MENU_ACTION"] = "exit"
        self.app.exit()

    def action_prev_page(self) -> None:
        view = self._get_view_name()
        if view == "tradelog":
            self.app.switch_screen("dashboard")
        elif view == "incidents":
            self.app.switch_screen("tradelog")
        elif view == "backtest":
            self.app.switch_screen("incidents")

    def action_next_page(self) -> None:
        view = self._get_view_name()
        if view == "dashboard":
            self.app.switch_screen("tradelog")
        elif view == "tradelog":
            self.app.switch_screen("incidents")
        elif view == "incidents":
            self.app.switch_screen("backtest")

    def action_focus_prev(self) -> None:
        self._cycle_pair_focus(-1)

    def action_focus_next(self) -> None:
        self._cycle_pair_focus(1)
        
    def on_key(self, event) -> None:
        key = event.key.lower()
        view = self._get_view_name()
        if key == "left":
            self.action_prev_page()
            event.prevent_default()
            event.stop()
        elif key == "right":
            self.action_next_page()
            event.prevent_default()
            event.stop()
        elif key in FOCUS_PREV_KEYS or (key == "up" and view != "incidents"):
            self._cycle_pair_focus(-1)
            event.prevent_default()
            event.stop()
        elif key in FOCUS_NEXT_KEYS or (key == "down" and view != "incidents"):
            self._cycle_pair_focus(1)
            event.prevent_default()
            event.stop()
        elif key in ("ctrl+m", "enter") and view != "menu":
            self.action_show_menu()
            event.prevent_default()
            event.stop()

    def _scroll_container(self, container_id: str, direction: str) -> None:
        try:
            scroll = self.query_one(f"#{container_id}")
            if direction == "down":
                scroll.scroll_down(animate=False)
            elif direction == "up":
                scroll.scroll_up(animate=False)
            elif direction == "page_down":
                scroll.scroll_page_down(animate=False)
            elif direction == "page_up":
                scroll.scroll_page_up(animate=False)
        except Exception:
            pass

    def _handle_scroll_keys(self, event) -> bool:
        key = event.key.lower()
        if key in ("down", "j"):
            self._scroll_container(self._scroll_id, "down")
            event.prevent_default()
            return True
        if key in ("up", "k"):
            self._scroll_container(self._scroll_id, "up")
            event.prevent_default()
            return True
        if key == "pageup":
            self._scroll_container(self._scroll_id, "page_up")
            event.prevent_default()
            return True
        if key == "pagedown":
            self._scroll_container(self._scroll_id, "page_down")
            event.prevent_default()
            return True
        return False


# ──────────────────────────────────────────────────────────────
# DashboardScreen — Native widgets layout
# ──────────────────────────────────────────────────────────────

class DashboardScreen(BaseTUIScreen):
    """The Main Dashboard Screen using native Textual widgets."""

    BINDINGS = list(_NAV_BINDINGS) + list(_PAIR_BINDINGS) + [
        Binding("f7", "manual_buy", "Manual Buy", priority=True, show=False),
        Binding("f8", "manual_sell", "Manual Sell", priority=True, show=False),
    ]

    _scroll_id = "dashboard-scroll"
    _chart_fp: tuple = ()
    _checklist_fp: tuple = ()
    _portfolio_fp: tuple = ()
    _positions_fp: tuple = ()
    _regime_fp: tuple = ()
    
    def compose(self):
        yield AppHeader()
        with ScrollableContainer(id="dashboard-scroll"):
            with Horizontal(id="dashboard-content"):
                with Vertical(id="sidebar"):
                    yield PortfolioPanel(id="portfolio-panel", classes="panel-box")
                    yield PositionsPanel(id="positions-panel", classes="panel-box")
                    yield RegimePanelWidget(id="regime-panel", classes="panel-box")
                with Vertical(id="main-panel"):
                    with Vertical(id="chart-panel", classes="panel-box chart-panel"):
                        yield ANSIChartWidget(self.db, id="chart-ansi")
                        yield ChartLegendBar(id="chart-legend")
                    yield StrategyChecklistPanel(id="checklist-panel", classes="panel-box")
        yield AppFooter()

    def _sync_layout(self, width: int) -> None:
        if width == self._layout_width:
            return
        self._layout_width = width
        try:
            sync_dashboard_layout(self.query_one("#dashboard-content"), width)
        except Exception:
            pass
        self._chart_fp = ()
        self._checklist_fp = ()
        self._portfolio_fp = ()
        self._positions_fp = ()
        self._regime_fp = ()
        try:
            strat = str((self._last_state or {}).get("strategy_name") or "")
            self.query_one(ChartLegendBar).sync_width(width, strat or None)
        except Exception:
            pass
        self._sync_chart(force=True)

    def _sync_chart(self, *, force: bool = False) -> None:
        state = self._last_state
        if not state:
            return
        w = max(38, self.size.width)
        fp = chart_panel_fingerprint(state, w)
        if not force and fp == self._chart_fp:
            return
        self._chart_fp = fp
        try:
            self.run_worker(self.query_one(ANSIChartWidget).sync_from_state(state, w))
        except Exception:
            pass
        try:
            strat = str(state.get("strategy_name") or "")
            self.query_one(ChartLegendBar).sync_width(w, strat or None)
        except Exception:
            pass
    
    def on_screen_resume(self) -> None:
        self._chart_fp = ()
        self._checklist_fp = ()
        self._portfolio_fp = ()
        self._positions_fp = ()
        self._regime_fp = ()
        super().on_screen_resume()

    def on_mount(self) -> None:
        try:
            self.query_one("#portfolio-panel").border_title = "PORTFOLIO"
            self.query_one("#positions-panel").border_title = "POSITIONS"
            self.query_one("#regime-panel").border_title = "REGIME & EVENTS"
            self.query_one("#chart-panel").border_title = "CANDLESTICK CHART"
            self.query_one("#checklist-panel").border_title = "SIGNAL & CHECKLIST"
        except Exception:
            pass
        self._sync_layout(self.size.width)
        super().on_mount()
        
    def on_resize(self, event) -> None:
        self._sync_layout(event.size.width)
    
    def _get_view_name(self) -> str:
        return "dashboard"
    
    def _push_state(self) -> None:
        super()._push_state()
        state = self._last_state
        envelope = self._last_envelope
        if not state:
            return
        w = max(38, self.size.width)
        self._sync_chart()
        checklist_fp = checklist_panel_fingerprint(state, w)
        if checklist_fp != self._checklist_fp:
            self._checklist_fp = checklist_fp
            try:
                self.query_one(StrategyChecklistPanel).sync_from_state(state, w)
            except Exception:
                pass
        portfolio_fp = portfolio_panel_fingerprint(envelope, w)
        if portfolio_fp != self._portfolio_fp:
            self._portfolio_fp = portfolio_fp
            try:
                self.query_one(PortfolioPanel).sync_from_envelope(envelope, w)
            except Exception:
                pass
        positions_fp = positions_panel_fingerprint(envelope, w)
        if positions_fp != self._positions_fp:
            self._positions_fp = positions_fp
            try:
                self.query_one(PositionsPanel).sync_from_envelope(envelope, w)
            except Exception:
                pass
        regime_fp = regime_panel_fingerprint(state, w)
        if regime_fp != self._regime_fp:
            self._regime_fp = regime_fp
            try:
                self.query_one(RegimePanelWidget).sync_from_state(state, w)
            except Exception:
                pass

    def on_key(self, event) -> None:
        if self._handle_scroll_keys(event):
            return
        super().on_key(event)

    def _manual_order_flow(self, action: str) -> None:
        self._load_state()
        state = self._last_state or {}
        symbol = str(self._last_focus or state.get("symbol") or "").upper()
        position = state.get("position") or {}
        position_state = str(position.get("state") or "idle")
        mode = str(state.get("execution_mode") or "unknown").upper()
        if not symbol:
            self.notify("No focused symbol", severity="error")
            return
        if action == "BUY" and position_state != "idle":
            self.notify(f"{symbol} already has a position", severity="warning")
            return
        if action == "BUY":
            def selected(choice: str | None) -> None:
                if not choice:
                    return
                try:
                    request = write_manual_order_request(
                        symbol,
                        "BUY",
                        management_mode=choice,
                    )
                except Exception as exc:
                    self.notify(f"Manual BUY queue failed: {exc}", severity="error")
                    return
                label = "strategy" if choice == "strategy" else "manual"
                self.notify(
                    f"Manual BUY queued for {symbol} ({label}, {request['request_id'][:8]})",
                    severity="information",
                )

            self.app.push_screen(
                ChoiceModal(
                    f"Manual BUY {symbol} ({mode})",
                    [
                        ("strategy", "Bot manages strategy"),
                        ("manual", "I will sell manual"),
                    ],
                ),
                selected,
            )
            return
        if action == "SELL" and position_state != "bought":
            self.notify(f"{symbol} has no tracked position", severity="warning")
            return

        if action == "SELL":
            qty = float(position.get("quantity") or 0.0)
            management_mode = str(position.get("management_mode") or "strategy").lower()
            if management_mode == "manual":
                message = (
                    f"Manual SELL {symbol} ({mode})?\n"
                    f"This closes your manual-managed quantity {qty:.8f} on the next engine tick."
                )
            else:
                message = (
                    f"Manual SELL {symbol} ({mode})?\n"
                    f"This closes the tracked quantity {qty:.8f} on the next engine tick."
                )
        else:
            message = (
                f"Manual BUY {symbol} ({mode})?\n"
                "The engine will use configured sizing and all risk/allocation guards."
            )

        def confirmed(yes: bool) -> None:
            if not yes:
                return
            try:
                request = write_manual_order_request(symbol, action)
            except Exception as exc:
                self.notify(f"Manual {action} queue failed: {exc}", severity="error")
                return
            self.notify(
                f"Manual {action} queued for {symbol} ({request['request_id'][:8]})",
                severity="information",
            )

        self.app.push_screen(
            ConfirmModal(
                message,
                confirm_label=f"Confirm {action}",
                cancel_label="Cancel",
            ),
            confirmed,
        )

    def action_manual_buy(self) -> None:
        self._manual_order_flow("BUY")

    def action_manual_sell(self) -> None:
        self._manual_order_flow("SELL")


# ──────────────────────────────────────────────────────────────
# TradeLogScreen — native Textual trade log
# ──────────────────────────────────────────────────────────────

class TradeLogScreen(BaseTUIScreen):
    """Trade Log & Market Intelligence (Textual)."""

    BINDINGS = list(_NAV_BINDINGS) + list(_PAIR_BINDINGS)

    _scroll_id = "tradelog-scroll"

    def compose(self):
        yield AppHeader()
        with ScrollableContainer(id="tradelog-scroll"):
            yield TradeLogNativeBody(self.db, id="tradelog-body-root")
        yield AppFooter()

    def on_mount(self) -> None:
        super().on_mount()
        try:
            self.query_one("#tl-datatable").focus()
        except Exception:
            pass

    def on_screen_resume(self) -> None:
        super().on_screen_resume()
        try:
            self.run_worker(self.query_one(TradeLogNativeBody)._refresh(force=True))
        except Exception:
            pass
        try:
            self.query_one("#tl-datatable").focus()
        except Exception:
            pass


    def _get_view_name(self) -> str:
        return "tradelog"

    def _push_state(self) -> None:
        super()._push_state()
        try:
            body = self.query_one(TradeLogNativeBody)
            body.state = self._last_state
            body.focus_symbol = self._last_focus
        except Exception:
            pass

    def on_key(self, event) -> None:
        # Let DataTable handle its own cursor / sort keys, except j/k which we use for page scrolling.
        try:
            focused = self.focused
            if focused is not None and "DataTable" in focused.__class__.__name__:
                key = event.key.lower()
                if key in ("j", "k"):
                    if self._handle_scroll_keys(event):
                        return
                super().on_key(event)
                return
        except Exception:
            pass
        if self._handle_scroll_keys(event):
            return
        super().on_key(event)



# ──────────────────────────────────────────────────────────────
# IncidentExplorerScreen — native Textual incident explorer
# ──────────────────────────────────────────────────────────────

class IncidentExplorerScreen(BaseTUIScreen):
    """Incident Explorer (Textual)."""

    BINDINGS = list(_NAV_BINDINGS) + list(_INCIDENT_PAIR_BINDINGS) + list(_INCIDENT_BINDINGS)

    def compose(self):
        yield AppHeader()
        with Container(id="incidents-main"):
            yield IncidentNativeBody(self.db, id="incident-body-root")
        yield AppFooter()

    def on_mount(self) -> None:
        super().on_mount()

    def on_screen_resume(self) -> None:
        super().on_screen_resume()
        try:
            self.run_worker(self.query_one(IncidentNativeBody)._refresh(force=True))
        except Exception:
            pass

    def _get_view_name(self) -> str:
        return "incidents"

    def _push_state(self) -> None:
        super()._push_state()
        try:
            body = self.query_one(IncidentNativeBody)
            body.state = self._last_state
            body.focus_symbol = self._last_focus
        except Exception:
            pass

    def _incident_action(self, mapped: str) -> None:
        st = get_explorer_state()
        if handle_explorer_action(st, mapped):
            try:
                self.run_worker(self.query_one(IncidentNativeBody)._refresh(force=True))
            except Exception:
                pass


    def action_incident_down(self) -> None:
        self._incident_action("j")

    def action_incident_up(self) -> None:
        self._incident_action("k")

    def action_incident_toggle_filter(self) -> None:
        self._incident_action("a")

    def action_incident_refresh(self) -> None:
        self.notify("Refreshing…")
        self._incident_action("r")

    def action_incident_validate(self) -> None:
        self._incident_action("v")

    def action_incident_scroll_up(self) -> None:
        self._incident_action("+")

    def action_incident_scroll_down(self) -> None:
        self._incident_action("-")


# ──────────────────────────────────────────────────────────────
# BacktestScreen — focused-pair plugin replay summary
# ──────────────────────────────────────────────────────────────

class BacktestScreen(BaseTUIScreen):
    """Backtest summary and parity check for the focused trading pair."""

    BINDINGS = list(_NAV_BINDINGS) + list(_PAIR_BINDINGS) + [
        Binding("r,R,shift+r", "backtest_run", "Run Backtest", priority=True, show=False),
        Binding("g,G", "backtest_optimize", "Optimize Grid", priority=True, show=False),
        Binding("o,O", "backtest_apply_best", "Apply Best Config", priority=True, show=False),
    ]

    _scroll_id = "backtest-main"

    def compose(self):
        yield AppHeader()
        with ScrollableContainer(id="backtest-main"):
            yield BacktestNativeBody(id="backtest-body-root")
        yield AppFooter()

    def on_mount(self) -> None:
        super().on_mount()

    def on_screen_resume(self) -> None:
        super().on_screen_resume()

    def _get_view_name(self) -> str:
        return "backtest"

    def _push_state(self) -> None:
        super()._push_state()
        try:
            body = self.query_one(BacktestNativeBody)
            body.state = self._last_state
            body.focus_symbol = self._last_focus
        except Exception:
            pass

    def on_key(self, event) -> None:
        key = event.key.lower()
        if key in ("r", "shift+r"):
            self.action_backtest_run()
            event.prevent_default()
            event.stop()
            return
        elif key == "g":
            self.action_backtest_optimize()
            event.prevent_default()
            event.stop()
            return
        elif key == "o":
            self.action_backtest_apply_best()
            event.prevent_default()
            event.stop()
            return
        if self._handle_scroll_keys(event):
            return
        super().on_key(event)

    def action_backtest_run(self) -> None:
        body = self.query_one("#backtest-body-root", BacktestNativeBody)
        if body.is_running():
            return
        body.show_running_state()
        self.run_worker(body._run_backtest(), group="backtest-run")

    def action_backtest_optimize(self) -> None:
        body = self.query_one("#backtest-body-root", BacktestNativeBody)
        if body.is_running():
            return
        body.show_optimizing_state()
        self.run_worker(body._run_optimize(), group="backtest-optimize")

    def action_backtest_apply_best(self) -> None:
        body = self.query_one("#backtest-body-root", BacktestNativeBody)
        body.apply_best_config()
