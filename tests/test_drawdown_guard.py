"""Tests for the portfolio drawdown circuit-breaker (RiskMixin)."""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from xauby.engine.risk import RiskMixin


def _stub(config):
    e = type("E", (RiskMixin,), {})()
    e.config = config
    return e


_GUARD_ON = {"risk": {"drawdown_guard": {"enabled": True, "max_drawdown_pct": 20.0}}}


class TestDrawdownGuard(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.mkdtemp(prefix="ddtest_")
        self._env = mock.patch.dict(os.environ, {"XAUBY_HOME": self._home}, clear=False)
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def test_disabled_always_allows(self):
        e = _stub({"risk": {"drawdown_guard": {"enabled": False}}})
        self.assertEqual(e.check_drawdown_guard(10.0), (True, ""))
        # No guard block at all.
        e2 = _stub({})
        self.assertEqual(e2.check_drawdown_guard(10.0), (True, ""))

    def test_allows_above_threshold_blocks_at_threshold(self):
        e = _stub(_GUARD_ON)
        e.update_equity_peak(1000.0)
        self.assertTrue(e.check_drawdown_guard(900.0)[0])   # -10%
        self.assertTrue(e.check_drawdown_guard(801.0)[0])   # -19.9%
        allowed, reason = e.check_drawdown_guard(800.0)     # -20% exactly
        self.assertFalse(allowed)
        self.assertIn("Drawdown guard", reason)
        self.assertFalse(e.check_drawdown_guard(700.0)[0])  # -30%

    def test_peak_only_grows(self):
        e = _stub(_GUARD_ON)
        e.update_equity_peak(1000.0)
        e.update_equity_peak(900.0)  # dip does not lower the peak
        self.assertEqual(e.update_equity_peak(900.0), 1000.0)
        self.assertEqual(e.update_equity_peak(1200.0), 1200.0)  # new high

    def test_peak_persists_across_restart(self):
        e = _stub(_GUARD_ON)
        e.update_equity_peak(1000.0)
        # Fresh instance (no in-memory cache) reads the persisted peak.
        e2 = _stub(_GUARD_ON)
        self.assertFalse(e2.check_drawdown_guard(750.0)[0])  # -25% from persisted 1000
        self.assertAlmostEqual(e2.drawdown_pct(750.0), 25.0)

    def test_zero_threshold_disables_block(self):
        e = _stub({"risk": {"drawdown_guard": {"enabled": True, "max_drawdown_pct": 0.0}}})
        e.update_equity_peak(1000.0)
        self.assertTrue(e.check_drawdown_guard(100.0)[0])  # never blocks

    def test_non_positive_equity_allows(self):
        e = _stub(_GUARD_ON)
        self.assertEqual(e.check_drawdown_guard(0.0), (True, ""))

    def test_per_symbol_consecutive_loss_override(self):
        e = _stub(
            {
                "trading": {
                    "max_consecutive_losses": 2,
                    "max_consecutive_losses_by_symbol": {"SOLUSDT": 3},
                }
            }
        )
        e._sym = lambda: "SOLUSDT"
        closed_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        e.db = mock.Mock()
        e.db.get_closed_trades.return_value = [
            {"closed_at": closed_at, "net_pnl": -2.0},
            {"closed_at": closed_at, "net_pnl": -1.0},
        ]
        self.assertEqual(e.check_daily_protections(1000.0, "SOLUSDT"), (True, ""))

    def test_consecutive_losses_are_scoped_by_execution_mode(self):
        e = _stub({"trading": {"max_consecutive_losses": 2}})
        e._sym = lambda: "SOLUSDT"
        e._execution_mode = lambda symbol=None: "live"
        closed_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        e.db = mock.Mock()
        e.db.get_closed_trades.return_value = [
            {"closed_at": closed_at, "net_pnl": -2.0, "execution_mode": "sim"},
            {"closed_at": closed_at, "net_pnl": -1.0, "execution_mode": "sim"},
        ]
        self.assertEqual(e.check_daily_protections(1000.0, "SOLUSDT"), (True, ""))

        e._execution_mode = lambda symbol=None: "sim"
        allowed, reason = e.check_daily_protections(1000.0, "SOLUSDT")
        self.assertFalse(allowed)
        self.assertIn("max consecutive losses (sim)", reason)

    def test_daily_loss_is_scoped_by_execution_mode(self):
        e = _stub({"trading": {"max_daily_loss_pct": 1.0}})
        e._sym = lambda: "SOLUSDT"
        e._execution_mode = lambda symbol=None: "live"
        closed_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        e.db = mock.Mock()
        e.db.get_closed_trades.return_value = [
            {"closed_at": closed_at, "net_pnl": -50.0, "execution_mode": "sim"},
            {"closed_at": closed_at, "net_pnl": -50.0, "execution_mode": "sim"},
        ]
        self.assertEqual(e.check_daily_protections(1000.0, "SOLUSDT"), (True, ""))

        e._execution_mode = lambda symbol=None: "sim"
        allowed, reason = e.check_daily_protections(1000.0, "SOLUSDT")
        self.assertFalse(allowed)
        self.assertIn("Daily PnL (sim)", reason)


if __name__ == "__main__":
    unittest.main()
