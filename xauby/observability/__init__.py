"""xauby.observability — engine-agnostic observability layer.

One-way dependency: Engine ──► observability (never the reverse).
"""
from xauby.observability.events import Event, EventType, new_run_id
from xauby.observability.logging import (
    TickContext,
    clear_tick_id,
    get_run_id,
    set_run_id,
    set_tick_id,
    setup_logging,
)
from xauby.observability.emitter import EventEmitter
from xauby.observability.store import EventStore
from xauby.observability.protocols import EngineProtocol
from xauby.observability.state import StateExporter
from xauby.observability.health import HealthMonitor
from xauby.observability.replay import (
    ContextBuilder,
    PositionSimulator,
    ReplayEngine,
    replay_incident,
)
from xauby.observability.event_coverage import (
    ENGINE_WIRED_EVENTS,
    EventCoverageReport,
    audit_event_coverage,
)
from xauby.observability.replay_validation import ReplayValidationReport, validate_run
from xauby.observability.incidents import (
    format_timeline_line,
    is_notable_event,
    list_runs,
    load_run_timeline,
    summarize_run,
)

__all__ = [
    "Event",
    "EventType",
    "EventEmitter",
    "EventStore",
    "EngineProtocol",
    "StateExporter",
    "HealthMonitor",
    "ContextBuilder",
    "PositionSimulator",
    "ReplayEngine",
    "replay_incident",
    "ReplayValidationReport",
    "validate_run",
    "ENGINE_WIRED_EVENTS",
    "EventCoverageReport",
    "audit_event_coverage",
    "format_timeline_line",
    "is_notable_event",
    "list_runs",
    "load_run_timeline",
    "summarize_run",
    "TickContext",
    "clear_tick_id",
    "get_run_id",
    "new_run_id",
    "set_run_id",
    "set_tick_id",
    "setup_logging",
]
