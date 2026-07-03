"""Partial take-profit in the PositionSimulator (backtest side).

The partial leg banks realized profit mid-trade; the final SimTrade carries
the COMBINED net PnL so trade counts and win rate stay comparable with
non-partial runs. The core claim under test: a trade that reaches the target
and then round-trips back below entry becomes a NET WINNER.
"""
from __future__ import annotations

import unittest

from xauby.observability.replay import PositionSimulator
from xauby.runtime.exits import resolve_partial_tp
from xauby.strategies.signal import hold, sell, buy


HOUR = 3600


def _sim(**kwargs) -> PositionSimulator:
    defaults = dict(
        initial_balance=1000.0,
        fee_pct=0.0,
        slippage_bps=0.0,
        disable_stop_loss=True,
        position_pct=1.0,
    )
    defaults.update(kwargs)
    return PositionSimulator(**defaults)


def _bar(sim, *, o, h, l, c, t, signal=None):
    return sim.on_bar(
        open_p=o, high=h, low=l, close=c, atr=1.0, zone="GREEN",
        bar_time=t, signal=signal or hold("test"),
    )


class TestResolver(unittest.TestCase):
    def test_enabled(self):
        self.assertEqual(
            resolve_partial_tp({"partial_tp_pct": 8.0, "partial_tp_fraction": 0.5}),
            (8.0, 0.5),
        )

    def test_default_fraction(self):
        self.assertEqual(resolve_partial_tp({"partial_tp_pct": 5.0}), (5.0, 0.5))

    def test_disabled_cases(self):
        self.assertEqual(resolve_partial_tp({}), (0.0, 0.0))
        self.assertEqual(resolve_partial_tp({"partial_tp_pct": 0.0}), (0.0, 0.0))
        self.assertEqual(
            resolve_partial_tp({"partial_tp_pct": 5.0, "partial_tp_fraction": 1.0}),
            (0.0, 0.0),
        )
        self.assertEqual(resolve_partial_tp({"partial_tp_pct": "x"}), (0.0, 0.0))


class TestLongPartial(unittest.TestCase):
    def test_round_trip_becomes_net_winner(self):
        # Entry 100, partial 50% at +8% (108), remainder exits at 97 (-3%).
        # Banked +4.0 per unit-notional beats the -1.5 remainder loss.
        sim = _sim(partial_tp_pct=8.0, partial_tp_fraction=0.5)
        self.assertTrue(sim.try_open(100.0, 1.0, 0))
        qty0 = sim.position.qty
        _bar(sim, o=103.0, h=109.0, l=102.0, c=105.0, t=4 * HOUR)
        self.assertTrue(sim.position.partial_taken)
        self.assertAlmostEqual(sim.position.qty, qty0 * 0.5)
        banked = sim.position.banked_pnl
        self.assertAlmostEqual(banked, qty0 * 0.5 * 8.0)  # (108-100) * half qty
        _bar(sim, o=97.0, h=98.0, l=96.0, c=97.0, t=8 * HOUR,
             signal=sell("4H zone turned RED"))
        self.assertEqual(len(sim.trades), 1)  # ONE combined trade, not two
        trade = sim.trades[0]
        expected = banked + (97.0 - 100.0) * qty0 * 0.5
        self.assertAlmostEqual(trade.pnl, expected)
        self.assertGreater(trade.pnl, 0)  # the round-trip is now a winner
        self.assertAlmostEqual(trade.pnl_pct, expected / (100.0 * qty0) * 100.0)

    def test_partial_fires_once(self):
        sim = _sim(partial_tp_pct=8.0, partial_tp_fraction=0.5)
        self.assertTrue(sim.try_open(100.0, 1.0, 0))
        _bar(sim, o=103.0, h=109.0, l=102.0, c=105.0, t=4 * HOUR)
        qty_after = sim.position.qty
        banked_after = sim.position.banked_pnl
        _bar(sim, o=110.0, h=115.0, l=109.0, c=114.0, t=8 * HOUR)
        self.assertAlmostEqual(sim.position.qty, qty_after)  # no second partial
        self.assertAlmostEqual(sim.position.banked_pnl, banked_after)

    def test_gap_open_fills_at_better_open(self):
        sim = _sim(partial_tp_pct=8.0, partial_tp_fraction=0.5)
        self.assertTrue(sim.try_open(100.0, 1.0, 0))
        qty0 = sim.position.qty
        _bar(sim, o=112.0, h=113.0, l=111.0, c=112.5, t=4 * HOUR)
        self.assertAlmostEqual(sim.position.banked_pnl, qty0 * 0.5 * 12.0)

    def test_trend_continuation_keeps_tail(self):
        sim = _sim(partial_tp_pct=8.0, partial_tp_fraction=0.5)
        self.assertTrue(sim.try_open(100.0, 1.0, 0))
        qty0 = sim.position.qty
        _bar(sim, o=103.0, h=109.0, l=102.0, c=105.0, t=4 * HOUR)
        _bar(sim, o=120.0, h=121.0, l=118.0, c=120.0, t=8 * HOUR,
             signal=sell("4H zone turned RED"))
        trade = sim.trades[0]
        expected = qty0 * 0.5 * 8.0 + qty0 * 0.5 * 20.0
        self.assertAlmostEqual(trade.pnl, expected)

    def test_fees_charged_per_leg(self):
        sim = _sim(fee_pct=0.001, partial_tp_pct=8.0, partial_tp_fraction=0.5)
        self.assertTrue(sim.try_open(100.0, 1.0, 0))
        qty0 = sim.position.qty
        _bar(sim, o=103.0, h=109.0, l=102.0, c=105.0, t=4 * HOUR)
        banked = sim.position.banked_pnl
        expected_banked = qty0 * 0.5 * 8.0 - (100.0 + 108.0) * qty0 * 0.5 * 0.001
        self.assertAlmostEqual(banked, expected_banked)
        _bar(sim, o=97.0, h=98.0, l=96.0, c=97.0, t=8 * HOUR,
             signal=sell("4H zone turned RED"))
        trade = sim.trades[0]
        expected_final = (97.0 - 100.0) * qty0 * 0.5 - (100.0 + 97.0) * qty0 * 0.5 * 0.001
        self.assertAlmostEqual(trade.pnl, banked + expected_final)

    def test_disabled_matches_legacy_behaviour(self):
        legacy, with_off = _sim(), _sim(partial_tp_pct=0.0)
        for sim in (legacy, with_off):
            self.assertTrue(sim.try_open(100.0, 1.0, 0))
            _bar(sim, o=103.0, h=109.0, l=102.0, c=105.0, t=4 * HOUR)
            _bar(sim, o=97.0, h=98.0, l=96.0, c=97.0, t=8 * HOUR,
                 signal=sell("4H zone turned RED"))
        self.assertAlmostEqual(legacy.trades[0].pnl, with_off.trades[0].pnl)
        self.assertAlmostEqual(legacy.balance, with_off.balance)


class TestShortPartial(unittest.TestCase):
    def test_short_round_trip_becomes_net_winner(self):
        sim = _sim(partial_tp_pct=8.0, partial_tp_fraction=0.5)
        self.assertTrue(sim.try_open_short(100.0, 1.0, 0))
        qty0 = sim.position.qty
        _bar(sim, o=97.0, h=98.0, l=91.0, c=94.0, t=4 * HOUR)
        self.assertTrue(sim.position.partial_taken)
        self.assertAlmostEqual(sim.position.banked_pnl, qty0 * 0.5 * 8.0)
        _bar(sim, o=103.0, h=104.0, l=102.0, c=103.0, t=8 * HOUR,
             signal=buy("4H zone turned GREEN"))
        self.assertEqual(len(sim.trades), 1)
        trade = sim.trades[0]
        expected = qty0 * 0.5 * 8.0 + (100.0 - 103.0) * qty0 * 0.5
        self.assertAlmostEqual(trade.pnl, expected)
        self.assertGreater(trade.pnl, 0)


if __name__ == "__main__":
    unittest.main()
