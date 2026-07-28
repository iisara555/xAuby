from typing import Dict, Any, List
from datetime import datetime

from xauby.utils.colors import (
    C_RESET, C_BOLD, C_PRIMARY, C_MUTED, C_DARK, C_GEMINI_CYAN,
    C_GREEN, C_RED, C_YELLOW, C_BLUE,
    C_BG_GREEN, C_BG_RED, C_BG_YELLOW, C_BG_INDIGO,
    C_BG_CYAN, C_BG_DARK, C_BG_ORANGE, RESET, BOLD, GREEN, RED, YELLOW,
    BLUE, WHITE, BB_CYAN, make_gemini_gradient
)
from xauby.utils.common import visible_len

def _state_symbol(state: Dict[str, Any]) -> str:
    sym = str(state.get("symbol") or "").strip()
    if sym:
        return sym
    try:
        from xauby.runtime.symbol_resolver import focus_symbol_from_config

        return focus_symbol_from_config()
    except Exception:
        return ""


def print_row(text: str, width: int, border_color: str = BB_CYAN, bg_style: str = "") -> None:
    """Prints a row centered within solid TUI border frames, padding with spaces."""
    vis = visible_len(text)
    content_width = width - 4
    if vis < content_width:
        padding = " " * (content_width - vis)
        if bg_style:
            print(f"{border_color}│{RESET} {bg_style}{text}{padding}{RESET}{border_color}│{RESET}")
        else:
            print(f"{border_color}│{RESET} {text}{padding}{border_color}│{RESET}")
    else:
        if bg_style:
            print(f"{border_color}│{RESET} {bg_style}{text}{RESET}{border_color}│{RESET}")
        else:
            print(f"{border_color}│{RESET} {text}{border_color}│{RESET}")

def get_panel_column_widths(W: int) -> tuple[int, int]:
    """Left/right inner column widths for wide two-panel layout."""
    right_w = 80
    left_w = W - 7 - right_w
    if left_w < 45:
        left_w = 45
        right_w = max(30, W - 7 - left_w)
    return left_w, right_w

def pad_line(text: str, width: int) -> str:
    """Pads text with spaces to match the visual width."""
    vis = visible_len(text)
    if vis < width:
        return text + " " * (width - vis)
    return text

def format_panel_header(title: str, width: int, color: str = C_PRIMARY) -> str:
    """Creates a panel header divider like ─── TITLE ─────────────────────"""
    title_str = f" {title} "
    vis = visible_len(title_str)
    if vis >= width:
        return title_str[:width]
    left_dashes = 3
    right_dashes = width - vis - left_dashes
    if right_dashes < 0:
        left_dashes = 1
        right_dashes = width - vis - left_dashes
    if right_dashes < 0:
        return title_str[:width]
    return f"{C_DARK}{'─' * left_dashes}{RESET}{color}{BOLD}{title_str}{RESET}{C_DARK}{'─' * right_dashes}{RESET}"

def format_panel_divider(width: int) -> str:
    """Creates a line of dashes of specified visual width."""
    return f"{C_DARK}{'─' * width}{RESET}"

def get_zone_colored(zone: str) -> str:
    zone = zone.upper()
    if zone == "GREEN":
        return f"{GREEN}{BOLD}GREEN{RESET}"
    elif zone == "RED":
        return f"{RED}{BOLD}RED{RESET}"
    elif zone == "BLUE":
        return f"{BLUE}{BOLD}BLUE{RESET}"
    elif zone == "YELLOW":
        return f"{YELLOW}{BOLD}YELLOW{RESET}"
    return f"{BOLD}{WHITE}{zone}{RESET}"

def get_quote_asset(symbol_or_state: Any) -> str:
    if not symbol_or_state:
        return "USDT"
    if isinstance(symbol_or_state, dict):
        symbol = symbol_or_state.get("symbol") or symbol_or_state.get("focus_symbol")
    else:
        symbol = symbol_or_state

    if not symbol:
        return "USDT"

    symbol = str(symbol).upper().replace("_", "")
    # Check common quote assets
    for q in ("USDT", "USD", "THB", "BTC", "ETH"):
        if symbol.endswith(q):
            return q
    # Default fallback
    if len(symbol) >= 7 and symbol[-4:] == "USDT":
        return "USDT"
    return symbol[-3:] if len(symbol) >= 5 else "USDT"


def format_pnl(pnl: float, pct: float, currency: str = "USDT") -> str:
    if pnl > 0:
        return f"{C_GREEN}+{pnl:,.4f} {currency} ({pct:+.2f}%){C_RESET}"
    elif pnl < 0:
        return f"{C_RED}{pnl:,.4f} {currency} ({pct:+.2f}%){C_RESET}"
    return f"{C_PRIMARY}0.0000 {currency} (0.00%){C_RESET}"

def format_pnl_compact(pnl: float, pct: float) -> str:
    if pnl > 0:
        return f"{C_GREEN}+{pct:.2f}%{C_RESET}"
    elif pnl < 0:
        return f"{C_RED}{pct:.2f}%{C_RESET}"
    return f"{C_PRIMARY}0.00%{C_RESET}"

def to_compact(val: float) -> str:
    """Format a price value compactly (e.g. 4500.0 -> '4.5k')."""
    if val >= 1000:
        return f"{val/1000:.1f}k"
    return f"{val:.1f}"

def make_zone_badge(tf: str, zone: str) -> tuple[str, str]:
    """Return (badge, compact) for a given timeframe and CDC zone."""
    ZONE_STYLES = {
        "GREEN":  ("BULL",  C_BG_GREEN,  C_GREEN),
        "BLUE":   ("PBUY2", C_BG_INDIGO, C_BLUE),
        "LBLUE":  ("PBUY1", C_BG_CYAN,   C_GEMINI_CYAN),
        "RED":    ("BEAR",  C_BG_RED,    C_RED),
        "ORANGE": ("PSELL2", C_BG_ORANGE, C_YELLOW),
        "YELLOW": ("PSELL1", C_BG_YELLOW, C_YELLOW),
    }
    # Special overrides for 4H labels
    TF_LABEL_OVERRIDE = {"GREEN": {"4H": "BUY"}, "RED": {"4H": "SELL"}}

    zone_upper = zone.upper() if zone else "UNKNOWN"
    if zone_upper in ("DISABLED", "OFF", "N/A"):
        return f"{C_BG_DARK} {tf}: OFF {C_RESET}", f"{C_MUTED}{tf}: OFF{C_RESET}"

    style = ZONE_STYLES.get(zone_upper)
    if not style:
        return f"{C_BG_DARK} {tf}: UNK {C_RESET}", f"{C_PRIMARY}{tf}: UNK{C_RESET}"

    label, bg, fg = style
    label = TF_LABEL_OVERRIDE.get(zone_upper, {}).get(tf, label)
    return f"{bg} {tf}: {label} {C_RESET}", f"{fg}{tf}: {label}{C_RESET}"

def compute_trade_stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculates all trade performance metrics and returns them as a dictionary."""
    total = len(trades)
    if total == 0:
        return {
            "total": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "net_pnl": 0.0,
            "avg_pnl_pct": 0.0,
            "avg_pnl_usdt": 0.0,
            "gross_profits": 0.0,
            "gross_losses": 0.0,
            "profit_factor_str": "0.00x",
            "max_win_usdt": 0.0,
            "max_win_pct": 0.0,
            "max_loss_usdt": 0.0,
            "max_loss_pct": 0.0,
            "avg_duration_str": "N/A",
            "total_fees": 0.0,
            "timeline_str": ""
        }

    wins = 0
    losses = 0
    net_pnl = 0.0
    gross_profits = 0.0
    gross_losses = 0.0
    pnl_pcts = []
    
    max_win_usdt = 0.0
    max_win_pct = 0.0
    max_loss_usdt = 0.0
    max_loss_pct = 0.0
    
    total_duration_sec = 0.0
    duration_count = 0
    total_fees = 0.0
    
    for t in trades:
        pnl_val = t.get("net_pnl")
        pnl = float(pnl_val) if pnl_val is not None else 0.0
        pct_val = t.get("net_pnl_pct")
        pct = float(pct_val) if pct_val is not None else 0.0
        fee_val = t.get("total_fees")
        fee = float(fee_val) if fee_val is not None else 0.0
        net_pnl += pnl
        total_fees += fee
        pnl_pcts.append(pct)
        
        if pnl > 0:
            wins += 1
            gross_profits += pnl
            if pnl > max_win_usdt:
                max_win_usdt = pnl
            if pct > max_win_pct:
                max_win_pct = pct
        else:
            losses += 1
            gross_losses += abs(pnl)
            if pnl < max_loss_usdt:
                max_loss_usdt = pnl
            if pct < max_loss_pct:
                max_loss_pct = pct
                
        opened_at_str = t.get("opened_at")
        closed_at_str = t.get("closed_at")
        if opened_at_str and closed_at_str:
            try:
                op_str = opened_at_str.replace("Z", "").replace("T", " ")[:19]
                cl_str = closed_at_str.replace("Z", "").replace("T", " ")[:19]
                t_open = datetime.strptime(op_str, "%Y-%m-%d %H:%M:%S")
                t_close = datetime.strptime(cl_str, "%Y-%m-%d %H:%M:%S")
                total_duration_sec += (t_close - t_open).total_seconds()
                duration_count += 1
            except Exception:
                pass
                
    win_rate = (wins / total) * 100.0
    avg_pnl_pct = sum(pnl_pcts) / total
    avg_pnl_usdt = net_pnl / total
    
    if gross_losses > 0:
        profit_factor = gross_profits / gross_losses
        pf_str = f"{profit_factor:.2f}x"
    else:
        pf_str = "∞" if gross_profits > 0 else "0.00x"
        
    avg_duration_str = "N/A"
    if duration_count > 0:
        avg_sec = total_duration_sec / duration_count
        hours, remainder = divmod(avg_sec, 3600)
        minutes, _ = divmod(remainder, 60)
        if avg_sec >= 86400:
            days = avg_sec / 86400
            avg_duration_str = f"{days:.1f} days"
        else:
            avg_duration_str = f"{int(hours)}h {int(minutes)}m"

    timeline_items = []
    recent_trades = trades[:10]
    for rt in reversed(recent_trades):
        rpnl_val = rt.get("net_pnl")
        rpnl = float(rpnl_val) if rpnl_val is not None else 0.0
        rpct_val = rt.get("net_pnl_pct")
        rpct = float(rpct_val) if rpct_val is not None else 0.0
        rcolor = GREEN if rpnl > 0 else RED
        rsign = "+" if rpnl > 0 else ""
        timeline_items.append(f"{rcolor}[{rsign}{rpct:.1f}%]{RESET}")
    timeline_str = " ".join(timeline_items)

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "net_pnl": net_pnl,
        "avg_pnl_pct": avg_pnl_pct,
        "avg_pnl_usdt": avg_pnl_usdt,
        "gross_profits": gross_profits,
        "gross_losses": gross_losses,
        "profit_factor_str": pf_str,
        "max_win_usdt": max_win_usdt,
        "max_win_pct": max_win_pct,
        "max_loss_usdt": max_loss_usdt,
        "max_loss_pct": max_loss_pct,
        "avg_duration_str": avg_duration_str,
        "total_fees": total_fees,
        "timeline_str": timeline_str
    }

def draw_analytics_view(db, state, W, is_mobile, border_color):
    from xauby.analytics.calculator import calculate_metrics, trade_span_years
    symbol = _state_symbol(state)
    trades = db.get_closed_trades(symbol, limit=1000)
    metric_ctx = state.get("metrics_context") or {}
    initial_balance = float(metric_ctx.get("initial_balance") or 1000.0)
    metrics = calculate_metrics(
        trades,
        initial_balance=initial_balance,
        years=trade_span_years(trades),
    )

    border_top = "┌" + "─" * (W - 2) + "┐"
    border_mid = "├" + "─" * (W - 2) + "┤"
    print("\033[H", end="")
    print(f"{border_color}{border_top}{RESET}")
    print_row(f"{make_gemini_gradient('✦ PERFORMANCE ANALYTICS ✦')} for {symbol}", W, border_color=border_color)
    print(f"{border_color}{border_mid}{RESET}")

    if is_mobile:
        print_row(f"  {C_MUTED}Total Returns : {C_PRIMARY}{metrics.total_return_pct:+.2f}%{C_RESET}", W, border_color=border_color)
        print_row(f"  {C_MUTED}Net PnL       : {C_GREEN if metrics.net_pnl >= 0 else C_RED}{metrics.net_pnl:+.4f} USDT{C_RESET}", W, border_color=border_color)
        print_row(f"  {C_MUTED}Win Rate      : {C_PRIMARY}{metrics.win_rate:.1f}% ({metrics.consecutive_wins} wins max){C_RESET}", W, border_color=border_color)
        print_row(f"  {C_MUTED}Profit Factor : {C_PRIMARY}{metrics.profit_factor:.2f}x{C_RESET}", W, border_color=border_color)
        print_row(f"  {C_MUTED}Sharpe Ratio  : {C_PRIMARY}{metrics.sharpe_ratio:.2f}{C_RESET}", W, border_color=border_color)
        print_row(f"  {C_MUTED}Max Drawdown  : {C_RED}{metrics.max_drawdown_pct:.2f}%{C_RESET}", W, border_color=border_color)
        excursion_text = (
            f"{metrics.average_mae_pct:.2f}% / {metrics.average_mfe_pct:.2f}% "
            f"({metrics.excursion_coverage_pct:.0f}% covered)"
            if metrics.excursion_coverage_pct > 0 else "collecting (0% covered)"
        )
        print_row(
            f"  {C_MUTED}Avg MAE / MFE : {C_PRIMARY}{excursion_text}{C_RESET}",
            W,
            border_color=border_color,
        )
    else:
        col1 = f"  {C_MUTED}Net PnL      : {C_GREEN if metrics.net_pnl >= 0 else C_RED}{metrics.net_pnl:+.4f} USDT{C_RESET}   │ {C_MUTED}Sharpe Ratio : {C_PRIMARY}{metrics.sharpe_ratio:.2f}{C_RESET}"
        col2 = f"  {C_MUTED}Total Return : {C_PRIMARY}{metrics.total_return_pct:+.2f}%{C_RESET}            │ {C_MUTED}Sortino Ratio: {C_PRIMARY}{metrics.sortino_ratio:.2f}{C_RESET}"
        col3 = f"  {C_MUTED}Win Rate     : {C_PRIMARY}{metrics.win_rate:.1f}%{C_RESET}                    │ {C_MUTED}Profit Factor: {C_PRIMARY}{metrics.profit_factor:.2f}x{C_RESET}"
        col4 = f"  {C_MUTED}Max Drawdown : {C_RED}{metrics.max_drawdown_pct:.2f}%{C_RESET}                   │ {C_MUTED}Risk Reward  : {C_PRIMARY}{metrics.risk_reward_ratio:.2f}{C_RESET}"
        col5 = f"  {C_MUTED}Avg Win/Loss : {C_GREEN}{metrics.avg_win:.2f}{C_RESET}/{C_RED}{metrics.avg_loss:.2f}{C_RESET}         │ {C_MUTED}Expectancy   : {C_PRIMARY}{metrics.expectancy:.4f}{C_RESET}"
        col6 = f"  {C_MUTED}Wins/Losses  : {C_GREEN}{metrics.consecutive_wins} consecutive{C_RESET}      │ {C_MUTED}Loss Streak  : {C_RED}{metrics.consecutive_losses} consecutive{C_RESET}"
        
        print_row(col1, W, border_color=border_color)
        print_row(col2, W, border_color=border_color)
        print_row(col3, W, border_color=border_color)
        print_row(col4, W, border_color=border_color)
        print_row(col5, W, border_color=border_color)
        print_row(col6, W, border_color=border_color)
        excursion_text = (
            f"{metrics.average_mae_pct:.2f}% / {metrics.average_mfe_pct:.2f}%"
            if metrics.excursion_coverage_pct > 0 else "collecting"
        )
        print_row(
            f"  {C_MUTED}Avg MAE / MFE: {C_PRIMARY}{excursion_text}{C_RESET}              │ "
            f"{C_MUTED}Coverage: {C_PRIMARY}{metrics.excursion_coverage_pct:.0f}%{C_RESET}",
            W,
            border_color=border_color,
        )

    print(f"{border_color}{border_mid}{RESET}")

DEFAULT_REGIME: Dict[str, Any] = {
    "regime": "UNKNOWN",
    "trend": "NEUTRAL",
    "volatility": "NORMAL",
    "macro_bias": "NEUTRAL",
    "confidence": 0.5,
    "gold_score": 50,
    "reasons": [],
    "phase": "UNKNOWN",
    "risk_state": "NORMAL",
    "trend_strength": "NEUTRAL",
    "volatility_state": "NORMAL",
    "liquidity_state": "NORMAL",
    "transition_risk": "LOW",
    "strategy_bias": {},
    "features": {},
}


def _regime_display_color(regime_name: str) -> str:
    name = (regime_name or "").upper()
    if "BULL" in name or "BREAKOUT" in name or name == "RISK-ON" or "ACCUMULATION" in name:
        return C_GREEN
    if "BEAR" in name or "PANIC" in name or "BREAKDOWN" in name or name == "RISK-OFF":
        return C_RED
    if "VOLATILITY" in name:
        return C_YELLOW
    return C_YELLOW


def _reason_short_label(label: str) -> str:
    if label.startswith("DXY"):
        return "DXY"
    if "Inflation" in label:
        return "Inflation"
    if label.startswith("Trend"):
        return "Trend"
    if label.startswith("Volume"):
        return "Volume"
    return label.split()[0] if label else ""


def _format_regime_detail_line(reg: Dict[str, Any], *, compact: bool = False) -> str:
    phase = str(reg.get("phase") or "UNKNOWN").replace("_", " ")
    risk_state = str(reg.get("risk_state") or "NORMAL").replace("_", " ")
    trend_strength = str(reg.get("trend_strength") or "NEUTRAL").replace("_", " ")
    vol_state = str(reg.get("volatility_state") or reg.get("volatility") or "NORMAL").replace("_", " ")
    transition = str(reg.get("transition_risk") or "LOW").replace("_", " ")
    risk_color = C_RED if "PANIC" in risk_state or "OFF" in risk_state else (C_YELLOW if "CHOP" in risk_state or "QUIET" in risk_state else C_GREEN)
    trans_color = C_RED if transition in ("HIGH", "EXTREME") else (C_YELLOW if transition == "MEDIUM" else C_GREEN)
    if compact:
        return (
            f"  {C_BOLD}Phase:{C_RESET}{C_PRIMARY}{phase[:12]}{C_RESET} "
            f"{C_MUTED}|{C_RESET} {C_BOLD}Risk:{C_RESET}{risk_color}{risk_state[:12]}{C_RESET} "
            f"{C_MUTED}|{C_RESET} {C_BOLD}Tr:{C_RESET}{trans_color}{transition}{C_RESET}"
        )
    return (
        f"  {C_BOLD}Phase:{C_RESET} {C_PRIMARY}{phase}{C_RESET} "
        f"{C_MUTED}|{C_RESET} {C_BOLD}Risk:{C_RESET} {risk_color}{risk_state}{C_RESET} "
        f"{C_MUTED}|{C_RESET} {C_BOLD}Trend:{C_RESET} {C_PRIMARY}{trend_strength}{C_RESET} "
        f"{C_MUTED}|{C_RESET} {C_BOLD}Vol:{C_RESET} {C_PRIMARY}{vol_state}{C_RESET} "
        f"{C_MUTED}|{C_RESET} {C_BOLD}Transition:{C_RESET} {trans_color}{transition}{C_RESET}"
    )


def _format_strategy_bias_line(reg: Dict[str, Any], *, compact: bool = False) -> str:
    bias = reg.get("strategy_bias") or {}
    family = str(bias.get("family") or "manual_review").replace("_", " ")
    posture = str(bias.get("posture") or "neutral").replace("_", " ")
    preferred = bias.get("preferred") or []
    preferred_text = ", ".join(str(x).replace("_", " ") for x in preferred[:2]) if preferred else "N/A"
    allowed = bias.get("allowed_actions") or []
    allowed_text = "/".join(str(x) for x in allowed[:3]) if allowed else "N/A"
    if compact:
        return (
            f"  {C_BOLD}Bias:{C_RESET}{C_PRIMARY}{family[:18]}{C_RESET} "
            f"{C_MUTED}|{C_RESET} {C_BOLD}Act:{C_RESET}{C_PRIMARY}{allowed_text}{C_RESET}"
        )
    return (
        f"  {C_BOLD}Strategy Bias:{C_RESET} {C_PRIMARY}{family}{C_RESET} "
        f"{C_MUTED}|{C_RESET} {C_BOLD}Posture:{C_RESET} {C_PRIMARY}{posture}{C_RESET} "
        f"{C_MUTED}|{C_RESET} {C_BOLD}Prefer:{C_RESET} {C_PRIMARY}{preferred_text}{C_RESET} "
        f"{C_MUTED}|{C_RESET} {C_BOLD}Allowed:{C_RESET} {C_PRIMARY}{allowed_text}{C_RESET}"
    )


def format_regime_detail_lines(reg: Dict[str, Any], *, compact: bool = False) -> List[str]:
    """Human-readable detailed regime lines for dashboards and future routers."""
    return [
        _format_regime_detail_line(reg, compact=compact),
        _format_strategy_bias_line(reg, compact=compact),
    ]


def format_regime_user_lines(reg: Dict[str, Any], *, narrow_bars: bool = False) -> List[str]:
    """Return regime panel lines for wide-column rendering (no print_row)."""
    regime_name = reg.get("regime", "UNKNOWN").upper()
    reg_color = _regime_display_color(regime_name)
    conf = float(reg.get("confidence", 0.5))
    gold_score = int(reg.get("gold_score", round(conf * 100)))
    reasons = reg.get("reasons") or []
    conf_bar_w = 12 if narrow_bars else 20
    filled = int(round(conf * conf_bar_w))
    bar = f"{C_GREEN}{'█' * filled}{C_MUTED}{'░' * (conf_bar_w - filled)}{C_RESET}"
    lines = [
        f"  {C_BOLD}Regime:{RESET} {reg_color}{regime_name}{C_RESET}",
        f"  {C_BOLD}Confidence:{RESET} {C_PRIMARY}{conf * 100:.0f}%{C_RESET} [{bar}]",
        f"  {C_BOLD}Regime Score:{RESET} {C_PRIMARY}{gold_score}/100{C_RESET}",
        *format_regime_detail_lines(reg, compact=False),
        f"  {C_BOLD}Reason:{RESET}",
    ]
    for item in reasons:
        label = item.get("label", "")
        supportive = bool(item.get("supportive"))
        mark = "✓" if supportive else "✗"
        mark_color = C_GREEN if supportive else C_RED
        lines.append(f"    {mark_color}{mark}{C_RESET} {label}")
    return lines


_SPARK_CHARS = "▁▂▃▄▅▆▇█"


def sparkline(values: List[float], width: int = 8) -> str:
    """Mini trend sparkline from numeric series (▁▂▃▄▅▆▇█)."""
    if not values:
        return _SPARK_CHARS[0] * max(1, width)
    series = [float(v) for v in values if v is not None]
    if not series:
        return _SPARK_CHARS[0] * max(1, width)
    if len(series) > width:
        series = series[-width:]
    lo = min(series)
    hi = max(series)
    span = hi - lo if hi > lo else 1.0
    chars = []
    for v in series:
        idx = int(round((v - lo) / span * (len(_SPARK_CHARS) - 1)))
        idx = max(0, min(len(_SPARK_CHARS) - 1, idx))
        chars.append(_SPARK_CHARS[idx])
    while len(chars) < width:
        chars.insert(0, _SPARK_CHARS[0])
    return "".join(chars[-width:])


def _truncate_ansi(text: str, max_width: int) -> str:
    """Safe ANSI escape code truncation to avoid splitting sequences and color leaks."""
    if max_width <= 0 or visible_len(text) <= max_width:
        return text
    if max_width <= 3:
        return text[:max_width]
    budget = max_width - 3
    out: List[str] = []
    used = 0
    i = 0
    while i < len(text) and used < budget:
        if text[i] == "\033":
            end = text.find("m", i)
            if end == -1:
                break
            out.append(text[i : end + 1])
            i = end + 1
            continue
        out.append(text[i])
        used += 1
        i += 1
    return "".join(out) + "\033[0m..."


def format_regime_inline_lines(
    reg: Dict[str, Any],
    guard_text: str = "",
    max_width: int = 76,
    *,
    close_text: str = "",
    risk_text: str = "",
) -> List[str]:
    """1–2 line inline regime block for tablet/desktop/phone dashboard."""
    from xauby.utils.common import visible_len

    regime_name = reg.get("regime", "UNKNOWN").upper()
    reg_color = _regime_display_color(regime_name)
    conf = float(reg.get("confidence", 0.5))
    gold_score = int(reg.get("gold_score", round(conf * 100)))
    reasons = reg.get("reasons") or []
    conf_bar_w = 8 if max_width < 60 else 10
    filled = int(round(conf * conf_bar_w))
    bar = f"{C_GREEN}{'█' * filled}{C_MUTED}{'░' * (conf_bar_w - filled)}{C_RESET}"
    line1 = (
        f"  {C_BOLD}REGIME{C_RESET} {C_MUTED}│{C_RESET} "
        f"{reg_color}{regime_name}{C_RESET} {C_MUTED}│{C_RESET} "
        f"{C_BOLD}Sc:{C_RESET}{C_PRIMARY}{gold_score}{C_RESET} {C_MUTED}│{C_RESET} "
        f"{C_BOLD}Cf:{C_RESET}{C_PRIMARY}{conf * 100:.0f}%{C_RESET}[{bar}]"
    )
    reason_parts = []
    for item in reasons:
        short = _reason_short_label(str(item.get("label", "")))
        supportive = bool(item.get("supportive"))
        mark = "✓" if supportive else "✗"
        mark_color = C_GREEN if supportive else C_RED
        reason_parts.append(f"{mark_color}{mark}{C_RESET}{short}")
    guard_part = f" {C_MUTED}│{C_RESET}{C_BOLD}G:{C_RESET}{guard_text}" if guard_text else ""
    close_part = f" {C_MUTED}│{C_RESET}{close_text}" if close_text else ""
    risk_part = f" {C_MUTED}│{C_RESET}{risk_text}" if risk_text else ""
    
    suffix = f"{guard_part}{close_part}{risk_part}"
    suffix_len = visible_len(suffix)
    prefix_base = "  "
    avail_width = max_width - suffix_len - visible_len(prefix_base)

    allowed_reasons = []
    current_reasons_len = 0
    for idx, r in enumerate(reason_parts):
        space_len = 1 if idx > 0 else 0
        r_len = visible_len(r)
        if current_reasons_len + space_len + r_len <= avail_width:
            allowed_reasons.append(r)
            current_reasons_len += space_len + r_len
        else:
            if allowed_reasons:
                if current_reasons_len + 4 <= avail_width:
                    allowed_reasons.append(f"{C_MUTED}...{C_RESET}")
            else:
                allowed_reasons.append(f"{C_MUTED}...{C_RESET}")
            break

    reasons_str = " ".join(allowed_reasons) if allowed_reasons else f"{C_MUTED}—{C_RESET}"
    line2 = f"  {reasons_str}{suffix}"

    return [
        _truncate_ansi(line1, max_width),
        _truncate_ansi(line2, max_width)
    ]


def format_regime_compact_lines(reg: Dict[str, Any]) -> List[str]:
    """Return compact regime lines (3 content lines) for phone layout."""
    regime_name = reg.get("regime", "UNKNOWN").upper()
    reg_color = _regime_display_color(regime_name)
    conf = float(reg.get("confidence", 0.5))
    gold_score = int(reg.get("gold_score", round(conf * 100)))
    reasons = reg.get("reasons") or []
    conf_bar_w = 12
    filled = int(round(conf * conf_bar_w))
    bar = f"{C_GREEN}{'█' * filled}{C_MUTED}{'░' * (conf_bar_w - filled)}{C_RESET}"
    reason_parts = []
    for item in reasons:
        short = _reason_short_label(str(item.get("label", "")))
        supportive = bool(item.get("supportive"))
        mark = "✓" if supportive else "✗"
        mark_color = C_GREEN if supportive else C_RED
        reason_parts.append(f"{mark_color}{mark}{C_RESET}{short}")
    reason_line = "  ".join(reason_parts) if reason_parts else f"{C_MUTED}—{C_RESET}"
    return [
        f"  {C_BOLD}Regime:{RESET} {reg_color}{regime_name}{C_RESET}",
        f"  {C_BOLD}Score:{RESET} {C_PRIMARY}{gold_score}/100{C_RESET} │ {C_BOLD}Conf:{RESET} {C_PRIMARY}{conf * 100:.0f}%{C_RESET}",
        *format_regime_detail_lines(reg, compact=True),
        f"  {reason_line}",
    ]


def draw_regime_user_panel(reg: Dict[str, Any], W: int, is_mobile: bool, border_color: str) -> None:
    """User-friendly regime block: Regime, Confidence, regime score, and Reason checklist."""
    regime_name = reg.get("regime", "UNKNOWN").upper()
    reg_color = _regime_display_color(regime_name)
    conf = float(reg.get("confidence", 0.5))
    gold_score = int(reg.get("gold_score", round(conf * 100)))
    reasons = reg.get("reasons") or []

    conf_bar_w = 12 if is_mobile else 20
    filled = int(round(conf * conf_bar_w))
    bar = f"{C_GREEN}{'█' * filled}{C_MUTED}{'░' * (conf_bar_w - filled)}{C_RESET}"

    print_row(f"  {C_BOLD}Regime:{RESET} {reg_color}{regime_name}{C_RESET}", W, border_color=border_color)
    print_row(
        f"  {C_BOLD}Confidence:{RESET} {C_PRIMARY}{conf * 100:.0f}%{C_RESET} [{bar}]",
        W,
        border_color=border_color,
    )
    print_row(f"  {C_BOLD}Regime Score:{RESET} {C_PRIMARY}{gold_score}/100{C_RESET}", W, border_color=border_color)
    for line in format_regime_detail_lines(reg, compact=is_mobile):
        print_row(line, W, border_color=border_color)
    print_row(f"  {C_BOLD}Reason:{RESET}", W, border_color=border_color)
    for item in reasons:
        label = item.get("label", "")
        supportive = bool(item.get("supportive"))
        mark = "✓" if supportive else "✗"
        mark_color = C_GREEN if supportive else C_RED
        print_row(f"    {mark_color}{mark}{C_RESET} {label}", W, border_color=border_color)


def draw_regime_compact_panel(reg: Dict[str, Any], W: int, border_color: str) -> None:
    """Phone layout: 3-line compact regime block."""
    for line in format_regime_compact_lines(reg):
        print_row(line, W, border_color=border_color)


def print_regime_section(reg: Dict[str, Any], W: int, is_phone: bool, border_color: str) -> None:
    """Print [1] MARKET REGIME block."""
    print_row(f" {C_BOLD}{C_PRIMARY}[1] MARKET REGIME & SENTIMENT{RESET}", W, border_color=border_color)
    if is_phone:
        draw_regime_compact_panel(reg, W, border_color)
    else:
        draw_regime_user_panel(reg, W, is_phone, border_color)


def regime_section_line_count(is_phone: bool) -> int:
    return 6 if is_phone else 10


def render_advanced_analytics_lines(metrics: Any, is_phone: bool) -> List[str]:
    """Return [2] ADVANCED PERFORMANCE ANALYTICS lines (with header)."""
    title = " [2] ADVANCED ANALYTICS" if is_phone else " [2] ADVANCED PERFORMANCE ANALYTICS"
    lines = [f" {C_BOLD}{C_PRIMARY}{title}{RESET}"]
    pf = metrics.profit_factor
    pf_str = f"{pf:.2f}x" if pf != float("inf") else "inf"
    if is_phone:
        lines.extend([
            f"  {C_MUTED}Total Returns: {C_PRIMARY}{metrics.total_return_pct:+.2f}%{C_RESET}",
            f"  {C_MUTED}Net PnL      : {C_GREEN if metrics.net_pnl >= 0 else C_RED}{metrics.net_pnl:+.4f}{C_RESET}",
            f"  {C_MUTED}Win Rate     : {C_PRIMARY}{metrics.win_rate:.0f}%{C_RESET} (max {metrics.consecutive_wins}W)",
            f"  {C_MUTED}Profit Factor: {C_PRIMARY}{pf_str}{C_RESET}",
        ])
        excursion_text = (
            f"{metrics.average_mae_pct:.2f}% / {metrics.average_mfe_pct:.2f}%"
            if metrics.excursion_coverage_pct > 0 else "collecting"
        )
        lines.append(
            f"  {C_MUTED}MAE/MFE avg  : {C_PRIMARY}{excursion_text}{C_RESET}"
        )
    else:
        lines.extend([
            f"  {C_MUTED}Net PnL      : {C_GREEN if metrics.net_pnl >= 0 else C_RED}{metrics.net_pnl:+.4f} USDT{C_RESET}   │ {C_MUTED}Sharpe/Sortino   : {C_PRIMARY}{metrics.sharpe_ratio:.2f} / {metrics.sortino_ratio:.2f}{C_RESET}",
            f"  {C_MUTED}Total Return : {C_PRIMARY}{metrics.total_return_pct:+.2f}%{C_RESET}            │ {C_MUTED}Profit Factor    : {C_PRIMARY}{pf_str}{C_RESET}",
            f"  {C_MUTED}Win Rate     : {C_PRIMARY}{metrics.win_rate:.1f}%{C_RESET}                    │ {C_MUTED}Max DD / Risk-Rew: {C_RED}{metrics.max_drawdown_pct:.2f}%{C_RESET} / {C_PRIMARY}{metrics.risk_reward_ratio:.2f}{C_RESET}",
        ])
        excursion_text = (
            f"{metrics.average_mae_pct:.2f}% / {metrics.average_mfe_pct:.2f}%"
            if metrics.excursion_coverage_pct > 0 else "collecting"
        )
        lines.append(
            f"  {C_MUTED}Avg MAE / MFE: {C_PRIMARY}{excursion_text}{C_RESET}              │ "
            f"{C_MUTED}Coverage: {C_PRIMARY}{metrics.excursion_coverage_pct:.0f}%{C_RESET}"
        )
    return lines


def advanced_analytics_line_count(is_phone: bool) -> int:
    return (5 if is_phone else 4) + 1


def render_track_record_lines(
    trades: List[Dict[str, Any]],
    is_phone: bool,
    *,
    initial_balance: float = 1000.0,
) -> List[str]:
    """Return [3] HISTORICAL TRACK RECORD lines (with header)."""
    from xauby.track_record.generator import generate_report

    r30 = generate_report(trades, 30, "30-Day", initial_balance=initial_balance)
    r90 = generate_report(trades, 90, "90-Day", initial_balance=initial_balance)
    r1y = generate_report(trades, 365, "1-Year", initial_balance=initial_balance)
    title = " [3] TRACK RECORD" if is_phone else " [3] HISTORICAL TRACK RECORD"
    lines = [f" {C_BOLD}{C_PRIMARY}{title}{RESET}"]
    if is_phone:
        for r in (r30, r90, r1y):
            pnl_color = C_GREEN if r.net_pnl >= 0 else C_RED
            lines.append(
                f"  {C_BOLD}{r.report_name}:{RESET} {C_PRIMARY}{r.total_trades}T{C_RESET} │ WR:{C_PRIMARY}{r.win_rate:.0f}%{C_RESET} │ PnL:{pnl_color}{r.net_pnl:+.1f}{C_RESET}"
            )
    else:
        header = f"  {'Report':<12} │ {'Trades':<8} │ {'Win Rate':<10} │ {'Net PnL':<15} │ {'Profit Fac':<10} │ {'Max DD':<8}"
        lines.append(header)
        lines.append(f"{C_DARK}{'─' * (len(header) - 2)}{C_RESET}")
        for r in (r30, r90, r1y):
            pnl_color = C_GREEN if r.net_pnl >= 0 else C_RED
            sign = "+" if r.net_pnl >= 0 else ""
            lines.append(
                f"  {r.report_name:<12} │ "
                f"{C_PRIMARY}{r.total_trades:<8}{C_RESET} │ "
                f"{C_PRIMARY}{r.win_rate:>5.1f}%{C_RESET}    │ "
                f"{pnl_color}{sign}{r.net_pnl:>9.4f} USDT{C_RESET} │ "
                f"{C_PRIMARY}{r.profit_factor:>9.2f}x{C_RESET} │ "
                f"{C_RED}{r.max_drawdown_pct:>7.2f}%{C_RESET}"
            )
    return lines


def track_record_line_count(is_phone: bool) -> int:
    return 4 if is_phone else 6


def draw_regime_view(db, state, W, is_mobile, border_color):
    reg = state.get("regime") or dict(DEFAULT_REGIME)
    
    border_top = "┌" + "─" * (W - 2) + "┐"
    border_mid = "├" + "─" * (W - 2) + "┤"
    print("\033[H", end="")
    print(f"{border_color}{border_top}{RESET}")
    print_row(f"{make_gemini_gradient('✦ MARKET REGIME MONITOR ✦')}", W, border_color=border_color)
    print(f"{border_color}{border_mid}{RESET}")

    draw_regime_user_panel(reg, W, is_mobile, border_color)

    trend_color = C_GREEN if reg.get("trend") == "BULLISH" else (C_RED if reg.get("trend") == "BEARISH" else C_MUTED)
    vol_color = C_RED if reg.get("volatility") == "HIGH" else (C_GREEN if reg.get("volatility") == "LOW" else C_PRIMARY)
    macro_color = C_GREEN if reg.get("macro_bias") == "RISK-ON" else (C_RED if reg.get("macro_bias") == "RISK-OFF" else C_MUTED)

    print(f"{border_color}{border_mid}{RESET}")
    print_row(f"  {C_MUTED}Details:{RESET}", W, border_color=border_color)
    print_row(f"  {C_BOLD}Trend Direction :{RESET} {trend_color}{reg.get('trend', 'NEUTRAL')}{C_RESET}", W, border_color=border_color)
    print_row(f"  {C_BOLD}Volatility Level:{RESET} {vol_color}{reg.get('volatility', 'NORMAL')}{C_RESET}", W, border_color=border_color)
    print_row(f"  {C_BOLD}Macro Bias      :{RESET} {macro_color}{reg.get('macro_bias', 'NEUTRAL')}{C_RESET}", W, border_color=border_color)
    
    print(f"{border_color}{border_mid}{RESET}")

def draw_track_record_view(db, state, W, is_mobile, border_color):
    from xauby.track_record.generator import generate_report
    symbol = _state_symbol(state)
    trades = db.get_closed_trades(symbol, limit=1000)
    
    metric_ctx = state.get("metrics_context") or {}
    initial_balance = float(metric_ctx.get("initial_balance") or 1000.0)
    r30 = generate_report(trades, 30, "30-Day", initial_balance=initial_balance)
    r90 = generate_report(trades, 90, "90-Day", initial_balance=initial_balance)
    r1y = generate_report(trades, 365, "1-Year", initial_balance=initial_balance)

    border_top = "┌" + "─" * (W - 2) + "┐"
    border_mid = "├" + "─" * (W - 2) + "┤"
    print("\033[H", end="")
    print(f"{border_color}{border_top}{RESET}")
    print_row(f"{make_gemini_gradient('✦ HISTORICAL PERFORMANCE REPORTS ✦')}", W, border_color=border_color)
    print(f"{border_color}{border_mid}{RESET}")

    if is_mobile:
        for r in (r30, r90, r1y):
            pnl_color = C_GREEN if r.net_pnl >= 0 else C_RED
            print_row(f"  {C_BOLD}{r.report_name} Report:{RESET}", W, border_color=border_color)
            print_row(f"    Trades: {C_PRIMARY}{r.total_trades}{C_RESET} │ Win Rate: {C_PRIMARY}{r.win_rate:.1f}%{C_RESET}", W, border_color=border_color)
            print_row(f"    Net PnL: {pnl_color}{r.net_pnl:+.4f} USDT{C_RESET} │ Max DD: {C_RED}{r.max_drawdown_pct:.1f}%{C_RESET}", W, border_color=border_color)
            print_row(f"    Avg Duration: {C_PRIMARY}{r.average_duration_hours:.1f}h{C_RESET}", W, border_color=border_color)
            print_row("─" * (W - 4), W, border_color=border_color)
    else:
        header = f"  {'Report':<12} │ {'Trades':<8} │ {'Win Rate':<10} │ {'Net PnL':<15} │ {'Profit Fac':<10} │ {'Max DD':<8} │ {'Avg Duration':<12}"
        print_row(header, W, border_color=border_color)
        print_row("─" * (W - 4), W, border_color=border_color)
        
        for r in (r30, r90, r1y):
            pnl_color = C_GREEN if r.net_pnl >= 0 else C_RED
            sign = "+" if r.net_pnl >= 0 else ""
            row = (
                f"  {r.report_name:<12} │ "
                f"{C_PRIMARY}{r.total_trades:<8}{C_RESET} │ "
                f"{C_PRIMARY}{r.win_rate:>5.1f}%{C_RESET}    │ "
                f"{pnl_color}{sign}{r.net_pnl:>9.4f} USDT{C_RESET} │ "
                f"{C_PRIMARY}{r.profit_factor:>9.2f}x{C_RESET} │ "
                f"{C_RED}{r.max_drawdown_pct:>7.2f}%{C_RESET} │ "
                f"{C_PRIMARY}{r.average_duration_hours:>9.1f}h{C_RESET}"
            )
            print_row(row, W, border_color=border_color)
            
    print(f"{border_color}{border_mid}{RESET}")
