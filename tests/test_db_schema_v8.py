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
                self.assertIn("exchange_close_id", cols)
                self.assertIn("exchange_position_id", cols)
                self.assertIn("pnl_source", cols)
                self.assertIn("pnl_confirmed", cols)
                self.assertIn("funding_fee", cols)
                self.assertIn("management_mode", state_cols)
                self.assertIn("exchange_position_id", state_cols)
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
                self.assertIsNotNone(
                    conn.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name='exchange_close_reconciliations'"
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

    def test_strategy_handoff_transition_is_conditional_and_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = LiteDB(os.path.join(tmp, "test.db"))
            db.save_trade_state(
                symbol="XAUUSDT",
                state="bought",
                entry_price=4000.0,
                quantity=0.04,
                management_mode="strategy_handoff",
            )

            self.assertTrue(
                db.transition_management_mode(
                    "XAUUSDT", expected="strategy_handoff", target="strategy"
                )
            )
            self.assertEqual(db.get_trade_state("XAUUSDT").management_mode, "strategy")
            self.assertFalse(
                db.transition_management_mode(
                    "XAUUSDT", expected="strategy_handoff", target="strategy"
                )
            )


if __name__ == "__main__":
    unittest.main()
