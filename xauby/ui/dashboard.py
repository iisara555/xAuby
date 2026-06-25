import os
import sys
import time
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from xauby.storage.interface import IDatabaseRepository
from xauby.database.db import LiteDB
from xauby.utils.colors import (
    C_RESET, C_BOLD, C_PRIMARY, C_MUTED, C_DARK, C_BORDER,
    C_GEMINI_BLUE, C_GEMINI_PURPLE, C_GEMINI_PINK, C_GEMINI_CYAN,
    C_GREEN, C_RED, C_YELLOW, C_BLUE,
    C_BG_GREEN, C_BG_RED, C_BG_YELLOW, C_BG_BLUE, C_BG_INDIGO, C_BG_CYAN, C_BG_DARK, C_BG_ORANGE,
    RESET, BOLD, GREEN, RED, YELLOW, BLUE, CYAN, MAGENTA, WHITE,
    BG_GREEN, BG_RED, BG_YELLOW, BG_BLUE, BB_AMBER, BB_BG_AMBER, BB_CYAN,
    make_gemini_gradient, fg_rgb, bg_rgb
)
from xauby.utils.common import (
    visible_len, get_terminal_width, get_terminal_height,
    format_to_ict, format_ts_ict, TH_TZ,
)

# Import split module components
from xauby.ui.widgets import (
    print_row, pad_line, format_panel_header, format_panel_divider,
    get_zone_colored, format_pnl, format_pnl_compact, to_compact,
    make_zone_badge, compute_trade_stats,
    draw_analytics_view, draw_regime_view, draw_track_record_view,
    draw_regime_user_panel, draw_regime_compact_panel,
    format_regime_user_lines, format_regime_compact_lines, format_regime_inline_lines,
    sparkline, DEFAULT_REGIME, get_panel_column_widths,
)
from xauby.ui.chart import (
    get_chart_lines,
    get_bar_chart_lines,
    get_chart_candle_cols,
    get_chart_max_candles_cap,
    resolve_chart_timeframe,
)
from xauby.ui.system import get_ram_usage, calculate_cpu_usage, get_db_size, format_uptime
from xauby.meta import resolve_header_bot_title
from xauby.ui.state_view import (
    resolve_dashboard_state,
    write_focus_request,
    cycle_focus,
    format_pairs_table_lines,
    format_static_portfolio_lines,
    format_all_positions_lines,
    merge_closed_trades,
)

# View control state
CURRENT_VIEW = "dashboard"
LAST_DRAWN_VIEW = None
LAST_TERM_SIZE = (0, 0)
OLD_TERMINAL_SETTINGS = None

def init_terminal():
    global OLD_TERMINAL_SETTINGS
    if sys.platform != 'win32':
        import termios
        import tty
        try:
            fd = sys.stdin.fileno()
            if os.isatty(fd):
                OLD_TERMINAL_SETTINGS = termios.tcgetattr(fd)
                tty.setcbreak(fd)
        except Exception as e:
            try:
                from xauby.runtime.paths import ensure_runtime_dir, log_path
                ensure_runtime_dir("logs")
                with open(log_path("dashboard_err.log"), "a") as f:
                    f.write(f"Exception in init_terminal: {e}\n")
            except Exception:
                pass

def restore_terminal():
    global OLD_TERMINAL_SETTINGS
    if sys.platform != 'win32' and OLD_TERMINAL_SETTINGS is not None:
        import termios
        try:
            fd = sys.stdin.fileno()
            if os.isatty(fd):
                termios.tcsetattr(fd, termios.TCSADRAIN, OLD_TERMINAL_SETTINGS)
        except Exception as e:
            try:
                from xauby.runtime.paths import ensure_runtime_dir, log_path
                ensure_runtime_dir("logs")
                with open(log_path("dashboard_err.log"), "a") as f:
                    f.write(f"Exception in restore_terminal: {e}\n")
            except Exception:
                pass
        OLD_TERMINAL_SETTINGS = None



@dataclass(frozen=True)
class LayoutProfile:
    tier: str
    is_phone: bool
    is_tablet: bool
    is_wide: bool

    @property
    def is_mobile(self) -> bool:
        """Backward compat: phone-only compact rendering."""
        return self.is_phone


def get_layout_profile(W: int) -> LayoutProfile:
    if W >= 110:
        return LayoutProfile("wide", False, False, True)
    if W >= 75:
        return LayoutProfile("tablet", False, True, False)
    return LayoutProfile("phone", True, False, False)


CHECKLIST_LINE_BUDGET = 60
BLOCKER_LINE_BUDGET = 75
CHART_AXIS_RESERVE = 12  # indent + │ + space + price label ($X,XXX)





def _chart_candle_count(content_width: int, profile: LayoutProfile) -> int:
    """Candles for chart: cli_ui.chart_max_candles cap, else fit width (chart_candle_cols per bar)."""
    cols = get_chart_candle_cols()
    cap = get_chart_max_candles_cap()
    if cap > 0:
        usable = max(16, content_width - CHART_AXIS_RESERVE)
        fit = max(8, usable // cols)
        return min(cap, fit)
    usable = max(16, content_width - CHART_AXIS_RESERVE)
    count = max(8, usable // cols)
    if profile.is_phone:
        count = min(count, 40)
    elif profile.is_tablet:
        count = min(count, 56)
    elif profile.is_wide:
        count = min(count, 64)
    return count


def _phone_regime_extras(
    guard_compact: str,
    g_info: Dict[str, Any],
    countdown_compact: str,
    risk_pct: float,
) -> tuple[str, str, str]:
    """Guard / candle close / risk fragments for inline regime line 2."""
    guard_inline = guard_compact if g_info.get("enabled") else "OFF"
    close_text = f"{C_MUTED}Cls:{C_RESET}{countdown_compact}"
    risk_text = f"{C_MUTED}Rsk:{C_RESET}{C_YELLOW}{risk_pct:.1f}%{C_RESET}"
    return guard_inline, close_text, risk_text


def _multi_phone_focus_line(
    focus_title: str,
    curr_price: float,
    change: float,
    change_color: str,
    max_width: int,
) -> str:
    line = (
        f"{C_BOLD}{C_PRIMARY}{focus_title}{C_RESET} │ "
        f"{C_PRIMARY}{curr_price:,.2f}{C_RESET} ({change_color}{change:+.2f}%{C_RESET})"
    )
    if visible_len(line) <= max_width:
        return line
    return _fit_checklist_line(line, max_width)


def _chart_header_compact(chart_tf: str, symbol: str, chart_w: int) -> str:
    return f"{chart_tf.upper()} · {symbol} · {chart_w}c"


def _chart_dimensions(
    W: int,
    profile: LayoutProfile,
    content_width: int | None = None,
) -> tuple:
    """Return (candle_count, chart_height, show_volume_bars)."""
    if content_width is None:
        content_width = W - 4 if not profile.is_wide else get_panel_column_widths(W)[1]
    width = _chart_candle_count(content_width, profile)
    if profile.is_wide:
        height = 25
    elif profile.is_tablet:
        height = 22
    else:
        height = 16
    # Phone: candles only (volume panel disabled — layout/scroll issues on narrow terminals).
    show_vol = height >= 18 and not profile.is_phone
    return width, height, show_vol


def _fit_checklist_line(text: str, max_width: int) -> str:
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
    return "".join(out) + "..."


def _render_range_bar(cfg: Dict[str, Any], width: int) -> str:
    try:
        lo_bound = float(cfg.get("min", 0.0))
        hi_bound = float(cfg.get("max", 100.0))
        low = float(cfg.get("low", lo_bound))
        high = float(cfg.get("high", hi_bound))
        value = float(cfg.get("value", lo_bound))
    except (TypeError, ValueError):
        return ""
    span = max(1e-9, hi_bound - lo_bound)
    clamped = max(lo_bound, min(hi_bound, value))
    dot_pos = max(0, min(width, int(round(((clamped - lo_bound) / span) * width))))
    lo_pos = int(round(((low - lo_bound) / span) * width))
    hi_pos = int(round(((high - lo_bound) / span) * width))
    ok = low <= value <= high
    dot_color = C_GREEN if ok else C_RED
    chars = []
    for i in range(width + 1):
        if i == dot_pos:
            chars.append(f"{dot_color}●{C_RESET}")
        elif i == lo_pos or i == hi_pos:
            chars.append(f"{C_MUTED}│{C_RESET}")
        elif lo_pos < i < hi_pos:
            chars.append(f"{C_GREEN}─{C_RESET}")
        else:
            chars.append(f"{C_DARK}─{C_RESET}")
    return "".join(chars)


def _render_progress_bar(cfg: Dict[str, Any], width: int) -> str:
    try:
        max_v = float(cfg.get("max", 1.0)) or 1.0
        threshold = float(cfg.get("threshold", 0.0))
        value = float(cfg.get("value", 0.0))
    except (TypeError, ValueError):
        return ""
    fill_pct = min(1.0, max(0.0, value / max_v))
    filled = int(round(fill_pct * width))
    thresh_pos = max(0, min(width, int(round((threshold / max_v) * width))))
    ok = value >= threshold
    color = C_GREEN if ok else C_RED
    chars = []
    for i in range(width):
        if i < filled:
            chars.append(f"{color}█{C_RESET}")
        elif i == thresh_pos:
            chars.append(f"{C_YELLOW}┊{C_RESET}")
        else:
            chars.append(f"{C_MUTED}░{C_RESET}")
    return "".join(chars)


def _bar_width_for_line(profile: LayoutProfile, max_width: int, base: int) -> int:
    """Pick bar width that fits rich checklist lines within ~60 visible cols."""
    budget = min(max_width, CHECKLIST_LINE_BUDGET)
    reserved = 38 if profile.is_phone else 42
    cap = max(6, budget - reserved)
    if profile.is_phone:
        return max(6, min(base, cap, 8))
    if profile.is_tablet:
        return max(8, min(base, cap, 10))
    return max(10, min(base, cap, 12))


def _shorten_hold_reason(reason: str) -> str:
    """Compact engine hold reason for blocker box (target <= 75 visible chars)."""
    import re

    r = reason.strip()
    if not r:
        return "Entry blocked"

    m = re.search(
        r"4H zone GREEN but not fresh \(green for (\d+) bars > window (\d+)\)",
        r,
        re.I,
    )
    if m:
        return f"Zone GREEN stale ({m.group(1)}/{m.group(2)})"

    m = re.search(r"4H zone not GREEN \(current: (\w+)\)", r, re.I)
    if m:
        return f"Zone not GREEN ({m.group(1)})"

    m = re.search(r"Volume ratio ([\d.]+)x below ([\d.]+)x", r, re.I)
    if m:
        return f"Vol low ({m.group(1)}<{m.group(2)}x)"

    m = re.search(r"RSI ([\d.]+) out of bounds \[([\d.]+), ([\d.]+)\]", r, re.I)
    if m:
        return f"RSI {m.group(1)} outside [{m.group(2)}-{m.group(3)}]"

    if r.lower().startswith("ema cross check failed:"):
        tail = r.split(":", 1)[-1].strip()
        if "prev EMA12" in tail and ">" in tail:
            return "EMA cross stale (prev bull)"
        if "curr EMA12" in tail and "<=" in tail:
            return "EMA not bullish"
        return "EMA cross failed"

    m = re.search(r"D1 zone: (\w+)", r, re.I)
    if "daily regime" in r.lower() and m:
        return f"D1 filter block ({m.group(1)})"

    if len(r) > 72:
        return r[:69] + "..."
    return r


GATE_LABEL_SHORT: Dict[str, str] = {
    "Fresh Entry": "Fresh",
    "4H Trend Zone": "Zone",
    "4H Zone": "Zone",
    "EMA Bullish": "EMA",
    "EMA Cross": "Cross",
    "RSI(14)": "RSI",
    "Vol Ratio": "Vol",
    "Volume": "Vol",
    "Macro Guard": "Guard",
    "D1 Regime": "D1",
    "Trailing Stop": "SL",
    "Exit Signal": "Exit",
    "Fresh": "Fresh",
}


def _short_gate_label(label: str) -> str:
    lab = str(label or "").strip()
    if lab in GATE_LABEL_SHORT:
        return GATE_LABEL_SHORT[lab]
    for key, short in GATE_LABEL_SHORT.items():
        if key.lower() in lab.lower():
            return short
    return lab[:6] if len(lab) > 6 else lab


def _gate_label(label: str, profile: "LayoutProfile") -> str:
    """Select gate label length based on layout profile."""
    lab = str(label or "").strip()
    if profile.is_wide:
        return lab
    if profile.is_tablet:
        return lab[:8] if len(lab) > 8 else lab
    return _short_gate_label(lab)


def _metric_tier(value: float, green_max: float, yellow_max: float) -> str:
    if value < green_max:
        return "ok"
    if value < yellow_max:
        return "warn"
    return "crit"


def _format_metric_badge(label: str, value_str: str, tier: str) -> str:
    if tier == "ok":
        bg = C_BG_GREEN
    elif tier == "warn":
        bg = C_BG_YELLOW
    else:
        bg = C_BG_RED
    return f" {bg}{C_BOLD} {label} {value_str} {C_RESET} "


def format_engine_status_badges(
    cpu_pct: float,
    ram_load: float,
    db_size_mb: float,
    *,
    compact: bool = False,
) -> str:
    """Colored CPU/RAM/DB chips for AppHeader."""
    cpu_t = _metric_tier(cpu_pct, 50.0, 80.0)
    ram_t = _metric_tier(ram_load, 60.0, 85.0)
    db_t = _metric_tier(db_size_mb, 200.0, 500.0)

    if compact:
        cpu_v = f"{cpu_pct:.0f}%"
        ram_v = f"{ram_load:.0f}%"
        db_v = f"{db_size_mb:.0f}M"
        return (
            _format_metric_badge("C", cpu_v, cpu_t)
            + _format_metric_badge("R", ram_v, ram_t)
            + _format_metric_badge("D", db_v, db_t)
        )

    cpu_v = f"{cpu_pct:.1f}%"
    ram_v = f"{ram_load:.1f}%"
    db_v = f"{db_size_mb:.1f}MB"
    return (
        _format_metric_badge("CPU", cpu_v, cpu_t)
        + _format_metric_badge("RAM", ram_v, ram_t)
        + _format_metric_badge("DB", db_v, db_t)
    )


def _action_style(action: str) -> tuple:
    act = str(action or "HOLD").upper()
    if act == "BUY":
        return act, C_GREEN, C_BG_GREEN
    if act == "SELL":
        return act, C_RED, C_BG_RED
    return act, C_YELLOW, C_BG_YELLOW


def _semantic_action(meta: Dict[str, Any]) -> str:
    """Use trade intent/side so SHORT signals are not labelled as plain SELL/BUY."""
    action = str(meta.get("action", "HOLD")).upper()
    intent = str(meta.get("intent") or "").upper()
    side = str(meta.get("position_side") or "").upper()
    if intent in ("OPEN", "CLOSE") and side in ("LONG", "SHORT"):
        return f"{intent} {side}"
    return action


def format_decision_gates_compact(
    state: Dict[str, Any],
    profile: LayoutProfile,
    max_width: int,
) -> List[str]:
    """Compact pass/fail gate row for DECISION panel."""
    from xauby.ui.copy import is_monitoring_empty_state

    meta = state.get("signal_meta") or {}
    items = meta.get("checklist") if isinstance(meta, dict) else None
    if not isinstance(items, list) or not items:
        items = _checklist_items_from_state(state)

    action = str(meta.get("action", "HOLD")).upper()
    semantic_action = _semantic_action(meta)
    style_action = "BUY" if semantic_action == "OPEN LONG" or semantic_action == "CLOSE SHORT" else (
        "SELL" if semantic_action == "OPEN SHORT" or semantic_action == "CLOSE LONG" else action
    )
    act, act_c, act_bg = _action_style(style_action)
    act = semantic_action
    passed = sum(1 for it in items if isinstance(it, dict) and it.get("ok"))
    total = sum(1 for it in items if isinstance(it, dict))
    lines: List[str] = []

    if is_monitoring_empty_state(state) and action == "HOLD" and not meta.get("reason"):
        from xauby.ui.copy import format_monitoring_empty
        lines.append(format_monitoring_empty(compact=profile.is_phone, with_subline=not profile.is_phone))
    else:
        head = (
            f"  {act_bg}{C_BOLD} {act}{C_RESET} "
            f"{C_MUTED}·{C_RESET} {act_c}{passed}/{total} passed{C_RESET}"
        )
        lines.append(_fit_checklist_line(head, min(max_width, CHECKLIST_LINE_BUDGET)))

    chk_ok = f"{fg_rgb(52, 211, 153)}✓{C_RESET}"
    chk_err = f"{fg_rgb(251, 113, 133)}✗{C_RESET}"
    chips: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        short = _gate_label(str(item.get("label", "")), profile)
        mark = chk_ok if item.get("ok") else chk_err
        chips.append(f"{mark}{short}")

    if chips:
        sep = " " if profile.is_phone else "  "
        chip_line = f"  {sep.join(chips)}"
        budget = min(max_width, CHECKLIST_LINE_BUDGET) if profile.is_phone else max_width
        while chips and visible_len(chip_line) > budget:
            chips.pop()
            chip_line = f"  {sep.join(chips)}"
        if chips:
            lines.append(chip_line)

    reason = str(meta.get("reason") or "").strip()
    pos_state = (state.get("position") or {}).get("state", "idle")
    if action == "HOLD" and pos_state != "bought" and reason:
        short_r = _shorten_hold_reason(reason)
        warn = f"  {C_YELLOW}⚠ {short_r}{C_RESET}"
        lines.append(_fit_checklist_line(warn, min(max_width, CHECKLIST_LINE_BUDGET)))

    return lines


def show_verbose_checklist(layout_width: int, config: Optional[Dict[str, Any]] = None) -> bool:
    """Whether to show full STRATEGY CHECKLIST panel below DECISION."""
    try:
        import yaml
        import os

        cfg = config
        if cfg is None and os.path.isfile("bot_config.yaml"):
            with open("bot_config.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        cfg = cfg or {}
        explicit = (cfg.get("cli_ui") or {}).get("checklist_verbose")
        if explicit is not None:
            return bool(explicit)
    except Exception:
        pass
    return layout_width >= 110


def _shorten_next_hint(failing: List[str]) -> str:
    """Short checklist-based next-step hint for blocker box."""
    short_map = {
        "Fresh Entry": "Fresh",
        "4H Trend Zone": "4H Zone",
        "Vol Ratio": "Vol",
        "Macro Guard": "Guard",
    }
    parts = [short_map.get(l, l.replace(" Entry", "")) for l in failing[:4]]
    if not parts:
        return "Monitor checklist"
    return "Wait: " + " + ".join(parts)


def _compose_pipe_line(
    mark: str,
    label: str,
    value_part: str,
    tail_cols: List[str],
    max_width: int,
    label_w: int = 16,
) -> str:
    """Join checklist segments with pipe separators; fit within 60 visible cols."""
    budget = min(max_width, CHECKLIST_LINE_BUDGET)
    sep = f" {C_MUTED}│{C_RESET} "
    base = f"  {mark} {label:<{label_w}}: {value_part}"
    cols = [c for c in tail_cols if c]

    if not cols:
        return _fit_checklist_line(base, budget) if visible_len(base) > budget else base

    while cols:
        line = base + sep + sep.join(cols)
        if visible_len(line) <= budget:
            return line
        cols.pop()

    return _fit_checklist_line(base, budget) if visible_len(base) > budget else base


def _item_tail_columns(item: Dict[str, Any]) -> List[str]:
    cols = item.get("columns")
    if isinstance(cols, list) and cols:
        return [str(c) for c in cols if c]
    hint = item.get("hint", "")
    return [str(hint)] if hint else []


def _render_strategy_checklist(
    items: List[Dict[str, Any]],
    profile: LayoutProfile,
    max_width: int,
) -> List[str]:
    """Render a strategy-supplied checklist (engine-agnostic, rich multi-column)."""
    chk_ok = f"{fg_rgb(52, 211, 153)}[✓]{RESET}"
    chk_err = f"{fg_rgb(251, 113, 133)}[✗]{RESET}"

    if profile.is_phone:
        passed = sum(1 for it in items if isinstance(it, dict) and it.get("ok"))
        total = sum(1 for it in items if isinstance(it, dict))
        failing = [str(it.get("label", ""))[:6] for it in items if isinstance(it, dict) and not it.get("ok")]
        ok_mark = f"{fg_rgb(52, 211, 153)}✓{RESET}"
        err_mark = f"{fg_rgb(251, 113, 133)}✗{RESET}"
        fail_str = ("  " + "  ".join(f"{C_RED}{lb}{C_RESET}" for lb in failing[:3])) if failing else ""
        summary = f"  {ok_mark}{C_GREEN}{passed}{C_RESET}/{total}{fail_str}"
        return [_fit_checklist_line(summary, max_width)]

    label_w = 14
    lines: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", ""))
        value = item.get("value", "")
        ok = bool(item.get("ok", False))
        mark = chk_ok if ok else chk_err
        val_color = C_PRIMARY if ok else C_RED
        tail_cols = _item_tail_columns(item)

        bar_cfg = item.get("bar")
        if isinstance(bar_cfg, dict):
            bar_type = bar_cfg.get("type")
            if bar_type == "range":
                width = _bar_width_for_line(profile, max_width, 16)
                bar = _render_range_bar(bar_cfg, width)
                try:
                    lo = float(bar_cfg.get("low", 0.0))
                    hi = float(bar_cfg.get("high", 100.0))
                    val_str = f"{val_color}{value!s}{C_RESET}"
                    bar_seg = f"[{C_MUTED}{lo:.0f}{C_RESET} {bar} {C_MUTED}{hi:.0f}{C_RESET}]"
                    zone_col = tail_cols[0] if tail_cols else ""
                    zone_str = f"{C_MUTED}{zone_col}{C_RESET}" if zone_col else ""
                    value_part = f"{val_str}  {bar_seg}"
                    line = _compose_pipe_line(
                        mark, label, value_part, [zone_str] if zone_str else [], max_width, label_w
                    )
                except (TypeError, ValueError):
                    value_part = f"{val_color}{value}{C_RESET}  {bar}"
                    line = _compose_pipe_line(mark, label, value_part, tail_cols, max_width, label_w)
                lines.append(line)
                continue
            if bar_type == "progress":
                width = _bar_width_for_line(profile, max_width, 14)
                bar = _render_progress_bar(bar_cfg, width)
                try:
                    max_v = float(bar_cfg.get("max", 3.0)) or 3.0
                    val_f = float(bar_cfg.get("value", 0.0))
                    pct = int(round(min(1.0, val_f / max_v) * 100))
                except (TypeError, ValueError):
                    pct = 0
                val_str = f"{val_color}{value!s}{C_RESET}"
                bar_seg = f"[{bar}] {pct}%"
                value_part = f"{val_str}  {bar_seg}"
                line = _compose_pipe_line(mark, label, value_part, tail_cols, max_width, label_w)
                lines.append(line)
                continue

        value_part = f"{val_color}{value}{C_RESET}"
        line = _compose_pipe_line(mark, label, value_part, tail_cols, max_width, label_w)
        lines.append(line)
    return lines


def render_blocker_box(
    state: Dict[str, Any],
    items: List[Dict[str, Any]],
    max_width: int,
    *,
    is_phone: bool = False,
) -> List[str]:
    """Two-line bordered box when entry is blocked (HOLD); one line on phone."""
    meta = state.get("signal_meta") or {}
    action = str(meta.get("action", "HOLD")).upper()
    pos_state = (state.get("position") or {}).get("state", "idle")
    if action != "HOLD" or pos_state == "bought":
        return []

    reason = str(meta.get("reason") or "").strip()
    failing = [
        str(it.get("label", ""))
        for it in items
        if isinstance(it, dict) and not it.get("ok", False)
    ]
    if not reason and not failing:
        return []

    blocked = _shorten_hold_reason(reason) if reason else "Entry conditions not met"

    if failing:
        next_hint = _shorten_next_hint(failing)
    elif reason:
        next_hint = "Review signals"
    else:
        next_hint = "Monitor checklist"

    inner_w = max(20, max_width - 4)
    blocker_budget = max(BLOCKER_LINE_BUDGET, inner_w - 2)
    top = f"  {C_YELLOW}⚠{C_RESET}  {C_BOLD}BLOCKED:{C_RESET} {C_RED}{blocked}{C_RESET}"
    bot = f"  {C_MUTED}⏳{C_RESET}  {C_BOLD}NEXT:{C_RESET} {C_PRIMARY}{next_hint}{C_RESET}"
    top = _fit_checklist_line(top, blocker_budget) if visible_len(top) > blocker_budget else top
    bot = _fit_checklist_line(bot, blocker_budget) if visible_len(bot) > blocker_budget else bot

    if is_phone:
        one = (
            f"  {C_YELLOW}⚠{C_RESET} {C_BOLD}BLOCKED:{C_RESET} {C_RED}{blocked}{C_RESET} "
            f"{C_MUTED}│{C_RESET} {C_BOLD}NEXT:{C_RESET} {C_PRIMARY}{next_hint}{C_RESET}"
        )
        budget = min(max_width, CHECKLIST_LINE_BUDGET)
        if visible_len(one) > budget:
            one = _fit_checklist_line(one, budget)
        return [one]

    border = f"{C_DARK}┌{'─' * (inner_w - 2)}┐{C_RESET}"
    mid1 = f"{C_DARK}│{C_RESET}{top[2:] if top.startswith('  ') else top}"
    mid1 = _fit_checklist_line(mid1, max_width)
    mid2 = f"{C_DARK}│{C_RESET}{bot[2:] if bot.startswith('  ') else bot}"
    mid2 = _fit_checklist_line(mid2, max_width)
    bottom = f"{C_DARK}└{'─' * (inner_w - 2)}┘{C_RESET}"
    return ["", border, mid1, mid2, bottom]


def _guard_status_labels(g_info: Dict[str, Any]) -> tuple:
    """Return (full_badge, compact) for macro guard scoped to the focused symbol."""
    g_enabled = bool(g_info.get("enabled", False))
    applies = g_info.get("applies_to_symbol", True)
    blocks = bool(g_info.get("blocks_buy", False))
    g_score = float(g_info.get("score", 0.0) or 0.0)
    if not g_enabled:
        return (
            f"{C_BG_DARK} GUARD: OFF ({g_score:+.2f}) {C_RESET}",
            f"{C_MUTED}G:OFF({g_score:+.2f}){C_RESET}",
        )
    if not applies:
        return (
            f"{C_BG_DARK} GUARD: N/A ({g_score:+.2f} ref) {C_RESET}",
            f"{C_MUTED}G:N/A({g_score:+.2f}){C_RESET}",
        )
    if blocks or g_score < -0.5:
        return (
            f"{C_BG_RED} GUARD: BLOCKED ({g_score:+.2f}) {C_RESET}",
            f"{C_RED}G:BLOCK({g_score:+.2f}){C_RESET}",
        )
    if g_score < 0:
        return (
            f"{C_BG_YELLOW} GUARD: WARN ({g_score:+.2f}) {C_RESET}",
            f"{C_YELLOW}G:WARN({g_score:+.2f}){C_RESET}",
        )
    return (
        f"{C_BG_GREEN} GUARD: OK ({g_score:+.2f}) {C_RESET}",
        f"{C_GREEN}G:OK({g_score:+.2f}){C_RESET}",
    )


def get_recent_events_lines(state: Dict[str, Any], limit: int = 3) -> List[str]:
    """Render recent observability events from state snapshot (noise filtered)."""
    skip_types = frozenset({"tick", "heartbeat"})
    events = state.get("recent_events") or []
    filtered: List[Dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        etype = ev.get("event_type") or ev.get("event") or ""
        if etype in skip_types:
            continue
        if etype == "signal_evaluated":
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            action = payload.get("action") or ev.get("action") or "HOLD"
            if str(action).upper() == "HOLD":
                continue
        filtered.append(ev)

    lines: List[str] = []
    if not filtered:
        lines.append(f"  {C_MUTED}No notable lifecycle events yet.{C_RESET}")
        return lines
    for ev in filtered[-limit:]:
        etype = ev.get("event_type") or ev.get("event", "?")
        ts = format_ts_ict(ev.get("ts") or "")
        ev_sym = str(ev.get("symbol", "") or "").upper().replace("_", "")
        sym_tag = f"{C_MUTED}{ev_sym}{C_RESET} " if ev_sym else ""
        payload = ev.get("payload") or {}
        if isinstance(payload, str):
            detail = payload[:32]
        else:
            parts = []
            for k in ("reason", "action", "price", "entry", "exit", "pnl", "trigger"):
                if k in ev and ev[k] is not None:
                    parts.append(f"{k}={ev[k]}")
                elif k in payload and payload[k] is not None:
                    val = payload[k]
                    if k == "reason":
                        val = str(val)[:28]
                    parts.append(f"{k}={val}")
            detail = " ".join(parts)[:36] if parts else ""
        lines.append(
            f"  {C_MUTED}{ts}{C_RESET} {sym_tag}{C_PRIMARY}{etype}{C_RESET} {detail}"
        )
    return lines


def _local_ema_cross_check(
    ema_fast: float,
    ema_slow: float,
    ema_fast_prev: float,
    ema_slow_prev: float,
    green_streak: int = 1,
    fresh_zone_window: int = 0,
) -> tuple[bool, str]:
    if ema_fast <= 0 or ema_slow <= 0:
        return False, "EMA values unavailable"
    if ema_fast <= ema_slow:
        return False, f"curr EMA12 {ema_fast:.2f} <= EMA26 {ema_slow:.2f}"
    if fresh_zone_window == 0:
        return True, "bull (fresh zone disabled)"
    if ema_fast_prev <= 0 or ema_slow_prev <= 0:
        return True, "bull (no prev EMA data)"
    prev_not_bull = ema_fast_prev <= ema_slow_prev
    if green_streak > 1 and green_streak <= fresh_zone_window:
        return True, "bull within fresh window"
    if not prev_not_bull:
        return False, f"prev EMA12 {ema_fast_prev:.2f} > EMA26 {ema_slow_prev:.2f} (need prev <= on cross bar)"
    return True, "fresh bull cross (prev <=, curr >)"


def _local_ema_cross_display(
    ema_fast: float,
    ema_slow: float,
    ema_fast_prev: float,
    ema_slow_prev: float,
    green_streak: int,
    fresh_zone_window: int,
    require_fresh_zone: bool,
) -> tuple[bool, str]:
    ok, _ = _local_ema_cross_check(
        ema_fast,
        ema_slow,
        ema_fast_prev,
        ema_slow_prev,
        green_streak=green_streak,
        fresh_zone_window=fresh_zone_window if require_fresh_zone else 0,
    )
    if ema_fast <= 0 or ema_slow <= 0:
        return False, "N/A"
    if ema_fast <= ema_slow:
        return False, "No cross"
    if require_fresh_zone and fresh_zone_window > 1 and green_streak > 1 and green_streak <= fresh_zone_window:
        return True, f"Win {green_streak}/{fresh_zone_window}"
    if ema_fast_prev > 0 and ema_slow_prev > 0 and ema_fast_prev <= ema_slow_prev:
        return ok, "Fresh cross"
    return False, "No cross"


def _checklist_items_from_state(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Resolve checklist items: registry display > signal_meta > CDC fallback."""
    try:
        from xauby.runtime.architecture_config import tui_indicator_registry
        import yaml

        with open("bot_config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        if tui_indicator_registry(cfg):
            panel = (state.get("indicator_display") or {}).get("panel_items")
            if isinstance(panel, list) and panel:
                return panel
    except Exception:
        pass
    meta = state.get("signal_meta") or {}
    items = meta.get("checklist") if isinstance(meta, dict) else None
    if isinstance(items, list) and items:
        return items
    return _fallback_checklist_items(state)


def _fallback_checklist_items(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build checklist item dicts when signal_meta.checklist is unavailable."""
    ind = state.get("indicators", {})
    risk_cfg = state.get("risk", {})
    h4_zone = ind.get("cdc_zone_4h", "UNKNOWN")
    rsi = float(ind.get("rsi_4h", 0.0))
    vol_ratio = float(ind.get("volume_ratio_4h", 0.0))
    rsi_min = float(risk_cfg.get("rsi_min", 45.0))
    rsi_max = float(risk_cfg.get("rsi_max", 70.0))
    vol_min_ratio = float(risk_cfg.get("vol_min_ratio", 1.0))
    fresh_zone_window = int(risk_cfg.get("fresh_zone_window", 3))
    require_fresh_zone = bool(risk_cfg.get("require_fresh_zone", True))

    ema_fast = float(ind.get("ema_fast_4h", 0.0))
    ema_slow = float(ind.get("ema_slow_4h", 0.0))
    ema_fast_prev = float(ind.get("ema_fast_4h_prev", 0.0))
    ema_slow_prev = float(ind.get("ema_slow_4h_prev", 0.0))
    green_streak = int(ind.get("cdc_zone_4h_green_streak", 0))

    ema_gap_pct = ((ema_fast - ema_slow) / ema_slow * 100.0) if ema_slow else 0.0
    ema_bull = ema_fast > ema_slow
    ema_cross_ok, cross_val = _local_ema_cross_display(
        ema_fast, ema_slow, ema_fast_prev, ema_slow_prev,
        green_streak=green_streak,
        fresh_zone_window=fresh_zone_window,
        require_fresh_zone=require_fresh_zone,
    )

    is_green = h4_zone == "GREEN"
    if is_green and 1 <= green_streak <= fresh_zone_window:
        zone_col = f"Fresh {green_streak}/{fresh_zone_window}"
    elif is_green:
        zone_col = (
            f"Fresh {green_streak}/{fresh_zone_window}"
            if green_streak <= fresh_zone_window
            else f"Stale ({green_streak}/{fresh_zone_window})"
        )
    else:
        zone_col = "Need GREEN"

    if not ema_cross_ok:
        cross_col = "Prev bearish" if ema_fast <= ema_slow else "Need fresh cross"
    elif require_fresh_zone and green_streak > 1 and green_streak <= fresh_zone_window:
        cross_col = "In fresh window"
    else:
        cross_col = "Cross OK"

    if rsi < rsi_min:
        rsi_zone = "Too low"
    elif rsi > rsi_max:
        rsi_zone = "Too high"
    else:
        rsi_zone = "Neutral"

    items: List[Dict[str, Any]] = [
        {
            "label": "EMA Bullish",
            "value": f"{'Bull' if ema_bull else 'Bear'} ({ema_gap_pct:+.2f}%)",
            "ok": ema_bull,
            "columns": ["Bull OK" if ema_bull else "Bearish"],
        },
        {
            "label": "EMA Cross",
            "value": cross_val,
            "ok": ema_cross_ok,
            "columns": [cross_col],
        },
        {
            "label": "4H Zone",
            "value": h4_zone,
            "ok": h4_zone == "GREEN",
            "columns": [zone_col],
        },
        {
            "label": "RSI(14)",
            "value": round(rsi, 1),
            "ok": rsi_min <= rsi <= rsi_max,
            "columns": [rsi_zone],
            "bar": {
                "type": "range",
                "min": 0.0, "max": 100.0,
                "low": rsi_min, "high": rsi_max, "value": rsi,
            },
        },
        {
            "label": "Vol Ratio",
            "value": f"{vol_ratio:.2f}x",
            "ok": vol_ratio >= vol_min_ratio,
            "columns": [f"Need: {vol_min_ratio:.1f}x"],
            "bar": {
                "type": "progress",
                "max": 3.0, "threshold": vol_min_ratio, "value": vol_ratio,
            },
        },
    ]

    if require_fresh_zone:
        fresh_ok = is_green and 1 <= green_streak <= fresh_zone_window
        if not is_green:
            fresh_val, fresh_hint = "wait GREEN", "Need GREEN"
        elif green_streak <= fresh_zone_window:
            fresh_val = f"Fresh {green_streak}/{fresh_zone_window}"
            fresh_hint = "In window"
        else:
            fresh_val = f"EXPIRED ({green_streak}/{fresh_zone_window})"
            fresh_hint = f"Cross: {green_streak}b ago"
        items.append({
            "label": "Fresh",
            "value": fresh_val,
            "ok": fresh_ok,
            "columns": [fresh_hint],
        })

    g_info = state.get("macro_guard", {})
    if g_info.get("enabled", False) and g_info.get("applies_to_symbol", True):
        g_score = float(g_info.get("score", 0.0))
        threshold = float(g_info.get("blocking_threshold", -0.5))
        items.append({
            "label": "Macro Guard",
            "value": f"{g_score:+.2f}",
            "ok": not g_info.get("blocks_buy", g_score < threshold),
            "columns": [f"Need >= {threshold:+.1f}"],
        })
    elif g_info.get("enabled", False):
        items.append({
            "label": "Macro Guard",
            "value": "N/A",
            "ok": True,
            "columns": ["Not applied to this pair"],
        })

    return items


def _checklist_budget_lines(state: Dict[str, Any], *, is_phone: bool = False) -> int:
    """Estimated terminal rows for checklist block (items + optional blocker box)."""
    meta = state.get("signal_meta") or {}
    items = meta.get("checklist") if isinstance(meta, dict) else None
    n = len(items) if isinstance(items, list) else 6
    pos_state = (state.get("position") or {}).get("state", "idle")
    action = str(meta.get("action", "HOLD")).upper()
    if action == "HOLD" and pos_state != "bought":
        extra = 1 if is_phone else 4
    else:
        extra = 0
    return min(14, max(7, n + 2 + extra))


def get_checklist_lines(state: Dict[str, Any], profile: LayoutProfile, W: int) -> List[str]:
    max_width = min(max(20, W - 4), CHECKLIST_LINE_BUDGET)
    meta = state.get("signal_meta") or {}
    items = meta.get("checklist") if isinstance(meta, dict) else None

    pos = state.get("position", {})
    pos_state = pos.get("state", "idle")

    if isinstance(items, list) and items:
        if pos_state == "bought":
            return _position_checklist_lines(state, pos, meta, items, profile, max_width)
        lines = _render_strategy_checklist(items, profile, max_width)
        lines.extend(render_blocker_box(state, items, max_width, is_phone=profile.is_phone))
        return lines

    sl = float(pos.get("stop_loss", 0.0))
    curr_price = float(state.get("current_price", 0.0))
    ind = state.get("indicators", {})
    h4_zone = ind.get("cdc_zone_4h", "UNKNOWN")

    chk_ok = f"{fg_rgb(52, 211, 153)}[✓]{RESET}"
    chk_err = f"{fg_rgb(251, 113, 133)}[✗]{RESET}"

    if pos_state == "bought":
        sl_ok = curr_price > sl
        zone_ok = h4_zone != "RED"
        lines = [
            _fit_checklist_line(
                f"  {chk_ok if sl_ok else chk_err} Trailing Stop : "
                f"{C_PRIMARY}{curr_price:,.2f}{C_RESET} vs SL {C_RED}{sl:,.2f}{C_RESET}",
                max_width,
            ),
            _fit_checklist_line(
                f"  {chk_ok if zone_ok else chk_err} Exit Signal  : "
                f"4H Zone {C_PRIMARY}{h4_zone}{C_RESET}",
                max_width,
            ),
        ]
        return lines

    fb_items = _checklist_items_from_state(state)
    lines = _render_strategy_checklist(fb_items, profile, max_width)
    lines.extend(render_blocker_box(state, fb_items, max_width, is_phone=profile.is_phone))
    return lines


def _position_checklist_lines(
    state: Dict[str, Any],
    pos: Dict[str, Any],
    meta: Dict[str, Any],
    items: List[Dict[str, Any]],
    profile: "LayoutProfile",
    max_width: int,
) -> List[str]:
    """Checklist view for when a position is open: exit header + strategy items."""
    curr_price = float(state.get("current_price", 0.0))
    sl = float(pos.get("stop_loss", 0.0))
    entry = float(pos.get("entry_price", 0.0))
    unr_pnl = float(pos.get("unrealized_pnl", 0.0))
    unr_pct = float(pos.get("unrealized_pnl_pct", 0.0))
    reason = str(meta.get("reason") or "")
    side = str(pos.get("position_side") or "LONG").upper()
    leverage = float(pos.get("leverage") or 1.0)
    market_type = str(pos.get("market_type") or "SPOT").upper()
    feed_health = str(pos.get("feed_health") or "OK").upper()

    chk_ok = f"{fg_rgb(52, 211, 153)}[✓]{RESET}"
    chk_err = f"{fg_rgb(251, 113, 133)}[✗]{RESET}"
    pnl_color = C_GREEN if unr_pnl >= 0 else C_RED
    pnl_sign = "+" if unr_pnl >= 0 else ""
    sl_ok = sl > 0 and (curr_price < sl if side == "SHORT" else curr_price > sl)

    if profile.is_phone:
        pnl_str = f"{pnl_color}{pnl_sign}{unr_pnl:,.2f} ({unr_pct:+.1f}%){C_RESET}"
        sl_str = f"{chk_ok if sl_ok else chk_err} SL {C_RED}{sl:,.2f}{C_RESET}"
        hdr = [_fit_checklist_line(f"  {side} {leverage:.0f}x {pnl_str}  {sl_str}", max_width)]
        return hdr + _render_strategy_checklist(items, profile, max_width)

    hdr: List[str] = []
    hdr.append(_fit_checklist_line(
        f"  {C_PRIMARY}{side} {leverage:.0f}x {market_type}{C_RESET}  Feed: {feed_health}",
        max_width,
    ))
    if entry > 0:
        hdr.append(_fit_checklist_line(
            f"  {C_MUTED}Entry{C_RESET} {C_PRIMARY}{entry:,.2f}{C_RESET}"
            f"  {pnl_color}{pnl_sign}{unr_pnl:,.4f} USDT ({unr_pct:+.2f}%){C_RESET}",
            max_width,
        ))
    if sl > 0:
        hdr.append(_fit_checklist_line(
            f"  {chk_ok if sl_ok else chk_err} SL Active  : "
            f"{C_PRIMARY}{curr_price:,.2f}{C_RESET} vs {C_RED}{sl:,.2f}{C_RESET}",
            max_width,
        ))
    if reason and not profile.is_phone:
        hdr.append(_fit_checklist_line(f"  {C_MUTED}{reason}{C_RESET}", max_width))
    hdr.append("")
    return hdr + _render_strategy_checklist(items, profile, max_width)


def get_performance_lines(db: IDatabaseRepository, symbol: str, is_mobile: bool) -> List[str]:
    trades = db.get_closed_trades(symbol, limit=1000)
    try:
        from xauby.runtime.architecture_config import per_symbol_execution_mode
        import yaml

        with open("bot_config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        if per_symbol_execution_mode(cfg):
            sim_trades = [t for t in trades if (t.get("execution_mode") or "live") == "sim"]
            live_trades = [t for t in trades if (t.get("execution_mode") or "live") == "live"]
            sim_stats = compute_trade_stats(sim_trades)
            live_stats = compute_trade_stats(live_trades)
            lines: List[str] = []
            for label, stats in (("Sim", sim_stats), ("Live", live_stats)):
                net = stats["net_pnl"]
                pnl_color = C_GREEN if net >= 0 else C_RED
                sign = "+" if net >= 0 else ""
                lines.append(
                    f"  {C_MUTED}{label} PnL{C_RESET}: {pnl_color}{sign}{net:,.4f} USDT{C_RESET} "
                    f"({stats['total']} trades, {stats['win_rate']:.1f}% WR)"
                )
            if not lines:
                lines.append(f"  {C_MUTED}No closed trades yet.{C_RESET}")
            lines.append(" ")
            return lines
    except Exception:
        pass
    total = len(trades)
    
    if total == 0:
        return [
            f"  {C_MUTED}Session Trades: {C_PRIMARY}0{C_RESET}  │  {C_MUTED}Win Rate: {C_PRIMARY}0.0%{C_RESET}",
            f"  {C_MUTED}Net Session P&L: {C_PRIMARY}0.0000 USDT{C_RESET}",
            f"  {C_MUTED}No trading statistics available for the session yet.{C_RESET}",
            " "
        ]
        
    stats = compute_trade_stats(trades)
    
    wins = stats["wins"]
    losses = stats["losses"]
    net_pnl = stats["net_pnl"]
    win_rate = stats["win_rate"]
    avg_pnl_pct = stats["avg_pnl_pct"]
    pf_str = stats["profit_factor_str"]
    
    pnl_color = C_GREEN if net_pnl >= 0 else C_RED
    sign = "+" if net_pnl >= 0 else ""
    
    lines = []
    if is_mobile:
        lines.append(f"  {C_MUTED}Trades : {C_PRIMARY}{total}{C_RESET} │ {C_MUTED}Win Rate: {C_PRIMARY}{win_rate:.1f}%{C_RESET}")
        lines.append(f"  {C_MUTED}Net P&L: {pnl_color}{sign}{net_pnl:,.2f} USDT ({avg_pnl_pct:+.2f}%){C_RESET}")
        lines.append(f"  {C_MUTED}Pr.Fact: {C_PRIMARY}{pf_str}{C_RESET} │ {C_MUTED}Wins/Losses: {C_GREEN}{wins}{C_RESET}/{C_RED}{losses}{C_RESET}")
    else:
        lines.append(f"  {C_MUTED}Session Trades : {C_PRIMARY}{total:<5}{C_RESET}  │  {C_MUTED}Win Rate: {C_PRIMARY}{win_rate:.1f}%{C_RESET} ({C_GREEN}{wins} W{C_RESET} / {C_RED}{losses} L{C_RESET})")
        lines.append(f"  {C_MUTED}Net Session PnL: {pnl_color}{sign}{net_pnl:,.4f} USDT{C_RESET} {C_MUTED}({avg_pnl_pct:+.2f}% avg){C_RESET}")
        lines.append(f"  {C_MUTED}Profit Factor  : {C_PRIMARY}{pf_str:<5}{C_RESET}  │  {C_MUTED}Average Trade: {pnl_color}{net_pnl/total:+,.2f} USDT{C_RESET}")
        
    timeline = stats.get("timeline_str", "")
    if timeline:
        if is_mobile:
            lines.append(f"  {C_MUTED}Recent : {timeline}")
        else:
            lines.append(f"  {C_MUTED}Recent History: {timeline}")
    else:
        lines.append(" ")
        
    return lines


def _print_recent_trades_table(
    closed_trades: List[Dict[str, Any]],
    W: int,
    limit: int,
    border_color: str,
    *,
    show_symbol: bool = False,
    is_phone: bool = False,
) -> None:
    """Render recent closed trades; optional Symbol column for multi-pair."""
    title = f"{C_BOLD}{C_PRIMARY}RECENT TRADES (ALL PAIRS):{RESET}" if show_symbol else (
        f"{C_BOLD}{C_PRIMARY}RECENT TRADES:{RESET}" if is_phone
        else f"{C_BOLD}{C_PRIMARY}RECENT CLOSED TRADES:{RESET}"
    )
    print_row(title, W, border_color=border_color)
    if not closed_trades:
        from xauby.ui.copy import format_history_empty
        msg = format_history_empty(compact=is_phone)
        print_row(msg, W, border_color=border_color)
        return
    if is_phone:
        if show_symbol:
            header = f"{'Sym':<6} | {'Date':<10} | {'P&L':<10}"
        else:
            header = f"{'Date':<11} | {'Entry->Exit':<11} | {'P&L':<8}"
        print_row("  " + header, W, border_color=border_color)
        for t in closed_trades[:limit]:
            dt = format_to_ict(t.get("closed_at", ""))[:10]
            pnl_val = t.get("net_pnl", 0.0)
            pnl_pct_val = t.get("net_pnl_pct", 0.0)
            pnl_str = format_pnl_compact(pnl_val, pnl_pct_val)
            if show_symbol:
                sym = str(t.get("symbol", ""))[:6]
                print_row(f"  {sym:<6} | {dt:<10} | {pnl_str}", W, border_color=border_color)
            else:
                entry = t.get("entry_price", 0.0)
                exit_p = t.get("exit_price", 0.0)
                prices = f"{to_compact(entry)}->{to_compact(exit_p)}"[:11]
                print_row(f"  {dt:<11} | {prices:<11} | {pnl_str}", W, border_color=border_color)
    else:
        if show_symbol:
            header = f"{'Symbol':<8} │ {'Date (ICT)':<14} │ {'Entry':<8} │ {'Exit':<8} │ {'Net P&L':<18}"
        else:
            header = f"{'Date (ICT)':<15} │ {'Size':<8} │ {'Entry':<8} │ {'Exit':<8} │ {'Net P&L':<20}"
        print_row(f"  {C_MUTED}{header}{C_RESET}", W, border_color=border_color)
        print_row("  " + f"{C_DARK}" + "─" * min(W - 8, 60) + f"{C_RESET}", W, border_color=border_color)
        for t in closed_trades[:limit]:
            dt = format_to_ict(t.get("closed_at", ""))
            entry = f"{t.get('entry_price', 0.0):,.1f}"[:8]
            exit_p = f"{t.get('exit_price', 0.0):,.1f}"[:8]
            pnl_str = format_pnl(t.get("net_pnl", 0.0), t.get("net_pnl_pct", 0.0))
            if show_symbol:
                sym = str(t.get("symbol", "????"))[:8]
                row_str = (
                    f"  {C_PRIMARY}{sym:<8}{C_RESET} {C_DARK}│{C_RESET} "
                    f"{C_PRIMARY}{dt:<14}{C_RESET} {C_DARK}│{C_RESET} "
                    f"{C_PRIMARY}{entry:<8}{C_RESET} {C_DARK}│{C_RESET} "
                    f"{C_PRIMARY}{exit_p:<8}{C_RESET} {C_DARK}│{C_RESET} {pnl_str}"
                )
            else:
                size = f"{t.get('amount', 0):,.4f}"[:8]
                row_str = (
                    f"  {C_PRIMARY}{dt:<15}{C_RESET} {C_DARK}│{C_RESET} {C_PRIMARY}{size:<8}{C_RESET} "
                    f"{C_DARK}│{C_RESET} {C_PRIMARY}{entry:<8}{C_RESET} {C_DARK}│{C_RESET} "
                    f"{C_PRIMARY}{exit_p:<8}{C_RESET} {C_DARK}│{C_RESET} {pnl_str}"
                )
            print_row(row_str, W, border_color=border_color)
