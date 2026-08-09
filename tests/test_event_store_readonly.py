from __future__ import annotations

from pathlib import Path

import pytest

from xauby.observability.store import EventStore


def test_readonly_event_store_never_creates_or_mutates(tmp_path: Path) -> None:
    db_path = tmp_path / "missing.db"
    jsonl_dir = tmp_path / "events"
    store = EventStore(
        db_path=str(db_path),
        jsonl_dir=str(jsonl_dir),
        readonly=True,
    )

    assert not jsonl_dir.exists()
    assert store.query(limit=1) == []
    assert not db_path.exists()

    with pytest.raises(RuntimeError, match="read-only EventStore"):
        store.append("tick", "BTCUSDT", "sim", "run-1")
    with pytest.raises(RuntimeError, match="read-only EventStore"):
        store.prune({})

    assert not jsonl_dir.exists()
    assert not db_path.exists()
