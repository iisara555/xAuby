from typing import Any, Dict, List

from xauby.ui.widgets import (
    print_row,
    format_panel_divider,
    format_pnl,
    format_pnl_compact,
    to_compact,
    print_regime_section,
    regime_section_line_count,
    render_advanced_analytics_lines,
    advanced_analytics_line_count,
    render_track_record_lines,
    track_record_line_count,
)
from xauby.utils.colors import (
    C_RESET,
    RESET,
    C_MUTED,
)
from xauby.utils.common import format_to_ict


def _footer_text(W: int) -> str:
    if W < 75:
        return " [1/d] Dash │ [2/t] Log & Intel │ [4/i] Inc │ [3] Ret │ Ctrl+C Exit"
    return (
        " [1/d] Dashboard View │ [2/t] Log & Intel │ [4/i] Incidents"
        " │ [3] Return to Launcher │ Ctrl+C Force Exit"
    )


def _gold_section_rows(is_phone: bool, show_analytics: bool, show_track: bool) -> int:
    rows = regime_section_line_count(is_phone) + 1
    if show_analytics:
        rows += advanced_analytics_line_count(is_phone) + 1
    if show_track:
        rows += track_record_line_count(is_phone) + 1
    return rows


def _perf_section_rows(
    show_peak: bool,
    show_timeline: bool,
    show_curve: bool,
    curve_h: int,
    max_trades: int,
) -> int:
    rows = 6
    if show_peak:
        rows += 6
    if show_timeline:
        rows += 3
    if show_curve:
        rows += 2 + curve_h
    rows += 2 + max_trades
    return rows


def _compute_tradelog_layout(body_h: int, is_phone: bool, is_wide: bool, trade_count: int) -> Dict[str, Any]:
    show_analytics = True
    show_track = True
    show_peak = True
    show_timeline = True
    show_curve = trade_count >= 2
    curve_h = 6
    max_trades = 10

    def gold_rows() -> int:
        return _gold_section_rows(is_phone, show_analytics, show_track)

    def perf_rows() -> int:
        return _perf_section_rows(show_peak, show_timeline, show_curve, curve_h, max_trades)

    def total_rows() -> int:
        if is_wide:
            left_len = 6
            if show_peak:
                left_len += 5
            if show_timeline:
                left_len += 3
            if show_curve:
                left_len += 2 + curve_h
            right_len = 2 + max_trades
            return gold_rows() + max(left_len, right_len)
        return gold_rows() + perf_rows()

    for stage in range(12):
        if total_rows() <= body_h:
            break
        if stage == 0:
            show_track = False
        elif stage == 1:
            show_analytics = False
        elif stage == 2:
            if show_curve and curve_h > 4:
                curve_h = 4
            else:
                show_curve = False
        elif stage == 3:
            show_peak = False
        elif stage == 4:
            show_timeline = False
        else:
            max_trades = max(2, max_trades - 1)

    return {
        "show_analytics": show_analytics,
        "show_track": show_track,
        "show_peak": show_peak,
        "show_timeline": show_timeline,
        "show_curve": show_curve,
        "curve_h": curve_h,
        "max_trades": max_trades,
    }


def _print_gold_sections(
    reg: Dict[str, Any],
    trades: List[Dict[str, Any]],
    metrics: Any,
    W: int,
    is_phone: bool,
    border_mid: str,
    border_color: str,
    show_analytics: bool,
    show_track: bool,
) -> None:
    print_regime_section(reg, W, is_phone, border_color)
    print(f"{border_color}{border_mid}{RESET}")
    if show_analytics:
        for line in render_advanced_analytics_lines(metrics, is_phone):
            print_row(line, W, border_color=border_color)
        print(f"{border_color}{border_mid}{RESET}")
    if show_track:
        for line in render_track_record_lines(trades, is_phone):
            print_row(line, W, border_color=border_color)
        print(f"{border_color}{border_mid}{RESET}")


def _build_trade_rows(trades: List[Dict[str, Any]], max_trades: int, W: int) -> List[str]:
    rows: List[str] = []
    if W < 75:
        t_header = f"  {'Date (ICT)':<11} │ {'Side':<4} │ {'P&L %':<7} │ {'Trigger':<8}"
        rows.append(t_header)
        rows.append(format_panel_divider(W - 4))
        for t in trades[:max_trades]:
            dt = format_to_ict(t.get("closed_at", ""))
            side = t.get("side", "BUY")
            pnl_val = float(t.get("net_pnl") or 0.0)
            pnl_pct_val = float(t.get("net_pnl_pct") or 0.0)
            pnl_pct_str = format_pnl_compact(pnl_val, pnl_pct_val)
            trigger = (t.get("trigger") or "N/A")[:8]
            rows.append(
                f"  {dt:<11} │ {side:<4} │ {pnl_pct_str:<7} │ {C_MUTED}{trigger:<8}{C_RESET}"
            )
    elif W >= 110:
        t_header = (
            f"  {'Date (ICT)':<13} │ {'Side':<4} │ {'Size':<6} │ "
            f"{'Prices (Ent->Ext)':<15} │ {'Net P&L (USDT)':<20}"
        )
        rows.append(t_header)
        rows.append(format_panel_divider(W - 4))
        for t in trades[:max_trades]:
            dt = format_to_ict(t.get("closed_at", ""))
            side = t.get("side", "BUY")
            amount_val = float(t.get("amount") or 0.0)
            size = f"{amount_val:,.3f}"[:6]
            entry = float(t.get("entry_price") or 0.0)
            exit_p = float(t.get("exit_price") or 0.0)
            prices = f"{to_compact(entry)}->{to_compact(exit_p)}"[:15]
            pnl_val = float(t.get("net_pnl") or 0.0)
            pnl_pct_val = float(t.get("net_pnl_pct") or 0.0)
            pnl_str = format_pnl(pnl_val, pnl_pct_val)
            rows.append(f"  {dt:<13} │ {side:<4} │ {size:<6} │ {prices:<15} │ {pnl_str}")
    else:
        t_header = f"  {'Date (ICT)':<13} │ {'Side':<4} │ {'Size':<6} │ {'Prices (Ent->Ext)':<15} │ {'Net P&L':<20}"
        rows.append(t_header)
        rows.append(format_panel_divider(W - 4))
        for t in trades[:max_trades]:
            dt = format_to_ict(t.get("closed_at", ""))
            side = t.get("side", "BUY")
            amount_val = float(t.get("amount") or 0.0)
            size = f"{amount_val:,.3f}"[:6]
            entry = float(t.get("entry_price") or 0.0)
            exit_p = float(t.get("exit_price") or 0.0)
            prices = f"{to_compact(entry)}->{to_compact(exit_p)}"[:15]
            pnl_val = float(t.get("net_pnl") or 0.0)
            pnl_pct_val = float(t.get("net_pnl_pct") or 0.0)
            pnl_str = format_pnl(pnl_val, pnl_pct_val)
            rows.append(f"  {dt:<13} │ {side:<4} │ {size:<6} │ {prices:<15} │ {pnl_str}")
    return rows


