"""Lightweight in-process event bus for observability subscribers."""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any, Callable, DefaultDict, Dict, List, Optional

logger = logging.getLogger("xauby.observability.event_bus")

Subscriber = Callable[[str, str, Dict[str, Any]], None]


class EventBus:
    """Thread-safe pub/sub; subscribers must not import engine modules."""

    def __init__(self):
        self._subs: DefaultDict[str, List[Subscriber]] = defaultdict(list)
        self._global: List[Subscriber] = []
        self._lock = threading.Lock()

    def subscribe(self, event_type: str, handler: Subscriber) -> None:
        with self._lock:
            self._subs[event_type].append(handler)

    def subscribe_all(self, handler: Subscriber) -> None:
        with self._lock:
            self._global.append(handler)

    def dispatch(
        self,
        event_type: str,
        symbol: str = "",
        **fields: Any,
    ) -> None:
        payload = {k: v for k, v in fields.items() if v is not None}
        handlers: List[Subscriber] = []
        with self._lock:
            handlers.extend(self._global)
            handlers.extend(self._subs.get(event_type, []))
            handlers.extend(self._subs.get("*", []))
        for handler in handlers:
            try:
                handler(event_type, symbol, payload)
            except Exception as exc:
                logger.debug("event bus handler error: %s", exc)


_BUS: Optional[EventBus] = None
_BUS_LOCK = threading.Lock()


def get_event_bus() -> EventBus:
    global _BUS
    with _BUS_LOCK:
        if _BUS is None:
            _BUS = EventBus()
        return _BUS
