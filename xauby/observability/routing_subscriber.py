"""Event-bus subscriber that records RegimeRouter (P2) events.

Decoupled from the engine: it consumes routing events via the in-process
:class:`EventBus` (no direct engine import) and keeps a small ring buffer that
operator surfaces (TUI / state export) can read without touching engine
internals. Registration is gated by ``architecture.event_bus_enabled``.
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from xauby.observability.event_bus import EventBus, get_event_bus
from xauby.observability.events import EventType

ROUTING_EVENT_TYPES = (
    EventType.REGIME_CHANGED,
    EventType.STRATEGY_SWITCHED,
    EventType.NO_TRADE_ENTERED,
    EventType.HANDOFF_COMPLETED,
    EventType.REGIME_ROUTER_LIVE_BLOCKED,
)


class RoutingEventRecorder:
    """Keep a bounded log of routing events received from the event bus."""

    def __init__(self, ring_size: int = 100):
        self._ring: Deque[Dict[str, Any]] = deque(maxlen=max(1, int(ring_size)))
        self._lock = threading.Lock()

    def handle(self, event_type: str, symbol: str, payload: Dict[str, Any]) -> None:
        rec = {"event": event_type, "symbol": symbol, **(payload or {})}
        with self._lock:
            self._ring.append(rec)

    def recent(self, limit: int = 20, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        sym = symbol.upper().replace("_", "") if symbol else None
        with self._lock:
            rows = list(self._ring)
        if sym:
            rows = [r for r in rows if str(r.get("symbol", "")).upper().replace("_", "") == sym]
        return rows[-limit:] if limit > 0 else []

    def register(self, bus: Optional[EventBus] = None) -> "RoutingEventRecorder":
        target = bus or get_event_bus()
        for event_type in ROUTING_EVENT_TYPES:
            target.subscribe(event_type, self.handle)
        return self
