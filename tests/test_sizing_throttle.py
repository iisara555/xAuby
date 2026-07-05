"""Chop-defence sizing in the PositionSimulator.

Loss-streak throttle and per-bar entry scaling change ONLY the entry size —
entries, exits, per-trade pnl% and win rate must match an unthrottled run.
"""
from __future__ import annotations

import unittest

from xauby.observability.replay import PositionSimulator
from xauby.strategies.signal import hold, sell


HOUR = 3600


def _sim(**kwargs) -> PositionSimulator:
    defaults = dict(initial_balance=1000.0, fee_pct=0.0, slippage_bps=0.0,
                    disable_stop_loss=True, position_pct=1.0)
    defaults.update(kwargs)
    return PositionSimulator(**defaults)


def _round_trip(sim, entry, exit_p, t0):
    self_qty = None
    sim.on_bar(open_p=entry, high=entry, low=entry, close=entry, atr=1.0,
               zone="GREEN", bar_time=t0, signal=hold("t"))
    # open manually to control prices exactly
    if not sim.has_position:
        sim.try_open(entry, 1.0, t0)
    self_qty = sim.position.qty
    sim.on_bar(open_p=exit_p, high=exit_p, low=exit_p, close=exit_p, atr=1.0,
               zone="RED", bar_time=t0 + 4 * HOUR, signal=sell("4H zone turned RED"))
    return self_qty


class TestLossStreakThrottle(unittest.TestCase):
    def test_halves_size_after_streak_and_resets_on_win(self):
        sim = _sim(throttle_after_losses=2, throttle_scale=0.5)
        q1 = _round_trip(sim, 100.0, 99.0, 0)              # loss 1 — full size
        bal1 = sim.balance
        q2 = _round_trip(sim, 100.0, 99.0, 10 * HOUR)      # loss 2 — full size
        bal2 = sim.balance
        q3 = _round_trip(sim, 100.0, 99.0, 20 * HOUR)      # loss 3 — throttled
        self.assertAlmostEqual(q1, 10.0)                    # 1000/100
        self.assertAlmostEqual(q2, bal1 / 100.0)            # still full
        self.assertAlmostEqual(q3, bal2 * 0.5 / 100.0)      # half size
        _round_trip(sim, 100.0, 110.0, 30 * HOUR)           # WIN (half size) resets
        self.assertEqual(sim._loss_streak, 0)
        q5 = _round_trip(sim, 100.0, 99.0, 40 * HOUR)
        self.assertAlmostEqual(q5, sim.trades[-2].pnl and (q5), q5)  # full again
        self.assertAlmostEqual(q5 * 100.0, (sim.balance + -sim.trades[-1].pnl) * 1.0, delta=1e-6)

    def test_win_rate_and_trade_pcts_identical_to_unthrottled(self):
        seq = [(100.0, 99.0), (100.0, 98.5), (100.0, 99.2), (100.0, 112.0), (100.0, 99.0)]
        plain, throttled = _sim(), _sim(throttle_after_losses=2, throttle_scale=0.5)
        for sim in (plain, throttled):
            for i, (e, x) in enumerate(seq):
                _round_trip(sim, e, x, i * 10 * HOUR)
        self.assertEqual(len(plain.trades), len(throttled.trades))
        for a, b in zip(plain.trades, throttled.trades):
            self.assertAlmostEqual(a.pnl_pct, b.pnl_pct, places=9)
        # NOTE: in this sequence the big win follows the loss streak, so the
        # throttle halves the WINNER too and ends up BEHIND — the exact
        # trade-off the backtest must adjudicate on real data.
        self.assertLess(throttled.balance, plain.balance)

    def test_pure_loss_streak_loses_less_money(self):
        plain, throttled = _sim(), _sim(throttle_after_losses=2, throttle_scale=0.5)
        for sim in (plain, throttled):
            for i in range(5):
                _round_trip(sim, 100.0, 99.0, i * 10 * HOUR)
        self.assertGreater(throttled.balance, plain.balance)

    def test_disabled_by_default(self):
        sim = _sim()
        for i in range(4):
            _round_trip(sim, 100.0, 99.0, i * 10 * HOUR)
        q = _round_trip(sim, 100.0, 99.0, 50 * HOUR)
        self.assertAlmostEqual(q * 100.0, sim.balance + -sim.trades[-1].pnl, delta=1e-6)


class TestEntrySizeScale(unittest.TestCase):
    def test_per_bar_scale_applies_and_clamps(self):
        sim = _sim(entry_size_scale=lambda t: 0.5 if t < 100 * HOUR else 2.0)
        q1 = _round_trip(sim, 100.0, 101.0, 0)
        self.assertAlmostEqual(q1, 5.0)  # 1000 * 0.5 / 100
        q2 = _round_trip(sim, 100.0, 101.0, 200 * HOUR)
        self.assertAlmostEqual(q2 * 100.0, sim.balance - sim.trades[-1].pnl, delta=1e-6)  # clamped to 1.0

    def test_combines_with_streak_throttle(self):
        sim = _sim(throttle_after_losses=1, throttle_scale=0.5,
                   entry_size_scale=lambda t: 0.5)
        _round_trip(sim, 100.0, 99.0, 0)              # loss (scale 0.5)
        bal = sim.balance
        q = _round_trip(sim, 100.0, 99.0, 10 * HOUR)  # 0.5 * 0.5 = 0.25
        self.assertAlmostEqual(q, bal * 0.25 / 100.0)


if __name__ == "__main__":
    unittest.main()
