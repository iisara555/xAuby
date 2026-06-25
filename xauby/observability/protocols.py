"""Engine-agnostic protocol definitions.

Observability must never import concrete engine classes. Engines implement
these protocols (structurally) so health, replay, and state export stay
portable.
"""
from __future__ import annotations

from typing import Any, Dict, Protocol, runtime_checkable


@runtime_checkable
class EngineProtocol(Protocol):
    """Minimal surface any trading engine should expose to observability."""

    symbol: str
    simulate_only: bool
    read_only: bool
    config: Dict[str, Any]

    def get_state_snapshot(self) -> Dict[str, Any]:
        """Return a JSON-serializable snapshot for dashboard / health."""
        ...

    def get_recent_events(self, limit: int = 20) -> list:
        ...
