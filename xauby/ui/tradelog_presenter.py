"""Trade log view model for Textual TUI (no stdout rendering)."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from xauby.analytics.calculator import calculate_metrics
from xauby.storage.interface import IDatabaseRepository
from xauby.ui.chart import get_capital_curve_lines
from xauby.ui.dashboard import LayoutProfile, get_layout_profile
from xauby.ui.tradelog import _build_trade_rows, _compute_tradelog_layout
from xauby.ui.state_view import default_symbol_from_whitelist
from xauby.ui.widgets import (
    DEFAULT_REGIME,
    compute_trade_stats,
    format_panel_header,
    pad_line,
    render_advanced_analytics_lines,
    render_track_record_lines,
    format_regime_user_lines,
    format_regime_compact_lines,
)

from xauby.utils.colors import C_MUTED, C_PRIMARY, C_RESET, C_BOLD, C_GREEN, C_RED
from xauby.utils.common import format_to_ict

TRADE_FILTERS = ("all", "win", "loss", "live", "sl", "manual")


@dataclass
class TradeTableRow:
    date_ict: str
    side: str
    size: str
    prices: str
    pnl_text: str
    pnl_value: float
    trigger: str = ""
    symbol: str = ""
    mode: str = ""
    strategy: str = ""
    duration: str = ""


@dataclass
class TradeLogViewModel:
    symbol: str
    trades: List[Dict[str, Any]]
    metrics: Any
    stats: Dict[str, Any]
    regime: Dict[str, Any]
    layout: Dict[str, Any]
    profile: LayoutProfile
    regime_lines: List[str] = field(default_factory=list)
    analytics_lines: List[str] = field(default_factory=list)
    track_lines: List[str] = field(default_factory=list)
    performance_lines: List[str] = field(default_factory=list)
    peak_lines: List[str] = field(default_factory=list)
    timeline_line: str = ""
    curve_lines: List[str] = field(default_factory=list)
    table_rows: List[TradeTableRow] = field(default_factory=list)
    ansi_trade_rows: List[str] = field(default_factory=list)
    trade_card_lines: List[str] = field(default_factory=list)
    is_wide: bool = False
    total_trades: int = 0
    visible_trades: int = 0
    scope_label: str = ""
    filter_label: str = ""


def _format_qty(value: Any) -> str:
    try:
        qty = float(value or 0.0)
    except (TypeError, ValueError):
        return "-"
    if qty <= 0:
        return "-"
    if qty < 1:
        return f"{qty:.8f}".rstrip("0").rstrip(".")
    if qty < 100:
        return f"{qty:.4f}".rstrip("0").rstrip(".")
    return f"{qty:,.2f}".rstrip("0").rstrip(".")


def _format_duration(t: Dict[str, Any]) -> str:
    opened = t.get("opened_at_dt")
    closed = t.get("closed_at_dt")
    if (not opened or not closed) and (t.get("opened_at") or t.get("closed_at")):
        try:
            from xauby.track_record.generator import _parse_date

            opened = opened or _parse_date(t.get("opened_at"))
            closed = closed or _parse_date(t.get("closed_at"))
        except Exception:
            pass
    if not opened or not closed:
        return "-"
    try:
        seconds = max(0.0, (closed - opened).total_seconds())
    except Exception:
        return "-"
    if seconds <= 0:
        return "-"
    hours, rem = divmod(int(seconds), 3600)
    minutes, _ = divmod(rem, 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _pnl_thb_suffix(pnl: float, *, compact: bool) -> str:
    try:
        from xauby.utils.currency import usdt_to_thb, format_thb

        if pnl == 0:
            return ""
        return f" {C_MUTED}{format_thb(usdt_to_thb(pnl), compact=compact)}{C_RESET}"
    except Exception:
        return ""


def _clip(text: str, max_len: int) -> str:
    if max_len <= 0:
        return ""
    if len(text) <= max_len:
        return text
    if max_len <= 1:
        return text[:max_len]
    return text[: max_len - 1] + "…"


def _short_regime(value: Any) -> str:
    text = str(value or "-").upper()
    replacements = {
        "BULL_TREND_STRONG": "BULL_STRONG",
        "BULL_TREND_WEAK": "BULL_WEAK",
        "BEAR_TREND_STRONG": "BEAR_STRONG",
        "BEAR_TREND_WEAK": "BEAR_WEAK",
        "SIDEWAYS_CHOP": "CHOP",
        "LOW_VOL_RANGE": "LOW_VOL",
    }
    return replacements.get(text, text.replace("_TREND_", "_"))


def _short_symbol(symbol: str) -> str:
    sym = str(symbol or "").upper()
    if sym.endswith("USDT"):
        return sym[:-4]
    return sym[:6] or "-"


def _short_dt(value: Any) -> str:
    dt = format_to_ict(value)
    # format_to_ict usually returns MM-DD HH:MM; keep DD HH:MM on phone.
    if len(dt) >= 11 and dt[2] == "-":
        return dt[3:11]
    return dt[:8]


def _short_reason(value: str) -> str:
    text = str(value or "").lower()
    has_sl = "stop" in text or "sl" in text
    has_manual = "manual" in text
    if has_sl and has_manual:
        return "SL+Man"
    if has_sl:
        return "SL"
    if has_manual:
        return "Manual"
    if "take" in text or "tp" in text:
        return "TP"
    if "strategy" in text:
        return "Strategy"
    return _clip(str(value or "Exit"), 8)


def _filter_label(mode: str) -> str:
    labels = {
        "all": "All exits",
        "win": "Winning exits",
        "loss": "Losing exits",
        "live": "LIVE only",
        "sl": "Stop-loss exits",
        "manual": "Manual exits",
    }
    return labels.get(mode, labels["all"])


def _apply_trade_filter(trades: List[Dict[str, Any]], mode: str) -> List[Dict[str, Any]]:
    mode = (mode or "all").lower()
    if mode not in TRADE_FILTERS:
        mode = "all"
    if mode == "all":
        return list(trades)
    if mode == "win":
        return [t for t in trades if float(t.get("net_pnl") or 0.0) > 0]
    if mode == "loss":
        return [t for t in trades if float(t.get("net_pnl") or 0.0) < 0]
    if mode == "live":
        return [t for t in trades if str(t.get("execution_mode") or "live").lower() == "live"]
    if mode == "sl":
        return [t for t in trades if "stop" in str(t.get("trigger") or "").lower() or "sl" in str(t.get("trigger") or "").lower()]
    if mode == "manual":
        return [t for t in trades if "manual" in str(t.get("trigger") or "").lower() or "manual" in str(t.get("strategy_name") or "").lower()]
    return list(trades)


def _unique_join(values: List[str], *, limit: int = 2) -> str:
    seen: List[str] = []
    for value in values:
        v = str(value or "").strip()
        if v and v not in seen:
            seen.append(v)
    if not seen:
        return "-"
    if len(seen) <= limit:
        return " + ".join(seen)
    return " + ".join(seen[:limit]) + f" +{len(seen) - limit}"


def _first_present(values: List[Any], default: str = "-") -> Any:
    for value in values:
        if str(value or "").strip():
            return value
    return default


def _group_position_exits(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[tuple, Dict[str, Any]] = {}
    order: List[tuple] = []
    for t in trades:
        symbol = str(t.get("symbol") or "").upper()
        opened_at = str(t.get("opened_at") or "")
        entry_price = round(float(t.get("entry_price") or 0.0), 8)
        key = (symbol, opened_at or str(t.get("id") or ""), entry_price)
        if key not in grouped:
            grouped[key] = {"symbol": symbol, "opened_at": opened_at, "entry_price": entry_price, "legs": []}
            order.append(key)
        grouped[key]["legs"].append(t)

    cards: List[Dict[str, Any]] = []
    for key in order:
        item = grouped[key]
        legs = sorted(item["legs"], key=lambda row: str(row.get("closed_at") or ""), reverse=True)
        qty = sum(float(t.get("amount") or 0.0) for t in legs)
        entry_cost = sum(float(t.get("entry_cost") or 0.0) for t in legs)
        gross_exit = sum(float(t.get("gross_exit") or 0.0) for t in legs)
        fees = sum(float(t.get("total_fees") or 0.0) for t in legs)
        pnl = sum(float(t.get("net_pnl") or 0.0) for t in legs)
        pnl_pct = (pnl / entry_cost * 100.0) if entry_cost > 0 else sum(float(t.get("net_pnl_pct") or 0.0) for t in legs)
        avg_entry = (entry_cost / qty) if qty > 0 else float(item.get("entry_price") or 0.0)
        avg_exit = (gross_exit / qty) if qty > 0 else 0.0
        closed_at = max(str(t.get("closed_at") or "") for t in legs)
        entry_regime = _first_present([t.get("entry_regime") for t in reversed(legs)])
        exit_regime = _first_present([t.get("exit_regime") for t in legs])
        cards.append(
            {
                "symbol": item["symbol"],
                "opened_at": item["opened_at"],
                "closed_at": closed_at,
                "legs": legs,
                "leg_count": len(legs),
                "quantity": qty,
                "entry_price": avg_entry,
                "exit_price": avg_exit,
                "entry_cost": entry_cost,
                "gross_exit": gross_exit,
                "fees": fees,
                "net_pnl": pnl,
                "net_pnl_pct": pnl_pct,
                "mode": _unique_join([str(t.get("execution_mode") or "live").upper() for t in legs], limit=1),
                "strategy": _unique_join([str(t.get("strategy_name") or "-") for t in legs], limit=2),
                "trigger": _unique_join([str(t.get("trigger") or "N/A") for t in legs], limit=2),
                "entry_regime": _short_regime(entry_regime),
                "exit_regime": _short_regime(exit_regime),
            }
        )
    cards.sort(key=lambda row: str(row.get("closed_at") or ""), reverse=True)
    return cards


def _build_trade_card_lines(trades: List[Dict[str, Any]], max_trades: int, width: int) -> List[str]:
    from xauby.ui.widgets import pad_line, to_compact

    lines: List[str] = []
    if not trades:
        lines.append(pad_line(f"  {C_MUTED}No closed trades yet.{C_RESET}", width))
        return lines

    for t in trades[:max_trades]:
        symbol = str(t.get("symbol") or "").upper() or "-"
        mode = str(t.get("mode") or "LIVE").upper()
        if not t.get("mode"):
            mode = str(t.get("execution_mode") or "live").upper()
        side = str(t.get("side") or "EXIT").upper()[:4]
        trigger = str(t.get("trigger") or "N/A")
        entry_regime = _short_regime(t.get("entry_regime"))
        exit_regime = _short_regime(t.get("exit_regime"))
        dt = format_to_ict(t.get("closed_at", ""))[:13]
        qty = _format_qty(t.get("amount"))
        entry = float(t.get("entry_price") or 0.0)
        exit_p = float(t.get("exit_price") or 0.0)
        pnl = float(t.get("net_pnl") or 0.0)
        pnl_pct = float(t.get("net_pnl_pct") or 0.0)
        pnl_c = C_GREEN if pnl >= 0 else C_RED
        sign = "+" if pnl >= 0 else ""
        if width < 75:
            reason = _short_reason(trigger)
            line = (
                f"  {_short_symbol(symbol)} {side} {_short_dt(t.get('closed_at'))} "
                f"{sign}{pnl:.3f}U({pnl_pct:+.2f}%) "
                f"{to_compact(entry)}->{to_compact(exit_p)} {reason}"
            )
            line = _clip(line, max(20, width - 1))
        else:
            reason = _clip(trigger, 34)
            line = (
                f"  {C_BOLD}{symbol}{C_RESET} {mode} {side} {C_MUTED}{dt}{C_RESET} | "
                f"{pnl_c}{sign}{pnl:.4f}U {pnl_pct:+.2f}%{C_RESET} | "
                f"Qty {qty} | {to_compact(entry)}->{to_compact(exit_p)} | {reason}"
            )
        if width >= 95:
            line += f" | {_clip(entry_regime, 10)}->{_clip(exit_regime, 10)}"
        lines.append(pad_line(line, width))
    return lines


def _trade_table_rows(trades: List[Dict[str, Any]], max_trades: int, W: int) -> List[TradeTableRow]:
    from xauby.ui.widgets import format_pnl, format_pnl_compact, to_compact

    rows: List[TradeTableRow] = []
    for t in trades[:max_trades]:
        dt = format_to_ict(t.get("closed_at", ""))
        side = str(t.get("side", "BUY"))
        pnl_val = float(t.get("net_pnl") or 0.0)
        pnl_pct_val = float(t.get("net_pnl_pct") or 0.0)
        trigger = str(t.get("trigger") or "N/A")
        symbol = str(t.get("symbol") or "").upper()
        mode = str(t.get("execution_mode") or "live").upper()
        strategy = str(t.get("strategy_name") or "-")
        duration = _format_duration(t)
        if W < 75:
            pnl_text = format_pnl_compact(pnl_val, pnl_pct_val)
            pnl_text += _pnl_thb_suffix(pnl_val, compact=True)
            rows.append(
                TradeTableRow(
                    date_ict=dt[:11],
                    side=side[:4],
                    size="",
                    prices="",
                    pnl_text=pnl_text,
                    pnl_value=pnl_val,
                    trigger=_clip(trigger, 18),
                    symbol=symbol,
                    mode=mode,
                    strategy=strategy,
                    duration=duration,
                )
            )
        else:
            amount_val = float(t.get("amount") or 0.0)
            size = _format_qty(amount_val)[:12]
            entry = float(t.get("entry_price") or 0.0)
            exit_p = float(t.get("exit_price") or 0.0)
            prices = f"{to_compact(entry)}->{to_compact(exit_p)}"[:15]
            rows.append(
                TradeTableRow(
                    date_ict=dt[:13],
                    side=side[:4],
                    size=size,
                    prices=prices,
                    pnl_text=format_pnl(pnl_val, pnl_pct_val),
                    pnl_value=pnl_val,
                    trigger=_clip(trigger, 22),
                    symbol=symbol,
                    mode=mode,
                    strategy=strategy,
                    duration=duration,
                )
            )
    return rows


def _sim_live_split_lines(trades: List[Dict[str, Any]], width: int) -> List[str]:
    """Per-mode PnL breakdown. Sim and Live PnL are never aggregated together.

    Returns empty when trades belong to a single execution mode (nothing to
    split). Spec P0-4: Sim PnL displayed separately from Live PnL.
    """
    from xauby.ui.widgets import pad_line
    from xauby.utils.colors import C_GREEN, C_RED, C_MUTED, C_RESET, C_BOLD

    sim = [t for t in trades if str(t.get("execution_mode") or "live") == "sim"]
    live = [t for t in trades if str(t.get("execution_mode") or "live") == "live"]
    if not sim or not live:
        return []

    def _net(rows: List[Dict[str, Any]]) -> float:
        return sum(float(t.get("net_pnl", 0.0) or 0.0) for t in rows)

    lines: List[str] = []
    for label, rows in (("Sim", sim), ("Live", live)):
        net = _net(rows)
        color = C_GREEN if net >= 0 else C_RED
        sign = "+" if net >= 0 else ""
        lines.append(
            pad_line(
                f"  {C_BOLD}{label} PnL{C_RESET}: {color}{sign}{net:,.2f} USDT{C_RESET} "
                f"{C_MUTED}({len(rows)} trades){C_RESET}",
                width,
            )
        )
    return lines


def _build_performance_lines(
    stats: Dict[str, Any],
    total: int,
    width: int,
    *,
    show_peak: bool,
    show_timeline: bool,
) -> tuple[List[str], List[str], str]:
    from xauby.ui.widgets import format_panel_header, pad_line
    from xauby.utils.colors import C_GREEN, C_RED, C_PRIMARY, C_RESET, C_BOLD

    net_pnl = stats["net_pnl"]
    pnl_color = C_GREEN if net_pnl >= 0 else C_RED
    sign = "+" if net_pnl >= 0 else ""
    perf: List[str] = []
    peak: List[str] = []
    
    _thb_net = ""
    _thb_avg = ""
    try:
        from xauby.utils.currency import usdt_to_thb, format_thb
        if net_pnl != 0:
            _thb_net = f" {C_MUTED}{format_thb(usdt_to_thb(net_pnl))}{C_RESET}"
        avg_pnl = stats.get("avg_pnl_usdt", 0.0)
        if avg_pnl != 0:
            _thb_avg = f" {C_MUTED}{format_thb(usdt_to_thb(avg_pnl), compact=True)}{C_RESET}"
    except Exception:
        pass

    if width < 75:
        perf.append(format_panel_header("PERFORMANCE SUMMARY", width, C_PRIMARY))
        perf.append(
            pad_line(f"  {C_BOLD}Total Trades:{C_RESET} {C_PRIMARY}{total}{C_RESET}", width)
        )
        perf.append(
            pad_line(
                f"  {C_BOLD}Win Rate    :{C_RESET} {C_PRIMARY}{stats['win_rate']:.1f}%{C_RESET} ({C_GREEN}{stats['wins']}W{C_RESET}/{C_RED}{stats['losses']}L{C_RESET})",
                width,
            )
        )
        perf.append(
            pad_line(
                f"  {C_BOLD}Net Profit  :{C_RESET} {pnl_color}{sign}{net_pnl:,.2f} USDT{C_RESET}{_thb_net}",
                width,
            )
        )
        perf.append(
            pad_line(
                f"  {C_BOLD}Avg Trade   :{C_RESET} {pnl_color}{stats['avg_pnl_usdt']:+,.2f} USDT{C_RESET}{_thb_avg} ({stats['avg_pnl_pct']:+.2f}%)",
                width,
            )
        )
        perf.append(
            pad_line(
                f"  {C_BOLD}Prof. Factor:{C_RESET} {C_PRIMARY}{stats['profit_factor_str']}{C_RESET} │ {C_BOLD}Fees:{C_RESET}{C_RED}{stats['total_fees']:,.2f}{C_RESET}",
                width,
            )
        )
    else:
        perf.append(format_panel_header("PERFORMANCE SUMMARY", width, C_PRIMARY))
        perf.append(
            pad_line(
                f" Total Trades : {C_PRIMARY}{total:<5}{C_RESET} │ Win Rate: {C_PRIMARY}{stats['win_rate']:.1f}%{C_RESET}",
                width,
            )
        )
        perf.append(
            pad_line(
                f" Wins/Losses  : {C_GREEN}{stats['wins']} W{C_RESET} / {C_RED}{stats['losses']} L{C_RESET}",
                width,
            )
        )
        perf.append(
            pad_line(f" Net Profit   : {pnl_color}{sign}{net_pnl:,.4f} USDT{C_RESET}", width)
        )
        perf.append(
            pad_line(
                f" Avg Trade P&L: {pnl_color}{stats['avg_pnl_usdt']:+,.2f} USDT{C_RESET} ({stats['avg_pnl_pct']:+.2f}%)",
                width,
            )
        )
        perf.append(
            pad_line(
                f" Profit Factor: {C_PRIMARY}{stats['profit_factor_str']}{C_RESET} │ Total Fees: {C_RED}{stats['total_fees']:,.4f} USDT{C_RESET}",
                width,
            )
        )

    timeline = stats.get("timeline_str", "")
    if show_peak:
        _thb_win = ""
        _thb_dd = ""
        try:
            from xauby.utils.currency import usdt_to_thb, format_thb
            win_usdt = stats["max_win_usdt"]
            dd_usdt = stats["max_loss_usdt"]
            if win_usdt != 0:
                _thb_win = f" {C_MUTED}{format_thb(usdt_to_thb(win_usdt), compact=(width < 75))}{C_RESET}"
            if dd_usdt != 0:
                _thb_dd = f" {C_MUTED}{format_thb(usdt_to_thb(dd_usdt), compact=(width < 75))}{C_RESET}"
        except Exception:
            pass

        if width < 75:
            peak.append(format_panel_header("PEAK PERFORMANCE", width, C_PRIMARY))
            peak.append(
                pad_line(
                    f"  {C_BOLD}Max Profit  :{C_RESET} {C_GREEN}+{stats['max_win_usdt']:,.2f} USDT{C_RESET}{_thb_win} ({C_GREEN}+{stats['max_win_pct']:.1f}%{C_RESET})",
                    width,
                )
            )
            peak.append(
                pad_line(
                    f"  {C_BOLD}Max DD      :{C_RESET} {C_RED}{stats['max_loss_usdt']:,.2f} USDT{C_RESET}{_thb_dd} ({C_RED}{stats['max_loss_pct']:.1f}%{C_RESET})",
                    width,
                )
            )
            peak.append(
                pad_line(
                    f"  {C_BOLD}Avg Duration:{C_RESET} {C_PRIMARY}{stats['avg_duration_str']}{C_RESET}",
                    width,
                )
            )
        else:
            peak.append(format_panel_header("PEAK PERFORMANCE", width, C_PRIMARY))
            peak.append(
                pad_line(
                    f" Max Profit   : {C_GREEN}+{stats['max_win_usdt']:,.2f} USDT{C_RESET}{_thb_win} ({C_GREEN}+{stats['max_win_pct']:.2f}%{C_RESET})",
                    width,
                )
            )
            peak.append(
                pad_line(
                    f" Max Drawdown : {C_RED}{stats['max_loss_usdt']:,.2f} USDT{C_RESET}{_thb_dd} ({C_RED}{stats['max_loss_pct']:.2f}%{C_RESET})",
                    width,
                )
            )
            peak.append(
                pad_line(f" Avg Duration : {C_PRIMARY}{stats['avg_duration_str']}{C_RESET}", width)
            )
            
    if show_timeline:
        perf.append(pad_line(f" Timeline: {timeline}", width))
    return perf, peak, timeline



_TRADELOG_VM_CACHE: Dict[tuple, TradeLogViewModel] = {}
_TRADELOG_VM_CACHE_LOCK = threading.Lock()


def build_tradelog_view_model(
    db: IDatabaseRepository,
    state: Dict[str, Any],
    width: int,
    height: int,
    *,
    focus_symbol: Optional[str] = None,
    is_multi: bool = False,
    trade_filter: str = "all",
) -> TradeLogViewModel:
    global _TRADELOG_VM_CACHE
    raw_symbol = (focus_symbol or state.get("symbol") or default_symbol_from_whitelist()).upper().replace("_", "")
    all_scope = bool(is_multi or raw_symbol in {"ALL", "*"})
    symbol = "ALL" if all_scope else raw_symbol

    # Get the latest trade to detect changes
    latest_trade = db.get_closed_trades(None if all_scope else symbol, limit=1)
    latest_trade_time = latest_trade[0].get("closed_at", "") if latest_trade else ""

    # Get the total count of closed trades for this symbol
    count = 0
    conn = None
    try:
        conn = db._get_connection()
        cursor = conn.cursor()
        if all_scope:
            cursor.execute("SELECT COUNT(*) FROM closed_trades")
        else:
            cursor.execute("SELECT COUNT(*) FROM closed_trades WHERE symbol = ?", (symbol,))
        count = cursor.fetchone()[0]
    except Exception:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    filter_mode = (trade_filter or "all").lower()
    if filter_mode not in TRADE_FILTERS:
        filter_mode = "all"
    metric_ctx = state.get("metrics_context") or {}
    initial_balance = float(metric_ctx.get("initial_balance") or 1000.0)
    cache_key = (
        symbol, width, height, latest_trade_time, count,
        bool(state.get("simulate_only")), filter_mode, initial_balance,
    )
    with _TRADELOG_VM_CACHE_LOCK:
        if cache_key in _TRADELOG_VM_CACHE:
            return copy.deepcopy(_TRADELOG_VM_CACHE[cache_key])

    all_trades = db.get_closed_trades(None if all_scope else symbol, limit=1000)
    trades = _apply_trade_filter(all_trades, filter_mode)
    
    from xauby.track_record.generator import _parse_date
    for t in trades:
        t["closed_at_dt"] = _parse_date(t.get("closed_at"))
        t["opened_at_dt"] = _parse_date(t.get("opened_at"))

    from xauby.analytics.calculator import trade_span_years
    metrics = calculate_metrics(
        trades,
        initial_balance=initial_balance,
        years=trade_span_years(trades),
    )
    reg = state.get("regime") or dict(DEFAULT_REGIME)
    profile = get_layout_profile(max(38, width))
    body_h = max(12, height - 10)
    layout = _compute_tradelog_layout(body_h, profile.is_phone, profile.is_wide, len(trades))

    show_analytics = layout["show_analytics"]
    show_track = layout["show_track"]
    is_phone = profile.is_phone

    if is_phone:
        regime_lines = [f" [1] MARKET REGIME & SENTIMENT", *format_regime_compact_lines(reg)]
    else:
        regime_lines = [
            f" [1] MARKET REGIME & SENTIMENT",
            *format_regime_user_lines(reg, narrow_bars=width < 90),
        ]
    analytics_lines = (
        render_advanced_analytics_lines(metrics, is_phone) if show_analytics else []
    )
    if not is_phone:
        try:
            from xauby.ui.state_view import format_regime_history_lines

            hist = format_regime_history_lines(db, symbol, limit=5, width=max(38, width) - 2)
            if len(hist) > 1:
                regime_lines = list(regime_lines) + [""] + hist
        except Exception:
            pass
    track_lines = (
        render_track_record_lines(
            trades, is_phone, initial_balance=initial_balance
        )
        if show_track else []
    )

    vm = TradeLogViewModel(
        symbol=symbol,
        trades=trades,
        metrics=metrics,
        stats={},
        regime=reg,
        layout=layout,
        profile=profile,
        regime_lines=regime_lines,
        analytics_lines=analytics_lines,
        track_lines=track_lines,
        is_wide=profile.is_wide and width >= 110,
        total_trades=len(trades),
        visible_trades=len(trades),
        scope_label="All pairs" if all_scope else symbol,
        filter_label=_filter_label(filter_mode),
    )

    col_w = max(30, (width - 7) // 2) if vm.is_wide else width - 4
    if not trades:
        vm.performance_lines = [
            format_panel_header("PERFORMANCE SUMMARY", col_w, C_PRIMARY),
            pad_line(f"  {C_MUTED}No closed trades for {symbol} yet.{C_RESET}", col_w),
        ]
        with _TRADELOG_VM_CACHE_LOCK:
            _TRADELOG_VM_CACHE[cache_key] = copy.deepcopy(vm)
        return vm

    stats = compute_trade_stats(trades)
    vm.stats = stats
    max_trades = layout["max_trades"]
    vm.ansi_trade_rows = _build_trade_rows(trades, max_trades, width)
    vm.table_rows = _trade_table_rows(trades, max_trades, width)
    vm.trade_card_lines = _build_trade_card_lines(trades, max_trades, col_w)

    perf, peak, _ = _build_performance_lines(
        stats,
        len(trades),
        col_w,
        show_peak=layout["show_peak"],
        show_timeline=layout["show_timeline"],
    )
    split_lines = _sim_live_split_lines(trades, col_w)
    if split_lines:
        perf.extend(split_lines)
    vm.performance_lines = perf
    vm.peak_lines = peak
    vm.timeline_line = stats.get("timeline_str", "")

    if layout["show_curve"]:
        chart_w = col_w - 18
        if chart_w >= 10:
            vm.curve_lines = list(
                get_capital_curve_lines(db, symbol, width=chart_w, height=layout["curve_h"], trades=trades)
            )

    with _TRADELOG_VM_CACHE_LOCK:
        _TRADELOG_VM_CACHE[cache_key] = copy.deepcopy(vm)
    return vm
