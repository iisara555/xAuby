"""Backtest/live parity for the engine-managed fixed take-profit.

The live engine exits on a fixed TP; these tests ensure the backtest
PositionSimulator exits at the *same* price via the shared resolver, so a
strategy's backtest reflects its real live behaviour.
"""

import unittest

from xauby.observability.replay import PositionSimulator
from xauby.runtime.exits import (
    DEFAULT_FIXED_TP_PCT_BY_STRATEGY,
    fixed_take_profit_price,
    resolve_fixed_tp_pct,
)
from xauby.strategies.signal import buy, hold


def _new_sim(fixed_tp_pct):
    # No fees / slippage so exit price math is exact.
    return PositionSimulator(
        initial_balance=1000.0,
        fee_pct=0.0,
        slippage_bps=0.0,
        risk_pct=0.01,
        fixed_tp_pct=fixed_tp_pct,
    )


def _open_at(sim, entry, sl):
    sig = buy("entry", stop_loss_price=sl)
    sim.on_bar(open_p=entry, high=entry, low=entry, close=entry,
               atr=0.0, zone="", bar_time=1, signal=sig)
    assert sim.has_position, "position should be open"


class TestSharedResolver(unittest.TestCase):
    def test_engine_and_backtest_share_one_default_table(self):
        # orders.py must delegate to the shared resolver (no duplicate table).
        import xauby.engine.orders as orders
        self.assertFalse(hasattr(orders, "DEFAULT_FIXED_TP_PCT_BY_STRATEGY"))
        self.assertEqual(DEFAULT_FIXED_TP_PCT_BY_STRATEGY["btc_ema_pullback"], 1.5)

    def test_config_overrides_default(self):
        self.assertEqual(resolve_fixed_tp_pct("btc_ema_pullback", {}), 1.5)
        self.assertEqual(resolve_fixed_tp_pct("btc_ema_pullback", {"fixed_tp_pct": 0.0}), 0.0)
        self.assertEqual(resolve_fixed_tp_pct("cdc_action_zone", {"fixed_tp_pct": 3.0}), 3.0)


class TestSimulatorFixedTP(unittest.TestCase):
    def test_exits_at_fixed_tp_price(self):
        sim = _new_sim(fixed_tp_pct=2.0)
        _open_at(sim, entry=100.0, sl=95.0)
        # Next bar trades up through TP (entry 100 -> tp 102).
        sim.on_bar(open_p=100.5, high=103.0, low=100.0, close=102.5,
                   atr=0.0, zone="", bar_time=2, signal=hold("hold"))
        self.assertFalse(sim.has_position)
        self.assertEqual(len(sim.trades), 1)
        self.assertEqual(sim.trades[0].trigger, "FIXED_TP")
        self.assertAlmostEqual(sim.trades[0].exit_price, 102.0)

    def test_exit_price_matches_shared_helper(self):
        name, cfg = "cdc_action_zone", {"fixed_tp_pct": 3.0}
        sim = _new_sim(fixed_tp_pct=resolve_fixed_tp_pct(name, cfg))
        _open_at(sim, entry=100.0, sl=95.0)
        sim.on_bar(open_p=101.0, high=104.0, low=101.0, close=103.5,
                   atr=0.0, zone="", bar_time=2, signal=hold("hold"))
        self.assertAlmostEqual(
            sim.trades[0].exit_price, fixed_take_profit_price(100.0, name, cfg)
        )

    def test_sl_gap_has_priority_over_tp(self):
        sim = _new_sim(fixed_tp_pct=2.0)
        _open_at(sim, entry=100.0, sl=95.0)
        # Bar gaps below SL at open even though high would reach TP.
        sim.on_bar(open_p=94.0, high=103.0, low=93.0, close=94.0,
                   atr=0.0, zone="", bar_time=2, signal=hold("hold"))
        self.assertEqual(len(sim.trades), 1)
        self.assertIn(sim.trades[0].trigger, ("STOP_LOSS", "TRAILING_STOP"))
        self.assertAlmostEqual(sim.trades[0].exit_price, 94.0)

    def test_disabled_tp_does_not_exit(self):
        sim = _new_sim(fixed_tp_pct=0.0)
        _open_at(sim, entry=100.0, sl=95.0)
        sim.on_bar(open_p=101.0, high=200.0, low=100.0, close=150.0,
                   atr=0.0, zone="", bar_time=2, signal=hold("hold"))
        self.assertTrue(sim.has_position)
        self.assertEqual(len(sim.trades), 0)


if __name__ == "__main__":
    unittest.main()
