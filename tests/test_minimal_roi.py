"""Minimal-ROI ladder: resolver, backtest simulator, and live engine exit.

The ladder is a freqtrade-style time-decaying take-profit: ``minimal_roi``
maps position age (minutes) to the ROI percent that locks in profit. One
resolver (xauby.runtime.exits) feeds both the live tick check and the
backtest PositionSimulator so the two stay in parity.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from xauby.observability.replay import PositionSimulator
from xauby.runtime.exits import minimal_roi_pct, resolve_minimal_roi
from xauby.strategies.signal import hold


class TestResolver(unittest.TestCase):
    def test_parses_and_sorts_by_age(self):
        steps = resolve_minimal_roi({"minimal_roi": {"2880": 3.0, "0": 10.0, "1440": 5.0}})
        self.assertEqual(steps, [(0.0, 10.0), (1440.0, 5.0), (2880.0, 3.0)])

    def test_disabled_when_missing_or_empty(self):
        self.assertEqual(resolve_minimal_roi(None), [])
        self.assertEqual(resolve_minimal_roi({}), [])
        self.assertEqual(resolve_minimal_roi({"minimal_roi": {}}), [])
        self.assertEqual(resolve_minimal_roi({"minimal_roi": "0:10"}), [])

    def test_invalid_and_nonpositive_steps_dropped(self):
        steps = resolve_minimal_roi(
            {"minimal_roi": {"0": 10.0, "abc": 5.0, "1440": 0.0, "-60": 4.0, "2880": None}}
        )
        self.assertEqual(steps, [(0.0, 10.0)])

    def test_threshold_steps_down_with_age(self):
        steps = resolve_minimal_roi({"minimal_roi": {"0": 10.0, "1440": 5.0, "2880": 3.0}})
        self.assertEqual(minimal_roi_pct(steps, 0.0), 10.0)
        self.assertEqual(minimal_roi_pct(steps, 1439.9), 10.0)
        self.assertEqual(minimal_roi_pct(steps, 1440.0), 5.0)
        self.assertEqual(minimal_roi_pct(steps, 99999.0), 3.0)

    def test_no_step_active_before_first_age(self):
        steps = resolve_minimal_roi({"minimal_roi": {"1440": 5.0}})
        self.assertEqual(minimal_roi_pct(steps, 60.0), 0.0)
        self.assertEqual(minimal_roi_pct(steps, 1440.0), 5.0)

    def test_empty_ladder_never_triggers(self):
        self.assertEqual(minimal_roi_pct([], 99999.0), 0.0)


def _sim(**kwargs) -> PositionSimulator:
    defaults = dict(
        initial_balance=1000.0,
        fee_pct=0.0,
        slippage_bps=0.0,
        disable_stop_loss=True,  # CDC-pure baseline: isolate the ROI exit
        position_pct=1.0,
    )
    defaults.update(kwargs)
    return PositionSimulator(**defaults)


def _bar(sim, *, o, h, l, c, t, action="HOLD"):
    return sim.on_bar(
        open_p=o, high=h, low=l, close=c, atr=1.0, zone="GREEN",
        bar_time=t, signal=hold("test"),
    ) if action == "HOLD" else None


HOUR = 3600
DAY = 24 * HOUR


class TestSimulatorLong(unittest.TestCase):
    def test_intrabar_hit_exits_at_threshold_price(self):
        steps = resolve_minimal_roi({"minimal_roi": {"0": 10.0}})
        sim = _sim(minimal_roi=steps)
        self.assertTrue(sim.try_open(100.0, 1.0, 0))
        _bar(sim, o=104.0, h=111.0, l=103.0, c=105.0, t=4 * HOUR)
        self.assertEqual(len(sim.trades), 1)
        trade = sim.trades[0]
        self.assertEqual(trade.trigger, "MINIMAL_ROI")
        self.assertAlmostEqual(trade.exit_price, 110.0)
        self.assertGreater(trade.pnl, 0)

    def test_ladder_step_down_exits_at_open(self):
        # After 1 day the threshold drops to 3%; price already sits at +5%
        # when the step activates, so the exit is a market fill at the open.
        steps = resolve_minimal_roi({"minimal_roi": {"0": 10.0, "1440": 3.0}})
        sim = _sim(minimal_roi=steps)
        self.assertTrue(sim.try_open(100.0, 1.0, 0))
        _bar(sim, o=104.0, h=105.0, l=103.0, c=105.0, t=4 * HOUR)  # +5% < 10%: holds
        self.assertEqual(len(sim.trades), 0)
        _bar(sim, o=105.0, h=105.5, l=104.0, c=104.5, t=DAY)  # threshold now 3%
        self.assertEqual(len(sim.trades), 1)
        self.assertAlmostEqual(sim.trades[0].exit_price, 105.0)

    def test_no_exit_below_threshold(self):
        steps = resolve_minimal_roi({"minimal_roi": {"0": 10.0}})
        sim = _sim(minimal_roi=steps)
        self.assertTrue(sim.try_open(100.0, 1.0, 0))
        _bar(sim, o=101.0, h=109.9, l=100.0, c=105.0, t=4 * HOUR)
        self.assertEqual(len(sim.trades), 0)
        self.assertTrue(sim.has_position)

    def test_disabled_ladder_keeps_position(self):
        sim = _sim(minimal_roi=[])
        self.assertTrue(sim.try_open(100.0, 1.0, 0))
        _bar(sim, o=150.0, h=200.0, l=140.0, c=180.0, t=10 * DAY)
        self.assertTrue(sim.has_position)


class TestSimulatorShort(unittest.TestCase):
    def test_short_roi_target_sits_below_entry(self):
        steps = resolve_minimal_roi({"minimal_roi": {"0": 10.0}})
        sim = _sim(minimal_roi=steps)
        self.assertTrue(sim.try_open_short(100.0, 1.0, 0))
        _bar(sim, o=96.0, h=97.0, l=89.0, c=91.0, t=4 * HOUR)
        self.assertEqual(len(sim.trades), 1)
        trade = sim.trades[0]
        self.assertEqual(trade.trigger, "MINIMAL_ROI")
        self.assertAlmostEqual(trade.exit_price, 90.0)
        self.assertGreater(trade.pnl, 0)

    def test_short_step_down_exits_at_open(self):
        steps = resolve_minimal_roi({"minimal_roi": {"0": 10.0, "1440": 3.0}})
        sim = _sim(minimal_roi=steps)
        self.assertTrue(sim.try_open_short(100.0, 1.0, 0))
        _bar(sim, o=96.0, h=97.0, l=95.0, c=96.0, t=4 * HOUR)
        self.assertEqual(len(sim.trades), 0)
        _bar(sim, o=96.0, h=97.0, l=95.5, c=96.5, t=DAY)
        self.assertEqual(len(sim.trades), 1)
        self.assertAlmostEqual(sim.trades[0].exit_price, 96.0)


class _EngineStub:
    """Just enough of LoopMixin for _apply_minimal_roi_exit."""

    from xauby.engine.loop import LoopMixin as _L

    _apply_minimal_roi_exit = _L._apply_minimal_roi_exit
    _position_age_minutes = _L._position_age_minutes


def _state(*, entry=100.0, side="LONG", opened_hours_ago=48.0):
    opened = datetime.now(timezone.utc) - timedelta(hours=opened_hours_ago)
    return {
        "state": "bought",
        "entry_price": entry,
        "position_side": side,
        "opened_at": opened.replace(tzinfo=None).isoformat(),
    }


class TestLiveExit(unittest.TestCase):
    CONF = {"minimal_roi": {"0": 10.0, "1440": 3.0}}

    def test_hold_becomes_sell_when_threshold_met(self):
        eng = _EngineStub()
        action, reason = eng._apply_minimal_roi_exit(
            _state(opened_hours_ago=48.0), "HOLD", "keep", 104.0,
            sl_confirmed=False, strat_conf=self.CONF,
        )
        self.assertEqual(action, "SELL")
        self.assertIn("Minimal ROI reached", reason)

    def test_young_position_keeps_high_threshold(self):
        eng = _EngineStub()
        action, _ = eng._apply_minimal_roi_exit(
            _state(opened_hours_ago=2.0), "HOLD", "keep", 104.0,
            sl_confirmed=False, strat_conf=self.CONF,
        )
        self.assertEqual(action, "HOLD")

    def test_short_position_measures_downside_roi(self):
        eng = _EngineStub()
        action, reason = eng._apply_minimal_roi_exit(
            _state(side="SHORT", opened_hours_ago=48.0), "HOLD", "keep", 96.0,
            sl_confirmed=False, strat_conf=self.CONF,
        )
        self.assertEqual(action, "SELL")
        self.assertIn("Minimal ROI reached", reason)

    def test_never_overrides_sl_confirmed_or_existing_sell(self):
        eng = _EngineStub()
        action, reason = eng._apply_minimal_roi_exit(
            _state(), "SELL", "zone RED", 200.0,
            sl_confirmed=False, strat_conf=self.CONF,
        )
        self.assertEqual((action, reason), ("SELL", "zone RED"))
        action, _ = eng._apply_minimal_roi_exit(
            _state(), "HOLD", "keep", 200.0,
            sl_confirmed=True, strat_conf=self.CONF,
        )
        self.assertEqual(action, "HOLD")

    def test_disabled_config_is_noop(self):
        eng = _EngineStub()
        action, reason = eng._apply_minimal_roi_exit(
            _state(), "HOLD", "keep", 200.0,
            sl_confirmed=False, strat_conf={},
        )
        self.assertEqual((action, reason), ("HOLD", "keep"))


if __name__ == "__main__":
    unittest.main()
