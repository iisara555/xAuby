"""EventEmitter facade — best-effort, never raises."""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from xauby.observability.events import Event
from xauby.observability.logging import get_run_id
from xauby.observability.store import EventStore

logger = logging.getLogger("xauby.observability.emitter")


class EventEmitter:
    """Unified event emission: log line + JSONL + SQLite + ring buffer."""

    def __init__(
        self,
        store: Optional[EventStore] = None,
        symbol: str = "",
        mode: str = "paper",
        run_id: Optional[str] = None,
        ring_size: int = 100,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.store = store or EventStore()
        self.symbol = symbol
        self.mode = mode
        self.run_id = run_id or get_run_id()
        self.store.set_run_id(self.run_id)
        self._ring_size = max(1, int(ring_size))
        self._ring: Deque[Dict[str, Any]] = deque(maxlen=self._ring_size)
        self._ring_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._tick_id: Optional[str] = None
        cfg = config or {}
        arch_cfg = cfg.get("architecture") or {}
        obs_cfg = cfg.get("observability") or {}
        self._event_bus_enabled = bool(arch_cfg.get("event_bus_enabled", True))
        self._durable_high_frequency = bool(
            obs_cfg.get("durable_high_frequency_events", True)
        )
        self._high_frequency_types = {
            "tick",
            "regime_classified",
            *(str(x) for x in obs_cfg.get("high_frequency_event_types", []) or []),
        }
        self.last_store_ms = 0
        self._local_seq = 0

    def configure(self, symbol: str, mode: str, run_id: Optional[str] = None) -> None:
        with self._state_lock:
            self.symbol = symbol
            self.mode = mode
            if run_id:
                self.run_id = run_id
                self.store.set_run_id(run_id)

    def set_tick_id(self, tick_id: str) -> None:
        with self._state_lock:
            self._tick_id = tick_id

    def emit(self, event_type: str, symbol: Optional[str] = None, **fields: Any) -> None:
        try:
            payload = {k: v for k, v in fields.items() if v is not None}
            with self._state_lock:
                sym = (symbol or self.symbol or "").upper().replace("_", "")
                mode = self.mode
                run_id = self.run_id
                tick_id = self._tick_id
                durable = self._durable_high_frequency or event_type not in self._high_frequency_types
            started = time.monotonic()
            if durable:
                ev = self.store.append(
                    event_type=event_type,
                    symbol=sym,
                    mode=mode,
                    run_id=run_id,
                    payload=payload,
                    tick_id=tick_id,
                )
                store_ms = int((time.monotonic() - started) * 1000)
                with self._state_lock:
                    self.last_store_ms = store_ms
            else:
                with self._state_lock:
                    self._local_seq += 1
                    seq = self._local_seq
                    self.last_store_ms = 0
                ev = Event(
                    event_type=event_type,
                    symbol=sym,
                    mode=mode,
                    run_id=run_id,
                    seq=seq,
                    payload=payload,
                    tick_id=tick_id,
                )
            kv = "｜".join(f"{k}={v}" for k, v in payload.items())
            if durable:
                logger.info(f"EVENT｜{event_type}" + (f"｜{kv}" if kv else ""))

            rec = ev.to_dict()
            with self._ring_lock:
                self._ring.append(rec)

            try:
                from xauby.observability.event_bus import get_event_bus
                if self._event_bus_enabled:
                    get_event_bus().dispatch(event_type, sym, **payload)
            except Exception:
                pass
        except Exception:
            pass

    def recent(self, limit: int = 20, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        sym = symbol.upper().replace("_", "") if symbol else None
        with self._ring_lock:
            if self._ring:
                rows = list(self._ring)
                if sym:
                    rows = [
                        r
                        for r in rows
                        if str(r.get("symbol", "")).upper().replace("_", "") == sym
                    ]
                return rows[-limit:] if limit > 0 else []
        return self.store.recent(limit=limit, symbol=sym)
