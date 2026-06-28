"""DB schema v8 migration tests."""
import os
import sqlite3
import tempfile
import unittest

from xauby.database.db import LiteDB, SCHEMA_VERSION


class TestSchemaV8Migration(unittest.TestCase):
    def test_v8_tables_and_execution_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.db")
            db = LiteDB(path)
            conn = sqlite3.connect(path)
            try:
                ver = conn.execute("PRAGMA user_version").fetchone()[0]
                self.assertEqual(ver, SCHEMA_VERSION)
                cols = [r[1] for r in conn.execute("PRAGMA table_info(closed_trades)").fetchall()]
                state_cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_states)").fetchall()]
                self.assertIn("execution_mode", cols)
                self.assertIn("management_mode", state_cols)
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='regime_history'"
                )
                self.assertIsNotNone(
                    conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='regime_history'"
                    ).fetchone()
                )
                self.assertIsNotNone(
                    conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='strategy_handoffs'"
                    ).fetchone()
                )
            finally:
                conn.close()

    def test_save_trade_state_accepts_symbol_keyword(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = LiteDB(os.path.join(tmp, "test.db"))

            db.save_trade_state(
                symbol="BTCUSDT",
                state="bought",
                entry_price=64000.0,
                stop_loss=63000.0,
                take_profit=0.0,
                highest_price_seen=64000.0,
                quantity=0.001,
                opened_at="2026-06-14T00:00:00",
                last_transition_at="2026-06-14T00:00:00",
            )

            state = db.get_trade_state("BTCUSDT")
            self.assertEqual(state.state, "bought")
            self.assertEqual(state.quantity, 0.001)
            self.assertEqual(state.management_mode, "strategy")

    def test_save_trade_state_persists_management_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = LiteDB(os.path.join(tmp, "test.db"))

            db.save_trade_state(
                symbol="BTCUSDT",
                state="bought",
                entry_price=64000.0,
                quantity=0.001,
                management_mode="manual",
            )

            state = db.get_trade_state("BTCUSDT")
            self.assertEqual(state.management_mode, "manual")


if __name__ == "__main__":
    unittest.main()
