from datetime import datetime
from typing import Dict, Any, List, Optional

from textual.widget import Widget
from textual.widgets import Static
from textual.reactive import reactive
from rich.text import Text

from xauby.database.db import LiteDB
from xauby.ui.chart import get_chart_lines, resolve_chart_timeframe
from xauby.runtime.symbol_resolver import focus_symbol_from_config
from xauby.ui.textual_tui.chart_legend import format_chart_legend
from xauby.ui.system import format_uptime
from xauby.ui.textual_tui.system_cache import get_cached_host_metrics
from xauby.ui.dashboard import (
    _render_strategy_checklist, render_blocker_box, _guard_status_labels,
    get_layout_profile, _chart_dimensions, _phone_regime_extras,
    _timeframe_countdown_compact,
    get_recent_events_lines,
    format_decision_gates_compact, format_engine_status_badges,
    format_mobile_safety_line, format_mobile_timing_line,
)
from xauby.ui.copy import format_monitoring_empty
from xauby.ui.state_view import (
    format_pairs_table_lines, format_static_portfolio_lines, format_all_positions_lines
)
from xauby.ui.widgets import (
    DEFAULT_REGIME,
    format_regime_detail_lines,
    format_regime_inline_lines,
    get_panel_column_widths,
)
from xauby.ui.textual_tui.layout import is_phone_layout, is_stacked_layout

from xauby.utils.common import TH_TZ

# Curated HSL-tailored colors matching original colors but structured
COLOR_PRIMARY = "#818CF8" # Indigo 400
COLOR_MUTED = "#6B7280"   # Gray 500
COLOR_DARK = "#374151"    # Gray 700
COLOR_GREEN = "#34D399"   # Emerald 400
COLOR_RED = "#F87171"     # Red 400
COLOR_YELLOW = "#FBBF24"  # Amber 400


def _chart_symbol(state: Dict[str, Any]) -> str:
    sym = str(state.get("symbol") or "").strip()
    if sym:
        return sym
    try:
        return focus_symbol_from_config()
    except Exception:
        return ""


class AppHeader(Static):
    """Header widget displaying Bot Status, Connection Info, and System Health."""

    def __init__(self, **kwargs):
        super().__init__("", **kwargs)
        self.styles.dock = "top"
        self.styles.background = "#18181B"
        self.styles.border_bottom = ("solid", "#3F3F46")
        self._state: Dict[str, Any] = {}
        self._envelope: Dict[str, Any] = {}
        self._render_fp: tuple = ()

    def sync_from_state(
        self,
        state: Dict[str, Any],
        envelope: Dict[str, Any],
        layout_width: int,
        *,
        force: bool = False,
    ) -> None:
        from xauby.ui.textual_tui.state_sync import header_fingerprint

        fp = header_fingerprint(state, layout_width)
        if not force and fp == self._render_fp:
            return
        self._render_fp = fp
        self._state = state or {}
        self._envelope = envelope or {}
        self.update(self._build_text(layout_width))

    def _build_text(self, layout_width: int) -> Text:
        W = max(20, layout_width)
        if not self._state:
            return Text.from_ansi("\033[94m✦ XAUBY ✦\033[0m Waiting for bot engine...")
        
        symbol = _chart_symbol(self._state)
        chart_tf = resolve_chart_timeframe(str(self._state.get("primary_timeframe", "4h") or "4h"))
        strat = str(self._state.get("strategy_name") or "cdc_action_zone")

        sim_only = bool(self._state.get("simulate_only", True))
        read_only = bool(self._state.get("read_only", False))
        exchange_state = self._state.get("exchange") or {}
        if isinstance(exchange_state, dict):
            exchange_name = str(
                exchange_state.get("name") or exchange_state.get("id") or ""
            ).strip()
        else:
            # Backward compatibility for state files written before exchange
            # identity became a structured top-level field.
            exchange_name = str(exchange_state).strip()
        exchange_badge = (
            f" \033[46;30m {exchange_name.upper()} \033[0m" if exchange_name else ""
        )

        is_phone = is_phone_layout(W)
        is_compact = is_stacked_layout(W)

        if is_phone:
            mode_str = "\033[42;30mSIM\033[0m" if sim_only else "\033[41;30mLIVE\033[0m"
            ro_str = " \033[43;30mRO\033[0m" if read_only else ""
        elif is_compact:
            mode_str = "\033[42;30m SIM \033[0m" if sim_only else "\033[41;30m LIVE \033[0m"
            ro_str = " \033[43;30m RO \033[0m" if read_only else ""
        else:
            mode_str = "\033[42;30m SIMULATION \033[0m" if sim_only else "\033[41;30m LIVE TRADING \033[0m"
            ro_str = " \033[43;30m READ-ONLY \033[0m" if read_only else ""

        pair_mode = str(self._state.get("execution_mode") or "")
        if pair_mode == "live":
            # LIVE pair badge: green
            mode_str = mode_str + " \033[92m[LIVE]\033[0m"
        elif pair_mode == "sim":
            # SIM pair badge: cyan
            mode_str = mode_str + " \033[96m[SIM]\033[0m"
        
        # Connection Stats
        latency = self._state.get("api_latency_ms", 0)
        offset = self._state.get("clock_offset_seconds", 0.0)
        latency_block = self._state.get("latency") or {}
        ws_age = int(latency_block.get("ws_tick_age_ms") or 0)
        tick_ms = int(latency_block.get("tick_duration_ms") or 0)
        sync_ms = int(latency_block.get("sync_candles_ms") or 0)
        state_ms = int(latency_block.get("state_export_ms") or 0)
        
        latency_col = "\033[92m" if latency < 150 else ("\033[93m" if latency < 350 else "\033[91m")
        ws_col = "\033[92m" if ws_age < 5_000 else ("\033[93m" if ws_age < 15_000 else "\033[91m")
        tick_col = "\033[92m" if tick_ms < 1_000 else ("\033[93m" if tick_ms < 3_000 else "\033[91m")
        offset_col = "\033[92m" if abs(offset) < 1.0 else ("\033[93m" if abs(offset) < 5.0 else "\033[91m")
        
        if is_phone:
            conn_info = f"Ping: {latency_col}{latency}ms\033[0m"
        elif is_compact:
            conn_info = (
                f"REST:{latency_col}{latency}ms\033[0m "
                f"WS:{ws_col}{ws_age / 1000:.1f}s\033[0m "
                f"Tick:{tick_col}{tick_ms}ms\033[0m"
            )
        else:
            conn_info = (
                f"REST:{latency_col}{latency}ms\033[0m "
                f"WS:{ws_col}{ws_age / 1000:.1f}s\033[0m "
                f"Tick:{tick_col}{tick_ms}ms\033[0m "
                f"Sync:{sync_ms}ms State:{state_ms}ms "
                f"Drift:{offset_col}{offset:+.2f}s\033[0m"
            )
        
        # System Health (cached — avoid psutil/disk on every paint)
        cpu_pct, ram_load, ram_used, ram_total, db_size_mb = get_cached_host_metrics()
        uptime_str = format_uptime(self._state.get("engine_started_at"))
        badges = format_engine_status_badges(
            cpu_pct, ram_load, db_size_mb, compact=is_phone or is_compact
        )
        if is_phone:
            uptime_seg = f"U:{uptime_str}"
        else:
            uptime_seg = f"\033[90mUp {uptime_str}\033[0m"

        # Ticker Info
        curr_price = float(self._state.get("current_price", 0.0))
        change = float(
            self._state.get("price_change_24h_pct")
            or self._state.get("percent_change_24h")
            or 0.0
        )
        change_color = "\033[92m" if change >= 0 else "\033[91m"
        
        if is_phone:
            ticker_str = f"\033[1;97m{symbol}\033[0m \033[1;96m${curr_price:,.1f}\033[0m ({change_color}{change:+.1f}%\033[0m)"
        elif is_compact:
            ticker_str = f"\033[1;97m{symbol}\033[0m (\033[90m{chart_tf.upper()}\033[0m) \033[1;96m${curr_price:,.2f}\033[0m ({change_color}{change:+.2f}%\033[0m)"
        else:
            ticker_str = f"\033[1;97m{symbol}\033[0m (\033[90m{chart_tf.upper()}\033[0m) \033[1;96m${curr_price:,.2f}\033[0m ({change_color}{change:+.2f}%\033[0m)"

        now_str = datetime.now(TH_TZ).strftime("%H:%M:%S")
        if is_phone:
            date_str = datetime.now(TH_TZ).strftime("%m-%d")
        elif is_compact:
            date_str = datetime.now(TH_TZ).strftime("%m/%d")
        else:
            date_str = datetime.now(TH_TZ).strftime("%Y-%m-%d")

        # Compose Layout
        if is_phone:
            title_prefix = "xA "
            ticker_str = (
                f"\033[1;97m{symbol}\033[0m "
                f"\033[1;96m${curr_price:,.1f}\033[0m "
                f"({change_color}{change:+.1f}%\033[0m)"
            )
            l1_left = Text.from_ansi(
                f"{title_prefix}{ticker_str} {mode_str}{exchange_badge}{ro_str}"
            )
            l1_right = Text.from_ansi(f"\033[90m{date_str} {now_str}\033[0m")
            if W >= 60 and (l1_left.cell_len + l1_right.cell_len + 2) <= W:
                l1 = l1_left + Text(" " * (W - l1_left.cell_len - l1_right.cell_len - 2)) + l1_right
            else:
                l1 = l1_left

            uptime_short = uptime_str.replace(" ", "")
            if len(uptime_short) > 7:
                uptime_short = uptime_short[:7]
            l2 = Text.from_ansi(
                f" {strat[:10]} P:{latency_col}{latency}ms\033[0m "
                f"{badges} U:{uptime_short}"
            )
            if l2.cell_len > W:
                l2 = Text.from_ansi(
                    f" {strat[:8]} P:{latency_col}{latency}ms\033[0m "
                    f"{badges}"
                )
            return Text("\n").join([l1, l2])

        if is_compact:
            title_prefix = "\033[1;35m✦ XAUBY ✦\033[0m  "
            l1_left = Text.from_ansi(
                f"{title_prefix}{ticker_str} {mode_str}{exchange_badge}{ro_str}"
            )
            l1_right = Text.from_ansi(f"\033[90m{date_str} {now_str}\033[0m")
            padding_l1 = W - l1_left.cell_len - l1_right.cell_len - 2
            if padding_l1 < 1:
                return Text("\n").join([l1_left, l1_right])
            l1 = l1_left + Text(" " * padding_l1) + l1_right

            strat_label = strat[:20]
            l2_left = Text.from_ansi(f" {strat_label}")
            l2_right = Text.from_ansi(f"{conn_info} {badges} {uptime_seg} ")
            padding_l2 = W - l2_left.cell_len - l2_right.cell_len - 2
            if padding_l2 < 1:
                return Text("\n").join([l1, l2_left, l2_right])
            l2 = l2_left + Text(" " * max(1, padding_l2)) + l2_right
        else:
            title_segment = (
                f" \033[1;35m✦ XAUBY TUI ✦\033[0m  {ticker_str}  "
                f"{mode_str}{exchange_badge}{ro_str}"
            )
            datetime_segment = f"\033[90m{date_str} {now_str} (ICT)\033[0m"
            
            l1_left = Text.from_ansi(title_segment)
            l1_right = Text.from_ansi(datetime_segment)
            padding_l1 = W - l1_left.cell_len - l1_right.cell_len - 2
            l1 = l1_left + Text(" " * max(1, padding_l1)) + l1_right
            
            l2_left = Text.from_ansi(f" Strategy: \033[1;36m{strat}\033[0m")
            l2_right = Text.from_ansi(f"{conn_info}  ┃  {badges}  {uptime_seg} ")
            padding_l2 = W - l2_left.cell_len - l2_right.cell_len - 2
            l2 = l2_left + Text(" " * max(1, padding_l2)) + l2_right
            
        return Text("\n").join([l1, l2])


class AppFooter(Widget):
    """Footer widget showing active hotkeys."""
    
    active_view = reactive("dashboard")
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.styles.dock = "bottom"
        self.styles.background = "#18181B"
        self.styles.border_top = ("solid", "#3F3F46")
        
    def render(self) -> Text:
        W = self.size.width
        # Highlight the active view
        d_style = "\033[1;97m" if self.active_view == "dashboard" else "\033[1;36m"
        t_style = "\033[1;97m" if self.active_view == "tradelog" else "\033[1;36m"
        i_style = "\033[1;97m" if self.active_view == "incidents" else "\033[1;36m"
        b_style = "\033[1;97m" if self.active_view == "backtest" else "\033[1;36m"
        m_style = "\033[1;97m" if self.active_view == "menu" else "\033[1;36m"

        segments = []
        if self.active_view == "dashboard":
            segments.append((" \033[1;32m[F7]Buy\033[0m", "screen.manual_buy"))
            segments.append((" \033[1;31m[F8]Sell\033[0m", "screen.manual_sell"))
        if W < 75:
            if self.active_view == "backtest":
                segments.append((" \033[1;32m[R]Run\033[0m", "screen.backtest_run"))
                segments.append((" \033[1;36m[G]Opt\033[0m", "screen.backtest_optimize"))
                segments.append((" \033[1;33m[O]Apply\033[0m", "screen.backtest_apply_best"))
                segments.append((" \033[1;90m[1-4]Nav\033[0m", ""))
            else:
                segments.append((f" {d_style}[1]D\033[0m", "screen.show_dashboard"))
                segments.append((f" {t_style}[2]L\033[0m", "screen.show_tradelog"))
                segments.append((f" {i_style}[3]I\033[0m", "screen.show_incidents"))
                segments.append((f" {b_style}[4]B\033[0m", "screen.show_backtest"))
            if self.active_view == "menu":
                segments.append((f" {m_style}[Ent]Dash\033[0m", "screen.show_dashboard"))
            else:
                segments.append((f" {m_style}[Ent]M\033[0m", "screen.show_menu"))
            segments.append((" \033[1;31m[Q]\033[0m", "screen.quit_app"))
        elif W < 90:
            if self.active_view == "backtest":
                segments.append((" \033[1;32m[R]Run\033[0m", "screen.backtest_run"))
                segments.append((" \033[1;36m[G]Opt\033[0m", "screen.backtest_optimize"))
                segments.append((" \033[1;33m[O]Apply\033[0m", "screen.backtest_apply_best"))
            else:
                segments.append((f" {d_style}[1]Dash\033[0m", "screen.show_dashboard"))
                segments.append((f" {t_style}[2]Log\033[0m", "screen.show_tradelog"))
                segments.append((f" {i_style}[3]Inc\033[0m", "screen.show_incidents"))
                segments.append((f" {b_style}[4]BT\033[0m", "screen.show_backtest"))
            if self.active_view == "menu":
                segments.append((f" {m_style}[Ent]Dash\033[0m", "screen.show_dashboard"))
            else:
                segments.append((f" {m_style}[Ent]Menu\033[0m", "screen.show_menu"))
            segments.append((" \033[1;31m[Q]Quit\033[0m", "screen.quit_app"))
        elif is_phone_layout(W):
            segments.append((f" {d_style}[1/D]Dash\033[0m", "screen.show_dashboard"))
            segments.append((f" {t_style}[2/T]Log\033[0m", "screen.show_tradelog"))
            segments.append((f" {i_style}[3/I]Inc\033[0m", "screen.show_incidents"))
            segments.append((f" {b_style}[4/B]Bt\033[0m", "screen.show_backtest"))
            if self.active_view == "backtest":
                segments.append((" \033[1;32m[R]Run\033[0m", "screen.backtest_run"))
                segments.append((" \033[1;36m[G]Opt\033[0m", "screen.backtest_optimize"))
                segments.append((" \033[1;33m[O]Apply\033[0m", "screen.backtest_apply_best"))
            segments.append((f" {m_style}[^M]Menu\033[0m", "screen.show_menu"))
            segments.append((" \033[1;90m[↑↓]Pair [←→]Page\033[0m", ""))
            segments.append((f" \033[1;31m[Q]Quit\033[0m", "screen.quit_app"))
        elif is_stacked_layout(W):
            segments.append((f" {d_style}[1/D]Dash\033[0m", "screen.show_dashboard"))
            segments.append((f" {t_style}[2/T]Log\033[0m", "screen.show_tradelog"))
            segments.append((f" {i_style}[3/I]Inc\033[0m", "screen.show_incidents"))
            segments.append((f" {b_style}[4/B]Bt\033[0m", "screen.show_backtest"))
            if self.active_view == "backtest":
                segments.append((" \033[1;32m[R] Run\033[0m", "screen.backtest_run"))
                segments.append((" \033[1;36m[G] Optimize\033[0m", "screen.backtest_optimize"))
                segments.append((" \033[1;33m[O] Apply Best\033[0m", "screen.backtest_apply_best"))
            if self.active_view == "tradelog":
                segments.append((" \033[1;90m[A]Scope [F]Filter [Alt+1-6]Sort\033[0m", ""))
            if self.active_view == "incidents":
                segments.append((" \033[1;90m[j/k]Runs [V]Val [R]Refr [A]Filt\033[0m", ""))
            segments.append((f" {m_style}[Ctrl+M]Menu\033[0m", "screen.show_menu"))
            segments.append((" \033[1;90m[↑↓]Pair [←→]Page\033[0m", ""))
            segments.append((f" \033[1;31m[Q]Quit\033[0m", "screen.quit_app"))
        else:
            segments.append((f" {d_style}[1/D] Dashboard\033[0m  ", "screen.show_dashboard"))
            segments.append((f" {t_style}[2/T] Log & Intel\033[0m  ", "screen.show_tradelog"))
            segments.append((f" {i_style}[3/I] Incidents\033[0m  ", "screen.show_incidents"))
            segments.append((f" {b_style}[4/B] Backtest\033[0m  ", "screen.show_backtest"))
            if self.active_view == "backtest":
                segments.append(("\033[1;32m[R] Run\033[0m  ", "screen.backtest_run"))
                segments.append(("\033[1;36m[G] Optimize\033[0m  ", "screen.backtest_optimize"))
                segments.append(("\033[1;33m[O] Apply Best\033[0m  ", "screen.backtest_apply_best"))
            if self.active_view == "tradelog":
                segments.append((" \033[1;90m[A] Scope  [F] Filter  [Alt+1-6] Sort\033[0m  ", ""))
            if self.active_view == "incidents":
                segments.append((" \033[1;90m[j/k] Runs  [↑↓] Timeline  [V] Validate  [R] Refresh  [A] Filter\033[0m  ", ""))
            segments.append((f" {m_style}[Ctrl+M] Menu\033[0m  ", "screen.show_menu"))
            segments.append((" \033[1;90m[↑/↓] Cycle Pair  [←/→] Page\033[0m  ", ""))
            segments.append((f" \033[1;31m[Q] Quit\033[0m", "screen.quit_app"))

        res = Text()
        for ansi_str, action in segments:
            seg_text = Text.from_ansi(ansi_str)
            if action:
                seg_text.apply_meta({"@click": action})
            res.append(seg_text)
        return res


class ChartLegendBar(Static):
    """Always-visible CDC zone + EMA line legend under the chart."""

    def __init__(self, **kwargs):
        super().__init__("", **kwargs)
        self._legend_width = 0
        self._legend_strategy = ""

    def sync_width(self, width: int, strategy_name: Optional[str] = None) -> None:
        # Prefer the bar's own rendered width so the legend aligns with the
        # chart panel (which fills ~65% of the screen on wide terminals).
        own_w = 0
        try:
            own_w = int(self.size.width)
        except Exception:
            own_w = 0
        w = own_w if own_w >= 24 else max(20, width)
        strat = strategy_name or ""
        if w == self._legend_width and strat == self._legend_strategy:
            return
        self._legend_width = w
        self._legend_strategy = strat
        compact = w < 75
        self.update(
            Text.from_ansi(
                format_chart_legend(w, compact=compact, strategy_name=strat or None)
            )
        )


class ANSIChartWidget(Static):
    """Classic ANSI candlestick chart (CDC zones + EMA overlays)."""

    def __init__(self, db: LiteDB, **kwargs):
        super().__init__("", **kwargs)
        self.db = db
        self.styles.min_height = 12
        self.styles.height = "auto"
        self._sync_fp: tuple = ()
        self._line_signature = ""
        self._refreshing = False
        self._needs_refresh = False
        self._next_state = None
        self._next_width = 0

    def _compose_lines(self, state: Dict[str, Any], layout_width: int) -> str:
        symbol = _chart_symbol(state)
        strategy_name = str(state.get("strategy_name") or "")
        curr_price = float(state.get("current_price", 0.0))
        chart_tf = resolve_chart_timeframe(str(state.get("primary_timeframe", "4h") or "4h"))

        w_box = max(20, layout_width)
        profile = get_layout_profile(w_box)

        # Prefer the chart widget's real rendered width so candles fill the
        # whole panel. The CSS gives #main-panel ~65% of the screen, which is
        # wider than the legacy fixed estimate — using that estimate left a big
        # empty gutter on the right of wide terminals.
        own_w = 0
        try:
            own_w = int(self.size.width)
        except Exception:
            own_w = 0

        if own_w >= 24:
            content_width = own_w
        elif profile.is_wide:
            content_width = get_panel_column_widths(w_box)[1]
        else:
            content_width = max(16, w_box - 4)

        width, height, show_vol = _chart_dimensions(w_box, profile, content_width=content_width)

        phone = is_phone_layout(w_box)
        if phone:
            height = min(height, 16)

        lines = list(
            get_chart_lines(
                self.db,
                symbol,
                width=width,
                height=height,
                current_price=curr_price,
                show_volume=show_vol and not phone,
                max_line_width=content_width,
                timeframe=chart_tf,
                include_footer_legend=False,
                strategy_name=strategy_name or None,
            )
        )
        return "\n".join(lines)

    async def sync_from_state(self, state: Dict[str, Any], layout_width: int) -> None:
        """Update chart text only when rendered output actually changes."""
        from xauby.ui.textual_tui.state_sync import chart_panel_fingerprint
        import asyncio

        if not state:
            waiting = (
                "\033[90m  ⏳ Waiting for bot engine to generate state...\n"
                "     Chart will appear once candle data is available.\033[0m"
            )
            if waiting != self._line_signature:
                self._line_signature = waiting
                self._sync_fp = ("empty", layout_width)
                self.update(Text.from_ansi(waiting))
            return

        fp = chart_panel_fingerprint(state, layout_width)
        if fp == self._sync_fp:
            return

        if getattr(self, "_refreshing", False):
            self._needs_refresh = True
            self._next_state = state
            self._next_width = layout_width
            return
        self._refreshing = True
        self._needs_refresh = False
        try:
            text = await asyncio.to_thread(self._compose_lines, state, layout_width)
            if text != self._line_signature:
                self._line_signature = text
                self.update(Text.from_ansi(text))
                line_count = text.count("\n") + 1
                self.styles.min_height = max(12, line_count + 1)
            self._sync_fp = fp
        finally:
            self._refreshing = False
            if getattr(self, "_needs_refresh", False):
                next_st = getattr(self, "_next_state", None)
                next_w = getattr(self, "_next_width", layout_width)
                if next_st:
                    self.run_worker(self.sync_from_state(next_st, next_w))


class StrategyChecklistPanel(Static):
    """Renders action summary + strategy checklist with range/progress bars."""

    def __init__(self, **kwargs):
        super().__init__("", **kwargs)
        self._sync_fp: tuple = ()

    def sync_from_state(self, state: Dict[str, Any], layout_width: int) -> None:
        from xauby.ui.textual_tui.state_sync import checklist_panel_fingerprint

        fp = checklist_panel_fingerprint(state, layout_width)
        if fp == self._sync_fp:
            return
        self._sync_fp = fp
        if not state:
            self.update(Text.from_ansi("\033[90m  Waiting for strategy state...\033[0m"))
            return

        meta = state.get("signal_meta", {}) or {}
        items = meta.get("checklist", [])
        if not items:
            from xauby.ui.dashboard import _checklist_items_from_state
            items = _checklist_items_from_state(state)

        w = max(20, layout_width)
        profile = get_layout_profile(w)

        # Action summary header (previously shown in the separate DecisionGatesPanel)
        decision_lines = format_decision_gates_compact(state, profile, w)

        if profile.is_phone:
            # Phone: operator status + compact action/chips + actionable blocker hint.
            lines = [format_mobile_safety_line(state, w)]
            lines.append(format_mobile_timing_line(state, w))
            lines.extend(decision_lines[:2])
            blocker = render_blocker_box(state, items, w, is_phone=True)
            if blocker:
                lines.extend(blocker)
        else:
            # Tablet/wide: action summary + separator + detailed checklist + blocker box
            # Omit the inline warn line from decision (line 3+) — blocker box covers it
            header = decision_lines[:2]
            lines = header + [""]
            lines.extend(_render_strategy_checklist(items, profile, w))
            blocker = render_blocker_box(state, items, w, is_phone=False)
            if blocker:
                lines.extend(blocker)

        self.update(Text.from_ansi("\n".join(lines)))


class PortfolioPanel(Static):
    """Renders Portfolio Balances and Asset Allocation."""

    def __init__(self, **kwargs):
        super().__init__("", **kwargs)
        self._sync_fp: tuple = ()

    def sync_from_envelope(self, envelope: Dict[str, Any], layout_width: int) -> None:
        from xauby.ui.textual_tui.state_sync import portfolio_panel_fingerprint

        fp = portfolio_panel_fingerprint(envelope, layout_width)
        if fp == self._sync_fp:
            return
        self._sync_fp = fp
        if not envelope:
            self.update(Text.from_ansi("\033[90m  Waiting for portfolio data...\033[0m"))
            return

        w = max(20, layout_width)
        profile = get_layout_profile(w)
        lines = format_static_portfolio_lines(envelope, w, is_phone=profile.is_phone)
        if not lines:
            by_symbol = envelope.get("by_symbol") or {}
            symbol = str(envelope.get("focused_symbol") or "").strip()
            if not symbol:
                try:
                    symbol = focus_symbol_from_config()
                except Exception:
                    symbol = ""
            lines = format_pairs_table_lines(envelope, symbol, w, is_phone=profile.is_phone)
        if not lines:
            lines = ["  \033[90mNo portfolio data available.\033[0m"]
        try:
            from xauby.ui.state_view import regime_router_banner_lines

            banner = regime_router_banner_lines(envelope, w)
        except Exception:
            banner = []
        # On phone: skip banner so balance is always first; elsewhere append below equity
        if profile.is_phone or not banner:
            self.update(Text.from_ansi("\n".join(lines)))
        else:
            self.update(Text.from_ansi("\n".join(lines + banner)))


class PositionsPanel(Static):
    """Renders active open positions and orders."""

    def __init__(self, **kwargs):
        super().__init__("", **kwargs)
        self._sync_fp: tuple = ()

    def sync_from_envelope(self, envelope: Dict[str, Any], layout_width: int) -> None:
        from xauby.ui.textual_tui.state_sync import positions_panel_fingerprint

        fp = positions_panel_fingerprint(envelope, layout_width)
        if fp == self._sync_fp:
            return
        self._sync_fp = fp
        if not envelope:
            self.update(Text.from_ansi("\033[90m  Waiting for position data...\033[0m"))
            return

        w = max(20, layout_width)
        profile = get_layout_profile(w)
        by_symbol = envelope.get("by_symbol") or {}
        lines = format_all_positions_lines(by_symbol, w, is_phone=profile.is_phone)
        self.display = not (profile.is_phone and not lines)
        if not lines and not profile.is_phone:
            lines = [format_monitoring_empty(compact=profile.is_phone, with_subline=not profile.is_phone)]
        self.update(Text.from_ansi("\n".join(lines)))


class RegimePanelWidget(Static):
    """Renders current market regime, macro guard, and checklist summary."""

    def __init__(self, **kwargs):
        super().__init__("", **kwargs)
        self._sync_fp: tuple = ()

    def _countdown_compact(self, timeframe: str = "4h") -> str:
        return _timeframe_countdown_compact(timeframe)

    def sync_from_state(self, state: Dict[str, Any], layout_width: int) -> None:
        from xauby.ui.textual_tui.state_sync import regime_panel_fingerprint

        fp = regime_panel_fingerprint(state, layout_width)
        if fp == self._sync_fp:
            return
        self._sync_fp = fp
        if not state:
            self.update(Text.from_ansi("\033[90m  Waiting for regime data...\033[0m"))
            return

        W = max(20, layout_width)
        profile = get_layout_profile(W)
        reg = state.get("regime") or dict(DEFAULT_REGIME)
        g_info = state.get("macro_guard", {})
        guard_badge, guard_compact = _guard_status_labels(g_info)
        risk = state.get("risk", {})
        risk_pct = float(risk.get("risk_pct", 0.01) or 0.01) * 100.0

        pos = state.get("position", {})
        pos_state = str(pos.get("state", "idle")).upper()
        pos_pnl = float(pos.get("unrealized_pnl_pct", 0.0))
        pnl_col = "\033[92m" if pos_pnl >= 0 else "\033[91m"
        pos_str = f"POSITION: \033[1;97m{pos_state}\033[0m ({pnl_col}{pos_pnl:+.2f}%\033[0m)"

        lines: List[str] = []
        if profile.is_phone:
            chart_tf = str(state.get("primary_timeframe") or "4h")
            guard_inline, close_text, risk_text = _phone_regime_extras(
                guard_compact, g_info, self._countdown_compact(chart_tf), risk_pct,
            )
            lines.extend(
                format_regime_inline_lines(
                    reg,
                    guard_text=guard_inline,
                    max_width=max(20, W - 2),
                    close_text=close_text,
                    risk_text=risk_text,
                )
            )
            lines.extend(format_regime_detail_lines(reg, compact=True))
        elif profile.is_tablet:
            guard_inline = guard_compact if g_info.get("enabled") else "OFF"
            lines.extend(
                format_regime_inline_lines(
                    reg,
                    guard_text=guard_inline,
                    max_width=max(20, W - 2),
                )
            )
            lines.extend(format_regime_detail_lines(reg, compact=True))
        else:
            regime_name = str(reg.get("regime") or "UNKNOWN").upper()
            phase = str(reg.get("phase") or "UNKNOWN").replace("_", " ")
            regime_badge = f"\033[44;97m REGIME: {regime_name} \033[0m"
            phase_badge = f"\033[45;97m PHASE: {phase} \033[0m"
            lines = [
                f"  {regime_badge}  {phase_badge}  {guard_badge}",
                "",
                f"  {pos_str}",
            ]
            lines.extend(format_regime_detail_lines(reg, compact=False))

        if profile.is_phone and pos_state != "IDLE":
            lines.append(f"  {pos_str}")

        events = get_recent_events_lines(
            state,
            limit=2 if profile.is_phone else 3,
            compact=profile.is_phone,
        )
        if events:
            lines.append("")
            lines.append("  \033[1;97mRECENT EVENTS:\033[0m")
            lines.extend(events)

        self.update(Text.from_ansi("\n".join(lines)))
