"""Event coverage catalog and run integrity audits."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set

from xauby.observability.events import ALL_EVENT_TYPES, EventType


# Events the engine is wired to emit (see xauby/engine/trading.py).
ENGINE_WIRED_EVENTS: Dict[str, str] = {
    EventType.ENGINE_STARTED: "Engine boot",
    EventType.ENGINE_STOPPED: "Engine shutdown",
    EventType.TICK: "Main loop tick",
    EventType.HEARTBEAT: "Periodic heartbeat",
    EventType.ERROR: "Engine / order errors",
    EventType.SIGNAL_EVALUATED: "Strategy signal output",
    EventType.SIGNAL_REJECTED: "BUY blocked after strategy signal",
    EventType.RISK_CHECK_PASSED: "Pre-order risk sizing OK",
    EventType.GUARD_BLOCKED: "Macro sentiment guard block",
    EventType.POSITION_OPENED: "Position entry recorded",
    EventType.POSITION_CLOSED: "Position exit recorded",
    EventType.STOP_LOSS_UPDATED: "Trailing / BE stop move",
    EventType.STOP_LOSS_TRIGGERED: "Stop loss breach",
    EventType.WS_DISCONNECTED: "WebSocket disconnect",
    EventType.WS_RECONNECTED: "WebSocket reconnect",
    EventType.ORDER_SUBMITTED: "Order sent (live or paper)",
    EventType.ORDER_FILLED: "Order fill (live or paper)",
    EventType.COOLDOWN_BLOCKED: "Loss/WS cooldown block",
    EventType.RISK_REJECTED: "Risk protection/sizing reject",
    EventType.ORDER_FAILED: "Exchange order submission fail",
    EventType.REGIME_CHANGED: "Confirmed regime change (router)",
    EventType.STRATEGY_SWITCHED: "RegimeRouter strategy switch",
    EventType.NO_TRADE_ENTERED: "RegimeRouter NO_TRADE state",
    EventType.HANDOFF_COMPLETED: "Strategy handoff completed",
    EventType.REGIME_ROUTER_LIVE_BLOCKED: "Live router forced to sim (gate)",
}

# Always expected once a run has started ticking.
RUN_BASELINE_TYPES = frozenset(
    {
        EventType.ENGINE_STARTED,
        EventType.TICK,
        EventType.SIGNAL_EVALUATED,
    }
)

# Contextual — only when the situation occurs.
RUN_CONTEXTUAL_TYPES = frozenset(set(ENGINE_WIRED_EVENTS) - RUN_BASELINE_TYPES)


def _etype(ev: Dict[str, Any]) -> str:
    return str(ev.get("event_type") or ev.get("event") or "")


def _payload(ev: Dict[str, Any]) -> Dict[str, Any]:
    p = ev.get("payload")
    return p if isinstance(p, dict) else {}


@dataclass
class EventCoverageReport:
    run_id: str
    observed: Dict[str, int] = field(default_factory=dict)
    wired_total: int = len(ENGINE_WIRED_EVENTS)
    wired_seen: int = 0
    wired_missing: List[str] = field(default_factory=list)
    unknown_types: List[str] = field(default_factory=list)
    coverage_pct: float = 0.0
    integrity_ok: bool = True
    integrity_issues: List[str] = field(default_factory=list)
    tick_pairs_ok: bool = True
    seq_monotonic_ok: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "observed": dict(self.observed),
            "wired_total": self.wired_total,
            "wired_seen": self.wired_seen,
            "wired_missing": list(self.wired_missing),
            "unknown_types": list(self.unknown_types),
            "coverage_pct": round(self.coverage_pct, 1),
            "integrity_ok": self.integrity_ok,
            "integrity_issues": list(self.integrity_issues),
            "tick_pairs_ok": self.tick_pairs_ok,
            "seq_monotonic_ok": self.seq_monotonic_ok,
        }


def audit_event_coverage(events: List[Dict[str, Any]], run_id: str = "") -> EventCoverageReport:
    """Audit which wired event types appear in a run and check basic integrity."""
    report = EventCoverageReport(run_id=run_id or (events[0].get("run_id") if events else ""))
    if not events:
        report.integrity_ok = False
        report.integrity_issues.append("no_events")
        return report

    counts = Counter(_etype(ev) for ev in events)
    report.observed = dict(counts)

    seen_wired: Set[str] = set()
    for etype in counts:
        if etype in ENGINE_WIRED_EVENTS:
            seen_wired.add(etype)
        elif etype and etype not in ALL_EVENT_TYPES:
            report.unknown_types.append(etype)

    report.wired_seen = len(seen_wired)
    report.wired_missing = sorted(set(ENGINE_WIRED_EVENTS) - seen_wired)
    report.coverage_pct = (report.wired_seen / report.wired_total * 100.0) if report.wired_total else 0.0

    # Monotonic seq
    seqs = [int(ev.get("seq") or 0) for ev in events if ev.get("seq") is not None]
    if seqs and seqs != sorted(seqs):
        report.seq_monotonic_ok = False
        report.integrity_issues.append("seq_not_monotonic")

    # tick_id pairing for signal_evaluated
    tick_ids = {
        str(ev.get("tick_id"))
        for ev in events
        if _etype(ev) == EventType.TICK and ev.get("tick_id")
    }
    orphan_signals = 0
    for ev in events:
        if _etype(ev) != EventType.SIGNAL_EVALUATED:
            continue
        tid = str(ev.get("tick_id") or "")
        if tid and tid not in tick_ids:
            orphan_signals += 1
    if orphan_signals:
        report.tick_pairs_ok = False
        report.integrity_issues.append(f"signal_without_tick:{orphan_signals}")

    # position_opened after live/paper buy should follow risk_check or order_filled
    risk_or_fill_seqs = {
        int(ev.get("seq") or 0)
        for ev in events
        if _etype(ev) in (EventType.RISK_CHECK_PASSED, EventType.ORDER_FILLED)
    }
    for ev in events:
        if _etype(ev) != EventType.POSITION_OPENED:
            continue
        seq = int(ev.get("seq") or 0)
        if risk_or_fill_seqs and not any(r < seq for r in risk_or_fill_seqs):
            report.integrity_issues.append(f"position_opened_without_precursor:seq={seq}")

    # order_filled should have order_submitted with same side earlier
    submitted_sides: List[tuple] = []
    for ev in events:
        et = _etype(ev)
        pl = _payload(ev)
        side = str(pl.get("side") or ev.get("side") or "").upper()
        seq = int(ev.get("seq") or 0)
        if et == EventType.ORDER_SUBMITTED and side:
            submitted_sides.append((seq, side))
        elif et == EventType.ORDER_FILLED and side:
            if not any(s < seq and sd == side for s, sd in submitted_sides):
                report.integrity_issues.append(f"order_filled_without_submit:{side}@seq={seq}")

    report.integrity_ok = len(report.integrity_issues) == 0
    return report
