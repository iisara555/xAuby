"""Dual-backend event store: JSONL write-ahead + SQLite index."""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from xauby.observability.events import Event
from xauby.runtime.paths import events_dir as _events_dir

logger = logging.getLogger("xauby.observability.store")


class EventStore:
    """Append-only event store with JSONL durability and SQLite query index."""

    def __init__(
        self,
        db=None,
        jsonl_dir: Optional[str] = None,
        db_path: Optional[str] = None,
    ):
        from xauby.database.db import resolve_db_path
        self._db = db
        self._db_path = db_path or resolve_db_path()
        self._jsonl_dir = jsonl_dir or _events_dir()
        self._lock = threading.Lock()
        self._seq = 0
        self._run_id = ""
        os.makedirs(self._jsonl_dir, exist_ok=True)

    def set_run_id(self, run_id: str) -> None:
        self._run_id = run_id
        self._seq = 0

    def _ensure_db(self):
        if self._db is None:
            from xauby.database.db import LiteDB
            self._db = LiteDB(self._db_path)
        return self._db

    def _jsonl_path(self, ts: Optional[str] = None) -> str:
        day = (ts or datetime.now(timezone.utc).isoformat())[:10].replace("-", "")
        return os.path.join(self._jsonl_dir, f"events-{day}.jsonl")

    def append(
        self,
        event_type: str,
        symbol: str,
        mode: str,
        run_id: str,
        payload: Optional[Dict[str, Any]] = None,
        tick_id: Optional[str] = None,
    ) -> Event:
        with self._lock:
            if run_id != self._run_id:
                self._run_id = run_id
                self._seq = 0
            self._seq += 1
            seq = self._seq

            ev = Event(
                event_type=event_type,
                symbol=symbol,
                mode=mode,
                run_id=run_id,
                seq=seq,
                payload=dict(payload or {}),
                tick_id=tick_id,
            )

            row = ev.to_dict()
            line = json.dumps(row, ensure_ascii=False)

            try:
                with open(self._jsonl_path(ev.ts), "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                logger.warning(
                    "EventStore JSONL append failed for %s seq=%s",
                    event_type,
                    seq,
                    exc_info=True,
                )

            try:
                db = self._ensure_db()
                db.insert_event(
                    run_id=ev.run_id,
                    seq=ev.seq,
                    ts=ev.ts,
                    event_type=ev.event_type,
                    symbol=ev.symbol,
                    mode=ev.mode,
                    payload=json.dumps(ev.payload, ensure_ascii=False),
                    tick_id=ev.tick_id,
                )
            except Exception:
                logger.warning(
                    "EventStore SQLite insert failed for %s seq=%s",
                    event_type,
                    seq,
                    exc_info=True,
                )

            return ev

    def query(
        self,
        *,
        symbol: Optional[str] = None,
        event_type: Optional[str] = None,
        run_id: Optional[str] = None,
        since_ts: Optional[str] = None,
        until_ts: Optional[str] = None,
        limit: int = 100,
        order: str = "desc",
    ) -> List[Dict[str, Any]]:
        try:
            db = self._ensure_db()
            return db.query_events(
                symbol=symbol,
                event_type=event_type,
                run_id=run_id,
                since_ts=since_ts,
                until_ts=until_ts,
                limit=limit,
                order=order,
            )
        except Exception:
            return []

    def recent(self, limit: int = 20, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.query(symbol=symbol, limit=limit)

    def latest_ts(self, symbol: Optional[str] = None) -> Optional[str]:
        rows = self.query(symbol=symbol, limit=1)
        return rows[0]["ts"] if rows else None

    def iter_jsonl(self, path: str) -> Iterator[Dict[str, Any]]:
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    def list_runs(
        self,
        limit: int = 20,
        symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        try:
            db = self._ensure_db()
            return db.list_event_runs(limit=limit, symbol=symbol)
        except Exception:
            return []

    def iter_run(self, run_id: str, limit: int = 10_000) -> List[Dict[str, Any]]:
        rows = self.query(run_id=run_id, limit=limit, order="asc")
        if not rows:
            return []
        return rows

    def prune(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        """Prune old JSONL files and SQLite event rows."""
        from xauby.utils.retention import prune_jsonl_events, prune_sqlite_events

        jsonl_stats = prune_jsonl_events(cfg, jsonl_dir=self._jsonl_dir)
        db = self._ensure_db()
        sqlite_stats = prune_sqlite_events(db, cfg)
        return {"jsonl": jsonl_stats, "sqlite": sqlite_stats}
