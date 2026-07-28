"""Live/replay parity for every engine-managed exit. Roadmap P1.7.

`tests/test_fixed_tp_backtest_parity.py` established the pattern for one
feature: prove both sides resolve through the same code, then prove the
simulator exits where the resolver says. This extends it to trailing,
breakeven, minimal-ROI, partial TP, funding and the short side.

Two of these encode divergences that existed until the shared
`next_trailing_stop` landed, and both changed outcomes rather than rounding:

* a strategy-supplied `Signal.trail_distance` was honoured live and ignored in
  replay, so any strategy using it backtested as something it does not trade;
* with no ATR available, live held the stop where it was while replay computed
  `peak - 0 * mult` and parked the stop exactly on the running peak, which
  stops out on the next adverse tick.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xauby.observability.replay import PositionSimulator  # noqa: E402
from xauby.runtime.exits import (  # noqa: E402
    minimal_roi_pct,
    next_trailing_stop,
    resolve_minimal_roi,
    resolve_partial_tp,
)
from xauby.strategies.signal import buy, hold, open_short, sell  # noqa: E402


def _sim(**kwargs):
    params = dict(initial_balance=1000.0, fee_pct=0.0, slippage_bps=0.0,
                  risk_pct=0.01)
    params.update(kwargs)
    return PositionSimulator(**params)


def _open_long(sim, entry, sl, atr=1.0):
    sim.on_bar(open_p=entry, high=entry, low=entry, close=entry, atr=atr,
               zone="", bar_time=0, signal=buy("entry", stop_loss_price=sl))
    assert sim.has_position


def _open_short(sim, entry, sl, atr=1.0):
    sim.on_bar(open_p=entry, high=entry, low=entry, close=entry, atr=atr,
               zone="", bar_time=0, signal=open_short("entry", stop_loss_price=sl))
    assert sim.has_position


class TestSharedTrailingResolver(unittest.TestCase):
    """Both sides must go through one function, and it must be this one."""

    def test_engine_and_simulator_import_the_same_resolver(self):
        import xauby.engine.loop as loop
        import xauby.observability.replay as replay

        self.assertIs(loop.next_trailing_stop, next_trailing_stop)
        self.assertIs(replay.next_trailing_stop, next_trailing_stop)

    def test_a_strategy_trail_distance_beats_the_atr_multiple(self):
        # Ignored by replay before P1.7.
        candidate = next_trailing_stop(
            side="long", entry_price=100.0, extreme_price=110.0, current_sl=95.0,
            atr=2.0, trailing_atr_mult=3.0, trail_distance=1.0)
        self.assertAlmostEqual(candidate, 109.0)   # 110 - 1, not 110 - 6

    def test_no_atr_holds_the_stop_instead_of_parking_it_on_the_peak(self):
        candidate = next_trailing_stop(
            side="long", entry_price=100.0, extreme_price=110.0, current_sl=95.0,
            atr=0.0, trailing_atr_mult=3.0)
        self.assertAlmostEqual(candidate, 95.0)
        self.assertNotAlmostEqual(candidate, 110.0,
                                  msg="a stop on the peak is an instant stop-out")

    def test_disable_stop_loss_pins_the_stop(self):
        # XAU runs this way: the strategy is the stop.
        candidate = next_trailing_stop(
            side="long", entry_price=100.0, extreme_price=140.0, current_sl=0.0,
            atr=2.0, trailing_atr_mult=3.0, disable_stop_loss=True)
        self.assertEqual(candidate, 0.0)

    def test_breakeven_lifts_the_stop_once_activated(self):
        below = next_trailing_stop(
            side="long", entry_price=100.0, extreme_price=101.0, current_sl=90.0,
            atr=2.0, trailing_atr_mult=5.0, breakeven_enabled=True,
            breakeven_activation_atr_mult=1.5, breakeven_buffer_atr_mult=0.1)
        self.assertAlmostEqual(below, 91.0)        # 101 - 10, not yet activated
        above = next_trailing_stop(
            side="long", entry_price=100.0, extreme_price=104.0, current_sl=90.0,
            atr=2.0, trailing_atr_mult=5.0, breakeven_enabled=True,
            breakeven_activation_atr_mult=1.5, breakeven_buffer_atr_mult=0.1)
        self.assertAlmostEqual(above, 100.2)       # entry + 0.1 * atr

    def test_breakeven_needs_an_atr(self):
        candidate = next_trailing_stop(
            side="long", entry_price=100.0, extreme_price=140.0, current_sl=90.0,
            atr=0.0, trailing_atr_mult=3.0, breakeven_enabled=True)
        self.assertAlmostEqual(candidate, 90.0)

    def test_the_short_side_mirrors_the_long_side(self):
        long_stop = next_trailing_stop(
            side="long", entry_price=100.0, extreme_price=110.0, current_sl=95.0,
            atr=2.0, trailing_atr_mult=3.0)
        short_stop = next_trailing_stop(
            side="short", entry_price=100.0, extreme_price=90.0, current_sl=105.0,
            atr=2.0, trailing_atr_mult=3.0)
        self.assertAlmostEqual(long_stop, 104.0)
        self.assertAlmostEqual(short_stop, 96.0)

    def test_short_breakeven_pulls_the_stop_down(self):
        candidate = next_trailing_stop(
            side="short", entry_price=100.0, extreme_price=96.0, current_sl=110.0,
            atr=2.0, trailing_atr_mult=5.0, breakeven_enabled=True,
            breakeven_activation_atr_mult=1.5, breakeven_buffer_atr_mult=0.1)
        self.assertAlmostEqual(candidate, 99.8)    # entry - 0.1 * atr


class TestSimulatorUsesIt(unittest.TestCase):
    def test_long_trailing_matches_the_resolver(self):
        sim = _sim(trailing_atr_mult=3.0)
        _open_long(sim, entry=100.0, sl=94.0, atr=2.0)
        sim.on_bar(open_p=100.0, high=110.0, low=99.0, close=109.0, atr=2.0,
                   zone="", bar_time=60, signal=hold("hold"))
        expected = next_trailing_stop(
            side="long", entry_price=100.0, extreme_price=110.0, current_sl=94.0,
            atr=2.0, trailing_atr_mult=3.0)
        self.assertAlmostEqual(sim.position.stop_loss, expected, places=9)

    def test_short_trailing_matches_the_resolver(self):
        sim = _sim(trailing_atr_mult=3.0)
        _open_short(sim, entry=100.0, sl=106.0, atr=2.0)
        sim.on_bar(open_p=100.0, high=101.0, low=90.0, close=91.0, atr=2.0,
                   zone="", bar_time=60, signal=hold("hold"))
        expected = next_trailing_stop(
            side="short", entry_price=100.0, extreme_price=90.0, current_sl=106.0,
            atr=2.0, trailing_atr_mult=3.0)
        self.assertAlmostEqual(sim.position.stop_loss, expected, places=9)

    def test_a_missing_atr_no_longer_moves_the_stop_to_the_peak(self):
        sim = _sim(trailing_atr_mult=3.0)
        _open_long(sim, entry=100.0, sl=94.0, atr=2.0)
        sim.on_bar(open_p=100.0, high=120.0, low=99.0, close=119.0, atr=0.0,
                   zone="", bar_time=60, signal=hold("hold"))
        self.assertAlmostEqual(sim.position.stop_loss, 94.0, places=9)


class TestMinimalRoiParity(unittest.TestCase):
    def test_both_sides_read_the_ladder_through_one_resolver(self):
        import xauby.engine.loop as loop
        import xauby.observability.replay as replay

        self.assertIs(loop.minimal_roi_pct, minimal_roi_pct)
        self.assertIs(replay.minimal_roi_pct, minimal_roi_pct)

    def test_the_xau_ladder_steps_down_with_age(self):
        steps = resolve_minimal_roi({"minimal_roi": {"0": 8.0, "1440": 5.0,
                                                     "4320": 3.0}})
        self.assertAlmostEqual(minimal_roi_pct(steps, 0.0), 8.0)
        self.assertAlmostEqual(minimal_roi_pct(steps, 1439.0), 8.0)
        self.assertAlmostEqual(minimal_roi_pct(steps, 1440.0), 5.0)
        self.assertAlmostEqual(minimal_roi_pct(steps, 5000.0), 3.0)

    def test_the_simulator_exits_at_the_ladder_price(self):
        steps = resolve_minimal_roi({"minimal_roi": {"0": 8.0}})
        sim = _sim(minimal_roi=steps, disable_stop_loss=True)
        _open_long(sim, entry=100.0, sl=0.0, atr=0.0)
        sim.on_bar(open_p=101.0, high=112.0, low=100.0, close=110.0, atr=0.0,
                   zone="", bar_time=60, signal=hold("hold"))
        self.assertFalse(sim.has_position)
        self.assertAlmostEqual(sim.trades[-1].exit_price, 108.0, places=9)
        self.assertEqual(sim.trades[-1].trigger, "MINIMAL_ROI")

    def test_the_short_ladder_targets_below_entry(self):
        steps = resolve_minimal_roi({"minimal_roi": {"0": 8.0}})
        sim = _sim(minimal_roi=steps, disable_stop_loss=True)
        _open_short(sim, entry=100.0, sl=0.0, atr=0.0)
        sim.on_bar(open_p=99.0, high=100.0, low=88.0, close=90.0, atr=0.0,
                   zone="", bar_time=60, signal=hold("hold"))
        self.assertFalse(sim.has_position)
        self.assertAlmostEqual(sim.trades[-1].exit_price, 92.0, places=9)


class TestPartialTpParity(unittest.TestCase):
    def test_both_sides_resolve_partial_tp_the_same_way(self):
        cfg = {"partial_tp_pct": 12.0, "partial_tp_fraction": 0.5}
        self.assertEqual(resolve_partial_tp(cfg), (12.0, 0.5))
        self.assertEqual(resolve_partial_tp({})[0], 0.0)

    def test_a_long_banks_half_at_the_target(self):
        sim = _sim(partial_tp_pct=10.0, partial_tp_fraction=0.5,
                   disable_stop_loss=True)
        _open_long(sim, entry=100.0, sl=0.0, atr=0.0)
        qty_before = sim.position.qty
        sim.on_bar(open_p=100.0, high=111.0, low=100.0, close=110.0, atr=0.0,
                   zone="", bar_time=60, signal=hold("hold"))
        self.assertTrue(sim.has_position, "the runner must keep riding")
        self.assertTrue(sim.position.partial_taken)
        self.assertAlmostEqual(sim.position.qty, qty_before * 0.5, places=9)

    def test_it_fires_once(self):
        sim = _sim(partial_tp_pct=10.0, partial_tp_fraction=0.5,
                   disable_stop_loss=True)
        _open_long(sim, entry=100.0, sl=0.0, atr=0.0)
        sim.on_bar(open_p=100.0, high=111.0, low=100.0, close=110.0, atr=0.0,
                   zone="", bar_time=60, signal=hold("hold"))
        qty_after_first = sim.position.qty
        sim.on_bar(open_p=110.0, high=120.0, low=109.0, close=119.0, atr=0.0,
                   zone="", bar_time=120, signal=hold("hold"))
        self.assertAlmostEqual(sim.position.qty, qty_after_first, places=9)

    def test_a_roi_rung_below_the_partial_target_pre_empts_it(self):
        # The invariant validate_exit_config enforces at startup, checked here
        # against the simulator that has to honour it: the full exit wins the
        # tick, so the partial can never fire.
        steps = resolve_minimal_roi({"minimal_roi": {"0": 8.0}})
        sim = _sim(minimal_roi=steps, partial_tp_pct=12.0,
                   partial_tp_fraction=0.5, disable_stop_loss=True)
        _open_long(sim, entry=100.0, sl=0.0, atr=0.0)
        sim.on_bar(open_p=100.0, high=115.0, low=100.0, close=114.0, atr=0.0,
                   zone="", bar_time=60, signal=hold("hold"))
        self.assertFalse(sim.has_position)
        self.assertEqual(sim.trades[-1].trigger, "MINIMAL_ROI")


class TestFundingParity(unittest.TestCase):
    def test_a_long_pays_funding_and_a_short_receives_it(self):
        long_sim = _sim(funding_rate_8h=0.0001, bar_hours=4.0,
                        disable_stop_loss=True)
        _open_long(long_sim, entry=100.0, sl=0.0, atr=0.0)
        paid = long_sim.accrue_funding()
        self.assertGreater(paid, 0.0)

        short_sim = _sim(funding_rate_8h=0.0001, bar_hours=4.0,
                         disable_stop_loss=True)
        _open_short(short_sim, entry=100.0, sl=0.0, atr=0.0)
        received = short_sim.accrue_funding()
        self.assertLess(received, 0.0)

    def test_funding_scales_with_the_bar_length(self):
        four_hour = _sim(funding_rate_8h=0.0001, bar_hours=4.0,
                         disable_stop_loss=True)
        _open_long(four_hour, entry=100.0, sl=0.0, atr=0.0)
        eight_hour = _sim(funding_rate_8h=0.0001, bar_hours=8.0,
                          disable_stop_loss=True)
        _open_long(eight_hour, entry=100.0, sl=0.0, atr=0.0)
        self.assertAlmostEqual(eight_hour.accrue_funding(),
                               four_hour.accrue_funding() * 2.0, places=9)

    def test_no_funding_configured_costs_nothing(self):
        sim = _sim(funding_rate_8h=0.0, bar_hours=4.0, disable_stop_loss=True)
        _open_long(sim, entry=100.0, sl=0.0, atr=0.0)
        self.assertEqual(sim.accrue_funding(), 0.0)

    def test_funding_is_deducted_from_the_closed_trade(self):
        sim = _sim(funding_rate_8h=0.001, bar_hours=8.0, disable_stop_loss=True)
        _open_long(sim, entry=100.0, sl=0.0, atr=0.0)
        sim.accrue_funding()
        paid = sim.position.funding_paid
        self.assertGreater(paid, 0.0)
        sim.on_bar(open_p=100.0, high=100.0, low=100.0, close=100.0, atr=0.0,
                   zone="", bar_time=60,
                   signal=sell("exit"))
        self.assertFalse(sim.has_position)
        # A flat round trip with funding accrued must close at a loss.
        self.assertLess(sim.trades[-1].pnl, 0.0)


if __name__ == "__main__":
    unittest.main()
