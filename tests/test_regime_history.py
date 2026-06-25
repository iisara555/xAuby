"""Regime history DB tests."""
import os
import tempfile
import unittest

from xauby.database.db import LiteDB


class TestRegimeHistory(unittest.TestCase):
    def test_insert_and_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "test.db")
            db = LiteDB(path)
            db.insert_regime_history(
                "BTCUSDT",
                old_regime="SIDEWAYS_CHOP",
                new_regime="BULL_BREAKOUT",
                candles_confirmed=3,
                strategy_activated="cdc_action_zone",
                confidence=0.85,
            )
            self.assertEqual(db.count_regime_history("BTCUSDT"), 1)
            rows = db.get_regime_history("BTCUSDT", limit=5)
            self.assertEqual(rows[0]["new_regime"], "BULL_BREAKOUT")


if __name__ == "__main__":
    unittest.main()
