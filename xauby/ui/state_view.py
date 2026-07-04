"""Helpers for multi-pair bot state JSON (schema v2)."""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from xauby.storage.interface import IDatabaseRepository

from xauby.runtime.paths import dashboard_focus_path

FOCUS_FILE = dashboard_focus_path()


# Absolute last-resort default symbol, used only when the whitelist is empty or
# unreadable. Asset-neutral and overridable via env so the UI is not hardwired to
# any particular instrument.
LEGACY_DEFAULT_SYMBOL = os.environ.get("XAUBY_DEFAULT_SYMBOL", "XAUTUSDT")


def default_symbol_from_whitelist(project_root: str = ".") -> str:
    """Return the first whitelist symbol (enabled preferred), else a neutral default."""
    path = os.path.join(project_root, "coin_whitelist.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        quote = str(data.get("quote_asset") or "USDT").upper()
        assets = [a for a in (data.get("assets") or []) if isinstance(a, dict)]
        # Prefer enabled pairs, but fall back to any defined pair before the
        # asset-neutral default.
        for require_enabled in (True, False):
            for asset in assets:
                if require_enabled and not asset.get("enabled", True):
                    continue
                base = str(asset.get("symbol", "")).upper().replace("_", "").strip()
                if not base:
                    continue
                return base if base.endswith(quote) else f"{base}{quote}"
    except Exception:
        pass
    return LEGACY_DEFAULT_SYMBOL


def _load_json_with_retry(path: str, retries: int = 3, backoff_ms: float = 10.0) -> Any:
    """Load JSON from *path* with retry on decode races."""
    last_exc: Exception = Exception("unknown")
    for attempt in range(retries):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError) as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(backoff_ms / 1000.0 * (attempt + 1))
    raise last_exc


def load_raw_state(state_file: str) -> Dict[str, Any]:
    return _load_json_with_retry(state_file)


def load_dashboard_focus_request(project_root: str = ".") -> Optional[str]:
    """Local focus override written by the TUI (applied by engine on next state write)."""
    path = FOCUS_FILE if os.path.isabs(FOCUS_FILE) else os.path.join(project_root, FOCUS_FILE)
    if not os.path.isfile(path):
        return None
    try:
        data = _load_json_with_retry(path) or {}
        sym = str(data.get("focus_symbol", "")).upper().replace("_", "")
        return sym or None
    except Exception:
        return None


def resolve_dashboard_state(
    raw: Dict[str, Any],
    project_root: str = ".",
) -> Tuple[Dict[str, Any], Dict[str, Any], str, List[str]]:
    """
    Returns (focus_snapshot, envelope, focus_symbol, active_pair_list).
    envelope has aggregate + by_symbol when v2.
    """
    if int(raw.get("schema_version", 1) or 1) >= 2 and raw.get("by_symbol"):
        by_symbol = raw.get("by_symbol") or {}
        pairs = list(raw.get("pairs") or by_symbol.keys())
        default_symbol = default_symbol_from_whitelist(project_root)
        focus = str(raw.get("focus_symbol") or raw.get("symbol") or (pairs[0] if pairs else default_symbol))
        focus = focus.upper().replace("_", "")
        override = load_dashboard_focus_request(project_root)
        if override and override in by_symbol:
            focus = override
        elif focus not in by_symbol and pairs:
            focus = pairs[0]
        focus_snap = dict(by_symbol.get(focus, raw))
        focus_snap.setdefault("symbol", focus)
        envelope = {
            "aggregate": raw.get("aggregate") or {},
            "by_symbol": by_symbol,
            "pairs": pairs,
        }
        return focus_snap, envelope, focus, pairs

    sym = str(raw.get("symbol") or default_symbol_from_whitelist(project_root)).upper().replace("_", "")
    return raw, {}, sym, [sym]


def write_focus_request(symbol: str, project_root: str = ".") -> None:
    sym = symbol.upper().replace("_", "")
    path = FOCUS_FILE if os.path.isabs(FOCUS_FILE) else os.path.join(project_root, FOCUS_FILE)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"focus_symbol": sym}, f)
    os.replace(tmp, path)


def cycle_focus(
    pairs: List[str], current: str, direction: int = 1
) -> str:
    if not pairs:
        return current
    try:
        idx = pairs.index(current.upper().replace("_", ""))
    except ValueError:
        idx = 0
    idx = (idx + direction) % len(pairs)
    return pairs[idx]


def format_pairs_table_lines(
    envelope: Dict[str, Any],
    focus_symbol: str,
    width: int,
    *,
    is_phone: bool = False,
) -> List[str]:
    """Compact multi-pair status rows."""
    from xauby.ui.widgets import pad_line
    from xauby.utils.colors import C_MUTED, C_PRIMARY, C_GREEN, C_YELLOW, C_RESET, C_BOLD

    by_symbol = envelope.get("by_symbol") or {}
    if len(by_symbol) <= 1:
        return []

    lines: List[str] = []
    agg = envelope.get("aggregate") or {}
    n = len(by_symbol)
    open_p = agg.get("open_positions", "?")
    lines.append(
        pad_line(
            f"{C_MUTED}Pairs ({n}){C_RESET}  open={open_p}  "
            f"{C_MUTED}[ / ] focus{C_RESET}",
            width,
        )
    )
    for sym in sorted(by_symbol.keys()):
        snap = by_symbol[sym]
        mark = f"{C_BOLD}>{C_RESET}" if sym == focus_symbol else " "
        tf = snap.get("primary_timeframe", "?")
        price = float(snap.get("current_price") or 0)
        pos = (snap.get("position") or {}).get("state", "idle")
        act = (snap.get("signal_meta") or {}).get("action", "—")
        reg = (snap.get("regime") or {}).get("regime", "—")
        mode_badge = ""
        try:
            from xauby.runtime.architecture_config import per_symbol_execution_mode
            import yaml

            with open("bot_config.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            if per_symbol_execution_mode(cfg):
                mode = str(snap.get("execution_mode") or "")
                if mode == "live":
                    mode_badge = f"{C_GREEN}[LIVE]{C_RESET} "
                elif mode == "sim":
                    mode_badge = f"{C_YELLOW}[SIM]{C_RESET} "
        except Exception:
            pass
        pos_c = C_GREEN if pos == "bought" else C_MUTED
        exp = float((snap.get("equity_breakdown") or {}).get("symbol_exposure_usdt", 0) or 0)
        mg = snap.get("macro_guard") or {}
        if not mg.get("applies_to_symbol", True):
            g_lbl = "N/A"
        elif mg.get("blocks_buy"):
            g_lbl = "BLK"
        elif mg.get("enabled"):
            g_lbl = f"{float(mg.get('score', 0)):+.1f}"[:5]
        else:
            g_lbl = "off"
        if is_phone and sym != focus_symbol:
            lines.append(
                pad_line(
                    f" {mark} {mode_badge}{C_PRIMARY}{sym:10}{C_RESET} {tf:4} "
                    f"{price:9.2f} {pos_c}{pos:5}{C_RESET} {act:4} G:{g_lbl:4}",
                    width,
                )
            )
        else:
            lines.append(
                pad_line(
                    f" {mark} {mode_badge}{C_PRIMARY}{sym:10}{C_RESET} {tf:4} "
                    f"{price:9.2f} {pos_c}{pos:5}{C_RESET} {act:4} "
                    f"exp:{exp:6.0f} G:{g_lbl:4}",
                    width,
                )
            )
    return lines


def _base_from_symbol(sym: str) -> str:
    s = sym.upper().replace("_", "")
    if s.endswith("USDT"):
        return s[:-4]
    return s


def _format_portfolio_thb(amount: float) -> str:
    return f"{amount:,.0f}B"


def format_static_portfolio_lines(
    envelope: Dict[str, Any],
    width: int,
    *,
    is_phone: bool = False,
) -> List[str]:
    """Wallet-level portfolio summary for multi-pair static shell."""
    from xauby.ui.widgets import pad_line
    from xauby.utils.colors import C_MUTED, C_PRIMARY, C_GREEN, C_YELLOW, C_RESET, C_BOLD, C_DARK

    agg = envelope.get("aggregate") or {}
    by_symbol = envelope.get("by_symbol") or {}
    total = float(agg.get("total_equity_usdt", 0) or 0)
    usdt_bal = 0.0
    base_vals: Dict[str, float] = {}

    for sym, snap in by_symbol.items():
        if not isinstance(snap, dict):
            continue
        bd = snap.get("equity_breakdown") or {}
        if not usdt_bal and bd.get("usdt_balance_usdt") is not None:
            usdt_bal = float(bd.get("usdt_balance_usdt", 0) or 0)
        port = snap.get("portfolio") or {}
        if not usdt_bal and port.get("USDT") is not None:
            usdt_bal = float(port.get("USDT", 0) or 0)
        base = _base_from_symbol(sym)
        qty = float(port.get(base, 0) or bd.get("base_quantity", 0) or 0)
        price = float(snap.get("current_price", 0) or 0)
        base_vals[base] = qty * price if price > 0 else 0.0

    if total <= 0:
        total = usdt_bal + sum(base_vals.values())

    try:
        from xauby.utils.currency import usdt_to_thb, format_thb
        thb_total = usdt_to_thb(total)
        thb_cash = usdt_to_thb(usdt_bal) if usdt_bal > 0 else 0.0
    except Exception:
        thb_total = 0.0
        thb_cash = 0.0

    # ── allocation bar ────────────────────────────────────────────────────
    bar_w = min(10 if is_phone else 14, max(6, width - 16))
    segments: List[tuple] = []
    if usdt_bal > 0:
        segments.append(("USDT", usdt_bal, C_GREEN))
    for base in sorted(base_vals.keys()):
        v = base_vals[base]
        if v > 0 or base in {_base_from_symbol(s) for s in by_symbol}:
            segments.append((base, v, C_YELLOW))
    seg_total = sum(v for _, v, _ in segments) or total or 1.0
    bar_parts: List[str] = []
    label_parts: List[str] = []
    filled = 0
    for i, (label, val, color) in enumerate(segments):
        chars = bar_w - filled if i == len(segments) - 1 else max(0, min(int(round((val / seg_total) * bar_w)), bar_w - filled))
        filled += chars
        if chars > 0:
            bar_parts.append(f"{color}{'█' * chars}{C_RESET}")
        pct = (val / seg_total) * 100 if seg_total else 0
        if is_phone:
            label_parts.append(f"{color}{label[:1]}{pct:.0f}%{C_RESET}")
        else:
            label_parts.append(f"{color}{label} {pct:.0f}%{C_RESET}")
    if not bar_parts:
        bar_parts.append(f"{C_GREEN}{'█' * bar_w}{C_RESET}")
    bar_str = f"[{''.join(bar_parts)}]"
    alloc_str = "  ".join(label_parts)

    lines: List[str] = []

    if is_phone:
        # ── phone: compact lines with cash balance ────────────────────────
        thb_str = f"/{_format_portfolio_thb(thb_total)}" if thb_total > 0 else ""
        cash_str = f"  {C_MUTED}C:{C_RESET}{C_GREEN}{usdt_bal:,.0f}U{C_RESET}" if usdt_bal > 0 else ""
        lines.append(pad_line(
            f"  {C_BOLD}Eq{C_RESET} {C_GREEN}{total:,.0f}U{C_RESET}{C_MUTED}{thb_str}{C_RESET}"
            f"  {bar_str} {alloc_str}{cash_str}",
            width,
        ))
    elif width < 80:
        # ── compact (60-79): 2 lines, values right-aligned on header ─────
        thb_text = _format_portfolio_thb(thb_total) if thb_total > 0 else ""
        thb_str = f"  {C_MUTED}{thb_text}{C_RESET}" if thb_text else ""
        total_str = f"{C_GREEN}{total:,.2f} U{C_RESET}{thb_str}"
        # raw lengths (strip ANSI codes is hard; use estimates)
        title_raw = "PORTFOLIO"
        total_raw_len = len(f"{total:,.2f} U") + (len(thb_text) + 2 if thb_text else 0)
        pad = max(1, width - 2 - len(title_raw) - total_raw_len)
        lines.append(pad_line(
            f"  {C_BOLD}{C_PRIMARY}{title_raw}{C_RESET}{' ' * pad}{total_str}",
            width,
        ))
        lines.append(pad_line(f"  {bar_str}  {alloc_str}", width))
    else:
        # ── desktop (≥80): 3 lines — header+total, bar+alloc, cash ──────
        thb_str = f"  {C_MUTED}{format_thb(thb_total)}{C_RESET}" if thb_total > 0 else ""
        total_str = f"{C_GREEN}{total:,.2f} USDT{C_RESET}{thb_str}"
        title_raw = "PORTFOLIO"
        total_raw_len = len(f"{total:,.2f} USDT") + (2 + len(format_thb(thb_total)) if thb_total > 0 else 0)
        pad = max(1, width - 2 - len(title_raw) - total_raw_len)
        lines.append(pad_line(
            f"  {C_BOLD}{C_PRIMARY}{title_raw}{C_RESET}{' ' * pad}{total_str}",
            width,
        ))
        div_len = min(width - 4, 24)
        lines.append(pad_line(f"  {C_DARK}{'─' * div_len}{C_RESET}", width))
        lines.append(pad_line(f"  {bar_str}  {alloc_str}", width))
        if usdt_bal > 0:
            cash_thb = f"  {C_MUTED}{format_thb(thb_cash)}{C_RESET}" if thb_cash > 0 else ""
            lines.append(pad_line(
                f"  {C_MUTED}Cash  {usdt_bal:,.2f} USDT{cash_thb}{C_RESET}",
                width,
            ))
    return lines


def format_all_positions_lines(
    by_symbol: Dict[str, Any],
    width: int,
    *,
    is_phone: bool = False,
) -> List[str]:
    """All-pair position summary for static shell."""
    from xauby.ui.widgets import get_quote_asset, pad_line
    from xauby.utils.colors import (
        C_MUTED, C_PRIMARY, C_GREEN, C_RED, C_RESET, C_BOLD, C_BG_GREEN, C_GEMINI_CYAN,
    )

    lines: List[str] = []
    if not by_symbol:
        if not is_phone:
            lines.append(pad_line(f"{C_BOLD}{C_PRIMARY}POSITIONS (ALL PAIRS){C_RESET}", width))
            lines.append(pad_line(f"  {C_MUTED}No pairs in state.{C_RESET}", width))
        return lines

    has_open = any(
        (snap.get("position") or {}).get("state") == "bought"
        for snap in by_symbol.values()
        if isinstance(snap, dict)
    )
    if is_phone and not has_open:
        return lines

    lines.append(pad_line(f"{C_BOLD}{C_PRIMARY}POSITIONS - LIVE MARK{C_RESET}", width))

    if not has_open:
        from xauby.ui.copy import format_monitoring_empty, PAIR_WATCHING

        if is_phone:
            return lines
        for ln in format_monitoring_empty(compact=False, with_subline=True).split("\n"):
            lines.append(pad_line(ln, width))
        return lines

    try:
        from xauby.utils.currency import usdt_to_thb, format_thb
        _thb_ok = True
    except Exception:
        _thb_ok = False

    for sym in sorted(by_symbol.keys()):
        snap = by_symbol[sym]
        if not isinstance(snap, dict):
            continue
        pos = snap.get("position") or {}
        st = pos.get("state", "idle")
        if st == "bought":
            side = str(pos.get("position_side") or "LONG").upper()
            leverage = float(pos.get("leverage") or 1.0)
            market_type = str(pos.get("market_type") or "SPOT").upper()
            side_color = C_RED if side == "SHORT" else C_GREEN
            st_disp = f"{side_color}{C_BOLD} {side} {leverage:.0f}x {market_type} {C_RESET}"
            qty = float(pos.get("quantity", 0) or 0)
            entry = float(pos.get("entry_price", 0) or 0)
            mark = float(snap.get("current_price", 0) or 0)
            bid = float(snap.get("bid", 0) or 0)
            ask = float(snap.get("ask", 0) or 0)
            sl = float(pos.get("stop_loss", 0) or 0)
            peak = float(pos.get("highest_price_seen", 0) or 0)
            pnl = float(pos.get("unrealized_pnl", 0) or 0)
            pnl_pct = float(pos.get("unrealized_pnl_pct", 0) or 0)
            partial_tp_pct = float(pos.get("partial_tp_pct", 0) or 0)
            partial_tp_fraction = float(pos.get("partial_tp_fraction", 0) or 0)
            partial_tp_trigger = float(pos.get("partial_tp_trigger_price", 0) or 0)
            partial_tp_taken = bool(pos.get("partial_tp_taken"))
            if pnl_pct == 0 and entry > 0 and mark > 0:
                direction = -1.0 if side == "SHORT" else 1.0
                pnl_pct = direction * ((mark - entry) / entry) * 100
            if mark > 0 and sl > 0:
                sl_gap_pct = ((sl - mark) / mark * 100) if side == "SHORT" else ((mark - sl) / mark * 100)
            else:
                sl_gap_pct = 0.0
            pnl_c = C_GREEN if pnl >= 0 else C_RED
            sl_c = C_GREEN if sl_gap_pct > 0 else C_RED
            mark_str = f"{mark:,.2f}" if mark > 0 else "-"
            entry_str = f"{entry:,.2f}" if entry > 0 else "-"
            sl_str = f"{sl:,.2f}" if sl > 0 else "-"
            peak_str = f"{peak:,.2f}" if peak > 0 else "-"
            partial_tp_str = f"{partial_tp_trigger:,.2f}" if partial_tp_trigger > 0 else "-"
            partial_tp_label = ""
            if partial_tp_pct > 0 and 0 < partial_tp_fraction < 1:
                status = "banked" if partial_tp_taken else "pending"
                status_color = C_GREEN if partial_tp_taken else C_MUTED
                partial_tp_label = (
                    f"PTP {partial_tp_fraction * 100:.0f}% @ {partial_tp_str} "
                    f"({partial_tp_pct:.1f}%, {status_color}{status}{C_RESET})"
                )
            extreme_label = "Low" if side == "SHORT" else "Peak"
            bidask = ""
            if bid > 0 and ask > 0 and not is_phone:
                bidask = f" {C_MUTED}B/A {bid:,.2f}/{ask:,.2f}{C_RESET}"
            pnl_thb_str = ""
            if _thb_ok and pnl != 0:
                try:
                    thb_pnl = usdt_to_thb(pnl)
                    pnl_thb_str = f" {C_MUTED}({format_thb(thb_pnl, compact=is_phone)}){C_RESET}"
                except Exception:
                    # Keep the USDT value visible when no real conversion rate
                    # has ever been observed; never substitute a guessed rate.
                    pass
            quote_label = str(snap.get("quote_asset") or get_quote_asset(sym)).upper()
            if is_phone:
                lines.append(
                    pad_line(
                        f"  {C_PRIMARY}{sym}{C_RESET} {st_disp} Mark {mark_str} "
                        f"{pnl_c}{pnl:+,.2f} {quote_label} ({pnl_pct:+.2f}%){C_RESET} "
                        f"SL {sl_str} ({sl_gap_pct:+.2f}%)",
                        width,
                    )
                )
                if partial_tp_label:
                    lines.append(pad_line(f"  {C_MUTED}{partial_tp_label}{C_RESET}", width))
            else:
                lines.append(
                    pad_line(
                        f"  {C_PRIMARY}{sym:10}{C_RESET} {st_disp} "
                        f"Mark {C_BOLD}{mark_str}{C_RESET} "
                        f"{pnl_c}uPnL {pnl:+,.2f} {quote_label} ({pnl_pct:+.2f}%){C_RESET}{pnl_thb_str}",
                        width,
                    )
                )
                if bidask:
                    lines.append(pad_line(f"    {bidask.strip()}", width))
                lines.append(
                    pad_line(
                        f"    Qty {qty:.8f} @ {entry_str}",
                        width,
                    )
                )
                if partial_tp_label:
                    lines.append(pad_line(f"    {partial_tp_label}", width))
                lines.append(
                    pad_line(
                        f"    SL {sl_c}{sl_str}{C_RESET} ({sl_gap_pct:+.2f}% to SL) | {extreme_label} {peak_str}",
                        width,
                    )
                )
        elif not is_phone:
            from xauby.ui.copy import PAIR_WATCHING

            lines.append(
                pad_line(
                    f"  {C_PRIMARY}{sym:10}{C_RESET} {C_GEMINI_CYAN}{PAIR_WATCHING}{C_RESET}",
                    width,
                )
            )
    return lines


def regime_router_banner_lines(envelope: Dict[str, Any], width: int) -> List[str]:
    """Persistent banner + live-gate warnings for RegimeRouter on LIVE symbols.

    Spec: show a persistent banner whenever any LIVE symbol has
    ``regime_router_enabled: true``, plus any live-gate warning text the engine
    exported (forced-sim fallback when not confirmed).
    """
    from xauby.ui.widgets import pad_line
    from xauby.utils.colors import C_RED, C_YELLOW, C_RESET, C_BOLD

    by_symbol = envelope.get("by_symbol") or {}
    live_router_syms: List[str] = []
    warnings: List[str] = []
    for sym in sorted(by_symbol.keys()):
        snap = by_symbol[sym]
        if not isinstance(snap, dict):
            continue
        rr = snap.get("regime_router") or {}
        mode = str(snap.get("execution_mode") or "")
        if rr.get("enabled") and mode == "live":
            live_router_syms.append(sym)
        warn = str(rr.get("warning") or "")
        if warn:
            warnings.append(warn)

    lines: List[str] = []
    if live_router_syms:
        joined = ", ".join(live_router_syms)
        lines.append(
            pad_line(
                f"{C_BOLD}{C_RED}[!] RegimeRouter LIVE active on: {joined} — monitor closely{C_RESET}",
                width,
            )
        )
    for warn in warnings:
        lines.append(pad_line(f"{C_YELLOW}{warn}{C_RESET}", width))
    return lines


def format_regime_history_lines(
    db: "IDatabaseRepository",
    symbol: Optional[str] = None,
    limit: int = 10,
    width: int = 80,
) -> List[str]:
    """Recent regime_history rows for the TUI (read-only query)."""
    from xauby.ui.widgets import pad_line
    from xauby.utils.colors import C_MUTED, C_PRIMARY, C_BOLD, C_RESET

    lines: List[str] = [pad_line(f"{C_BOLD}{C_PRIMARY}REGIME HISTORY{C_RESET}", width)]
    try:
        rows = db.get_regime_history(symbol, limit=limit)
    except Exception:
        rows = []
    if not rows:
        lines.append(pad_line(f"  {C_MUTED}No regime changes recorded yet.{C_RESET}", width))
        return lines
    for row in rows:
        ts = str(row.get("timestamp", ""))[:19]
        sym = str(row.get("symbol", ""))
        old_r = str(row.get("old_regime", "") or "—")
        new_r = str(row.get("new_regime", ""))
        strat = str(row.get("strategy_activated", "") or "—")
        conf = row.get("confidence", 0.0)
        try:
            conf_s = f"{float(conf):.2f}"
        except (TypeError, ValueError):
            conf_s = "?"
        lines.append(
            pad_line(
                f"  {C_MUTED}{ts}{C_RESET} {C_PRIMARY}{sym:9}{C_RESET} "
                f"{old_r} → {new_r}  [{strat}] conf={conf_s}",
                width,
            )
        )
    return lines


def merge_closed_trades(
    db: "IDatabaseRepository",
    symbols: Optional[List[str]] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Recent closed trades across pairs, newest first."""
    fetch_n = max(limit * 10, 50)
    rows = db.get_closed_trades(None, limit=fetch_n)
    if symbols:
        allowed = {str(s).upper().replace("_", "") for s in symbols}
        rows = [
            t
            for t in rows
            if str(t.get("symbol", "")).upper().replace("_", "") in allowed
        ]

    def _sort_key(t: Dict[str, Any]) -> str:
        return str(t.get("closed_at") or "")

    rows.sort(key=_sort_key, reverse=True)
    return rows[:limit]
