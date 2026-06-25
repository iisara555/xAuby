"""Incident Explorer — list runs, summarize timelines, format event traces."""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from xauby.observability.replay import replay_incident
from xauby.utils.common import format_ts_ict

# regime_classified fires every tick once the router is active; the notable
# timeline surfaces regime_changed instead, so the raw per-tick classification
# is treated as noise to keep the incident explorer readable.
NOISE_EVENT_TYPES = frozenset({"tick", "heartbeat", "regime_classified"})

TIMELINE_FILTER_MODES = ("notable", "trade", "all")

TRADE_FOCUS_ACTIONS = frozenset({"BUY", "SELL"})
TRADE_FOCUS_EVENT_TYPES = frozenset({"order_filled"})

FILTER_LABELS: Dict[str, str] = {
    "notable": "Notable",
    "trade": "Trade (BUY, SELL, ORDER_FILLED)",
    "all": "All events",
}


def _event_type(ev: Dict[str, Any]) -> str:
    return str(ev.get("event_type") or ev.get("event") or "")


def _event_action(ev: Dict[str, Any]) -> str:
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    return str(payload.get("action") or ev.get("action") or "").upper()


def is_trade_focus_event(ev: Dict[str, Any]) -> bool:
    """BUY/SELL signals and order_filled fills only."""
    if not isinstance(ev, dict):
        return False
    etype = _event_type(ev).lower()
    if etype in TRADE_FOCUS_EVENT_TYPES:
        return True
    if etype == "signal_evaluated":
        return _event_action(ev) in TRADE_FOCUS_ACTIONS
    return False


def is_notable_event(ev: Dict[str, Any]) -> bool:
    """Return True if an event is worth surfacing in the explorer (non-noise)."""
    if not isinstance(ev, dict):
        return False
    etype = ev.get("event_type") or ev.get("event") or ""
    if etype in NOISE_EVENT_TYPES:
        return False
    if etype == "signal_evaluated":
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        action = payload.get("action") or ev.get("action") or "HOLD"
        if str(action).upper() == "HOLD":
            return False
    return True


def filter_timeline_events(
    events: List[Dict[str, Any]],
    filter_mode: str,
) -> List[Dict[str, Any]]:
    """Apply timeline filter: notable | trade | all."""
    mode = (filter_mode or "notable").lower()
    if mode == "all":
        return list(events)
    if mode == "trade":
        return [ev for ev in events if is_trade_focus_event(ev)]
    return [ev for ev in events if is_notable_event(ev)]


def cycle_timeline_filter(current: str) -> str:
    modes = TIMELINE_FILTER_MODES
    cur = (current or "notable").lower()
    if cur not in modes:
        cur = "notable"
    idx = modes.index(cur)
    return modes[(idx + 1) % len(modes)]


def format_filter_status(
    filter_mode: str,
    *,
    shown: int,
    total: int,
) -> str:
    """English status line for the active timeline filter and counts."""
    label = FILTER_LABELS.get((filter_mode or "notable").lower(), filter_mode)
    mode = (filter_mode or "notable").lower()
    if mode == "all":
        return f"Filter: {label} · {total} event(s)"
    return f"Filter: {label} · {shown} of {total} event(s)"


def list_runs(
    store,
    *,
    limit: int = 20,
    symbol: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return recent engine runs with event counts (newest first)."""
    try:
        db = store._ensure_db() if hasattr(store, "_ensure_db") else store
        return db.list_event_runs(limit=limit, symbol=symbol)
    except Exception:
        return []


def load_run_timeline(
    store,
    run_id: str,
    *,
    limit: int = 500,
    notable_only: bool = False,
    filter_mode: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Chronological events for a run_id."""
    events = replay_incident(store, run_id, limit=limit)
    mode = filter_mode
    if mode is None:
        mode = "notable" if notable_only else "all"
    return filter_timeline_events(events, mode)


def summarize_run(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a compact summary for a run timeline."""
    if not events:
        return {
            "total": 0,
            "notable": 0,
            "types": {},
            "started_at": "",
            "ended_at": "",
            "symbol": "",
            "mode": "",
        }
    types = Counter(
        (ev.get("event_type") or ev.get("event") or "?") for ev in events
    )
    notable = sum(1 for ev in events if is_notable_event(ev))
    first, last = events[0], events[-1]
    return {
        "total": len(events),
        "notable": notable,
        "types": dict(types),
        "started_at": first.get("ts", ""),
        "ended_at": last.get("ts", ""),
        "symbol": first.get("symbol") or last.get("symbol") or "",
        "mode": first.get("mode") or last.get("mode") or "",
    }


def format_event_payload(ev: Dict[str, Any], max_len: int = 48) -> str:
    """Compact payload string for TUI / CLI."""
    payload = ev.get("payload") or {}
    if isinstance(payload, str):
        return payload[:max_len]
    parts: List[str] = []
    for key in (
        "reason",
        "action",
        "old_regime",
        "new_regime",
        "regime",
        "old_strategy",
        "new_strategy",
        "strategy_activated",
        "price",
        "entry",
        "exit",
        "pnl",
        "trigger",
        "symbol",
        "mode",
        "score",
        "threshold",
        "old_sl",
        "new_sl",
        "error",
        "context",
        "side",
        "order_id",
        "order_type",
        "notional",
    ):
        val = ev.get(key)
        if val is None and isinstance(payload, dict):
            val = payload.get(key)
        if val is not None:
            if key == "reason":
                val = str(val)[:32]
            parts.append(f"{key}={val}")
    detail = " ".join(parts)
    return detail[:max_len] if detail else ""


def format_timeline_line(
    ev: Dict[str, Any],
    *,
    width: int = 100,
    show_tick: bool = True,
) -> str:
    """Single formatted timeline row (no ANSI — caller adds color)."""
    seq = ev.get("seq", "?")
    ts = format_ts_ict(ev.get("ts") or "", fmt="%m-%d %H:%M:%S")
    etype = (ev.get("event_type") or ev.get("event") or "?")[:22]
    tick = ev.get("tick_id") or "-"
    detail = format_event_payload(ev, max_len=max(20, width - 58))
    if show_tick and tick != "-":
        tick_short = tick.split("-")[-1] if "-" in tick else tick
        head = f"{ts} #{seq:<4} t{tick_short:<4} {etype:<22}"
    else:
        head = f"{ts} #{seq:<4} {etype:<22}"
    room = max(0, width - len(head) - 2)
    if len(detail) > room:
        detail = detail[: max(0, room - 1)] + "…"
    return f"{head} {detail}".rstrip()
