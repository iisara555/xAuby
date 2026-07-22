"""Canonical event types and the Event record model."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def new_run_id() -> str:
    """Return a unique run identifier for this engine process."""
    return uuid.uuid4().hex[:12]


class EventType:
    """Canonical event type constants."""

    # Engine lifecycle
    ENGINE_STARTED = "engine_started"
    ENGINE_STOPPED = "engine_stopped"
    TICK = "tick"
    HEARTBEAT = "heartbeat"
    ERROR = "error"

    # Signal / decision
    SIGNAL_EVALUATED = "signal_evaluated"
    SIGNAL_REJECTED = "signal_rejected"
    RISK_CHECK_PASSED = "risk_check_passed"
    GUARD_BLOCKED = "guard_blocked"
    COOLDOWN_BLOCKED = "cooldown_blocked"
    RISK_REJECTED = "risk_rejected"
    ORDER_FAILED = "order_failed"

    # Position lifecycle
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    STOP_LOSS_UPDATED = "stop_loss_updated"
    STOP_LOSS_TRIGGERED = "stop_loss_triggered"

    # Connectivity
    WS_DISCONNECTED = "ws_disconnected"
    WS_RECONNECTED = "ws_reconnected"
    FEED_STALE = "feed_stale"

    # Orders
    ORDER_SUBMITTED = "order_submitted"
    ORDER_FILLED = "order_filled"
    REVERSE_OPEN_DEFERRED = "reverse_open_deferred"

    # Regime / routing (P2)
    REGIME_CLASSIFIED = "regime_classified"
    REGIME_CHANGED = "regime_changed"
    STRATEGY_SWITCHED = "strategy_switched"
    NO_TRADE_ENTERED = "no_trade_entered"
    HANDOFF_COMPLETED = "handoff_completed"
    REGIME_ROUTER_LIVE_BLOCKED = "regime_router_live_blocked"


ALL_EVENT_TYPES = frozenset(
    v for k, v in vars(EventType).items() if not k.startswith("_") and isinstance(v, str)
)


@dataclass
class Event:
    """Immutable event record stored in JSONL + SQLite."""

    event_type: str
    symbol: str
    mode: str  # "paper" | "live"
    run_id: str
    seq: int
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    payload: Dict[str, Any] = field(default_factory=dict)
    tick_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["event"] = self.event_type
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        return cls(
            event_type=data.get("event_type") or data.get("event", ""),
            symbol=data.get("symbol", ""),
            mode=data.get("mode", "paper"),
            run_id=data.get("run_id", ""),
            seq=int(data.get("seq", 0)),
            ts=data.get("ts", ""),
            payload={
                k: v
                for k, v in data.items()
                if k
                not in (
                    "event_type",
                    "event",
                    "symbol",
                    "mode",
                    "run_id",
                    "seq",
                    "ts",
                    "tick_id",
                    "payload",
                )
            }
            if "payload" not in data
            else dict(data.get("payload") or {}),
            tick_id=data.get("tick_id"),
        )
