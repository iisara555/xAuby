"""Perpetual-swap funding accrual in the backtest simulator."""
from __future__ import annotations

import unittest

from xauby.observability.replay import PositionSimulator, SimPosition
from xauby.strategies.signal import buy


class TestFundingAccrual(unittest.TestCase):
    def _long_sim(self, **kw):
        sim = PositionSimulator(initial_balance=1000.0, fee_pct=0.0, slippage_bps=0.0,
                                risk_pct=0.1, sl_atr_mult=2.0, **kw)
        sim.try_open(entry_price=100.0, atr=5.0, bar_time=0, signal=buy("e", volatility=5.0))
        return sim

    def test_zero_rate_is_noop(self):
        sim = self._long_sim(funding_rate_8h=0.0, bar_hours=4.0)
        self.assertEqual(sim.accrue_funding(), 0.0)
        self.assertEqual(sim.position.funding_paid, 0.0)

    def test_long_pays_funding(self):
        sim = self._long_sim(funding_rate_8h=0.0001, bar_hours=8.0)
        notional = sim.position.entry_price * sim.position.qty
        inc = sim.accrue_funding()
        # 8h bar = one full funding window -> notional * rate.
        self.assertAlmostEqual(inc, notional * 0.0001)
        self.assertAlmostEqual(sim.position.funding_paid, notional * 0.0001)

    def test_funding_scales_with_bar_hours(self):
        sim = self._long_sim(funding_rate_8h=0.0001, bar_hours=4.0)
        notional = sim.position.entry_price * sim.position.qty
        inc = sim.accrue_funding()
        self.assertAlmostEqual(inc, notional * 0.0001 * 0.5)  # 4h = half a window

    def test_funding_reduces_realized_pnl(self):
        sim = self._long_sim(funding_rate_8h=0.0001, bar_hours=8.0)
        notional = sim.position.entry_price * sim.position.qty
        sim.accrue_funding()
        sim.accrue_funding()  # two windows held
        bal_before = sim.balance
        sim._close(exit_price=100.0, bar_time=10, trigger="SIGNAL")  # flat price
        # No price move, no fees -> the only PnL is the funding cost paid.
        self.assertAlmostEqual(sim.balance, bal_before - 2 * notional * 0.0001)

    def test_short_receives_funding(self):
        # Forward-compat: a short position (side=-1) earns funding.
        sim = PositionSimulator(initial_balance=1000.0, fee_pct=0.0,
                                funding_rate_8h=0.0001, bar_hours=8.0)
        sim.position = SimPosition(entry_price=100.0, stop_loss=105.0,
                                   highest_price=100.0, qty=1.0, entry_time=0)
        sim.position.side = -1  # type: ignore[attr-defined]
        inc = sim.accrue_funding()
        self.assertLess(inc, 0.0)  # credit, not cost


if __name__ == "__main__":
    unittest.main()
