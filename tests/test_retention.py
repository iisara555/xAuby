import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

from xauby.database.db import LiteDB
from xauby.utils.retention import (
    cleanup_rotated_logs,
    prune_backups,
    prune_jsonl_events,
    prune_sqlite_events,
)


class TestLogCleanup(unittest.TestCase):
    def test_deletes_stale_rotated_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            stale = os.path.join(tmp, "xauby_bot.log.1")
            fresh = os.path.join(tmp, "xauby_bot.log.2")
            open(stale, "w").close()
            open(fresh, "w").close()
            old = time.time() - 40 * 86400
            os.utime(stale, (old, old))

            stats = cleanup_rotated_logs(
                {"cleanup_on_startup": True, "debug_retention_days": 30},
                log_dir=tmp,
            )
            self.assertEqual(stats["deleted"], 1)
            self.assertFalse(os.path.exists(stale))
            self.assertTrue(os.path.exists(fresh))


class TestJsonlPrune(unittest.TestCase):
    def test_deletes_old_event_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_name = "events-20260101.jsonl"
            today = datetime.now(timezone.utc).strftime("%Y%m%d")
            new_name = f"events-{today}.jsonl"
            open(os.path.join(tmp, old_name), "w").close()
            open(os.path.join(tmp, new_name), "w").close()

            stats = prune_jsonl_events(
                {"enabled": True, "jsonl_retention_days": 30},
                jsonl_dir=tmp,
            )
            self.assertEqual(stats["deleted"], 1)
            self.assertFalse(os.path.exists(os.path.join(tmp, old_name)))
            self.assertTrue(os.path.exists(os.path.join(tmp, new_name)))


class TestBackupPrune(unittest.TestCase):
    def test_respects_max_backups_and_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            now = time.time()
            for i in range(12):
                path = os.path.join(tmp, f"crypto_bot_lite_{i:02d}.bak")
                with open(path, "w") as f:
                    f.write("x")
                os.utime(path, (now - i * 3600, now - i * 3600))
                paths.append(path)

            stats = prune_backups({
                "enabled": True,
                "max_backups": 5,
                "max_age_days": 30,
                "backup_dir": tmp,
            })
            remaining = [f for f in os.listdir(tmp) if f.endswith(".bak")]
            self.assertLessEqual(len(remaining), 5)
            self.assertGreater(stats["deleted"], 0)


class TestSqliteEventPrune(unittest.TestCase):
    def test_delete_events_before(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "test.db")
            db = LiteDB(db_path)
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    ts TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'paper',
                    payload TEXT,
                    tick_id TEXT,
                    UNIQUE(run_id, seq)
                )
            """)
            conn.execute(
                "INSERT INTO events (run_id, seq, ts, event_type, symbol, mode) VALUES (?, ?, ?, ?, ?, ?)",
                ("r1", 1, "2020-01-01T00:00:00", "tick", "XAUTUSDT", "paper"),
            )
            conn.execute(
                "INSERT INTO events (run_id, seq, ts, event_type, symbol, mode) VALUES (?, ?, ?, ?, ?, ?)",
                ("r1", 2, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), "tick", "XAUTUSDT", "paper"),
            )
            conn.commit()
            conn.close()

            cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=None).isoformat()
            deleted = db.delete_events_before(cutoff)
            self.assertEqual(deleted, 1)

            stats = prune_sqlite_events(db, {"enabled": True, "sqlite_retention_days": 1})
            self.assertGreaterEqual(stats["deleted"], 0)


class TestEmitterRingBuffer(unittest.TestCase):
    def test_ring_does_not_grow_past_max(self):
        from xauby.observability.emitter import EventEmitter
        from xauby.observability.store import EventStore

        store = EventStore(db=None, jsonl_dir=tempfile.mkdtemp())
        emitter = EventEmitter(store=store, symbol="XAUTUSDT", ring_size=5)
        for i in range(20):
            emitter.emit("tick", seq=i)
        with emitter._ring_lock:
            self.assertLessEqual(len(emitter._ring), 5)


if __name__ == "__main__":
    unittest.main()
