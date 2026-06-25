"""Tests for SimBroker paper fills."""
import os
import tempfile
import unittest

from xauby.engine.brokers.sim_broker import SimBroker


class TestSimBroker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.broker = SimBroker(balance_file=self.tmp.name, initial_balance=1000.0)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_buy_debits_balance(self):
        r = self.broker.execute_buy("BTCUSDT", qty=0.01, price=50000.0, notional=500.0)
        self.assertTrue(r.success)
        self.assertLess(self.broker.get_usdt_balance(), 1000.0)

    def test_sell_credits_balance(self):
        self.broker.execute_buy("BTCUSDT", qty=0.01, price=50000.0, notional=500.0)
        bal_after_buy = self.broker.get_usdt_balance()
        r = self.broker.execute_sell("BTCUSDT", qty=0.01, price=51000.0, entry_price=50000.0, entry_cost=500.0)
        self.assertTrue(r.success)
        self.assertGreater(self.broker.get_usdt_balance(), bal_after_buy)

    def test_insufficient_balance_rejected(self):
        r = self.broker.execute_buy("BTCUSDT", qty=1.0, price=50000.0, notional=50000.0)
        self.assertFalse(r.success)

    def test_ledger_persists_across_restart(self):
        self.broker.execute_buy("BTCUSDT", qty=0.01, price=50000.0, notional=500.0)
        self.broker.update_unrealized("BTCUSDT", 51000.0)
        restarted = SimBroker(balance_file=self.tmp.name, initial_balance=1000.0)
        ledger = restarted.get_ledger("BTCUSDT")
        self.assertEqual(ledger.entry_price, 50000.0)
        self.assertEqual(ledger.quantity, 0.01)
        self.assertGreater(ledger.unrealized_pnl, 0.0)


if __name__ == "__main__":
    unittest.main()
