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
                self.assertIn("mae_pct", cols)
                self.assertIn("mfe_pct", cols)
                self.assertIn("excursion_measured", cols)
                self.assertIn("management_mode", state_cols)
                self.assertIn("exchange_position_id", state_cols)
                self.assertIn("lowest_price_seen", state_cols)
                self.assertIn("excursion_tracking_complete", state_cols)
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

    def test_v12_database_migrates_excursion_columns_without_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.db")
            LiteDB(path)
            conn = sqlite3.connect(path)
            try:
                conn.execute("ALTER TABLE trade_states DROP COLUMN lowest_price_seen")
                conn.execute("ALTER TABLE trade_states DROP COLUMN excursion_tracking_complete")
                conn.execute("ALTER TABLE closed_trades DROP COLUMN mae_pct")
                conn.execute("ALTER TABLE closed_trades DROP COLUMN mfe_pct")
                conn.execute("ALTER TABLE closed_trades DROP COLUMN excursion_measured")
                conn.execute("PRAGMA user_version=12")
                conn.commit()
            finally:
                conn.close()

            LiteDB(path)
            conn = sqlite3.connect(path)
            try:
                self.assertEqual(
                    conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION
                )
                state_cols = {
                    row[1] for row in conn.execute("PRAGMA table_info(trade_states)")
                }
                trade_cols = {
                    row[1] for row in conn.execute("PRAGMA table_info(closed_trades)")
                }
                self.assertIn("lowest_price_seen", state_cols)
                self.assertIn("excursion_tracking_complete", state_cols)
                self.assertTrue(
                    {"mae_pct", "mfe_pct", "excursion_measured"} <= trade_cols
                )
            finally:
                conn.close()

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

    def test_long_excursions_persist_on_atomic_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = LiteDB(os.path.join(tmp, "test.db"))
            db.save_trade_state(
                symbol="XAUUSDT", state="bought", entry_price=100.0,
                highest_price_seen=100.0, quantity=1.0, position_side="LONG",
            )
            self.assertTrue(db.update_position_extrema("XAUUSDT", 93.0))
            self.assertTrue(db.update_position_extrema("XAUUSDT", 112.0))
            self.assertTrue(db.close_position_atomic(
                "XAUUSDT", side="BUY", amount=1.0, entry_price=100.0,
                exit_price=108.0, entry_cost=100.0, gross_exit=108.0,
            ))
            trade = db.get_closed_trades("XAUUSDT", limit=1)[0]
            self.assertAlmostEqual(trade["mae_pct"], 7.0)
            self.assertAlmostEqual(trade["mfe_pct"], 12.0)
            self.assertEqual(trade["excursion_measured"], 1)
            self.assertEqual(db.get_trade_state("XAUUSDT").lowest_price_seen, 0.0)

    def test_short_excursions_invert_adverse_and_favourable_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = LiteDB(os.path.join(tmp, "test.db"))
            db.save_trade_state(
                symbol="BTCUSDT", state="bought", entry_price=100.0,
                highest_price_seen=100.0, quantity=1.0, position_side="SHORT",
            )
            db.update_position_extrema("BTCUSDT", 106.0)
            db.update_position_extrema("BTCUSDT", 82.0)
            self.assertTrue(db.close_position_atomic(
                "BTCUSDT", side="SHORT", amount=1.0, entry_price=100.0,
                exit_price=88.0, entry_cost=100.0, gross_exit=88.0,
            ))
            trade = db.get_closed_trades("BTCUSDT", limit=1)[0]
            self.assertAlmostEqual(trade["mae_pct"], 6.0)
            self.assertAlmostEqual(trade["mfe_pct"], 18.0)
            self.assertEqual(trade["excursion_measured"], 1)

    def test_migrated_open_position_never_claims_complete_excursion_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.db")
            LiteDB(path)
            conn = sqlite3.connect(path)
            try:
                conn.execute("ALTER TABLE trade_states DROP COLUMN lowest_price_seen")
                conn.execute("ALTER TABLE trade_states DROP COLUMN excursion_tracking_complete")
                conn.execute("ALTER TABLE closed_trades DROP COLUMN mae_pct")
                conn.execute("ALTER TABLE closed_trades DROP COLUMN mfe_pct")
                conn.execute("ALTER TABLE closed_trades DROP COLUMN excursion_measured")
                conn.execute(
                    "INSERT INTO trade_states "
                    "(symbol, state, entry_price, highest_price_seen, quantity) "
                    "VALUES ('BTCUSDT', 'bought', 100, 106, 1)"
                )
                conn.execute("PRAGMA user_version=12")
                conn.commit()
            finally:
                conn.close()

            db = LiteDB(path)
            self.assertFalse(db.get_trade_state("BTCUSDT").excursion_tracking_complete)
            db.update_position_extrema("BTCUSDT", 82.0)
            self.assertTrue(db.close_position_atomic(
                "BTCUSDT", side="SHORT", amount=1.0, entry_price=100.0,
                exit_price=88.0, entry_cost=100.0, gross_exit=88.0,
            ))
            trade = db.get_closed_trades("BTCUSDT", limit=1)[0]
            self.assertEqual(trade["excursion_measured"], 0)


if __name__ == "__main__":
    unittest.main()
