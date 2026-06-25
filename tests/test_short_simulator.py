"""Tests for the research-only ShortPositionSimulator in regime_strategy_eval."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.strategies.signal import buy, hold, sell

_spec = importlib.util.spec_from_file_location(
    "regime_strategy_eval", ROOT / "scripts" / "regime_strategy_eval.py"
)
_ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ev)
ShortPositionSimulator = _ev.ShortPositionSimulator

HOUR = 3600


def make_sim(**kw):
    defaults = dict(
        initial_balance=1000.0,
        fee_pct=0.0005,
        funding_8h=0.0001,
        sl_atr_mult=2.0,
        trailing_atr_mult=2.0,
        risk_pct=0.02,
        sl_confirm_ticks=3,
    )
    defaults.update(kw)
    return ShortPositionSimulator(**defaults)


class TestShortPositionSimulator(unittest.TestCase):
    def test_open_places_sl_above_entry(self):
        sim = make_sim()
        ok = sim.try_open(100.0, atr=2.0, bar_time=0, signal=buy("s"))
        self.assertTrue(ok)
        self.assertGreater(sim.position.stop_loss, 100.0)
        self.assertAlmostEqual(sim.position.stop_loss, 104.0)  # entry + 2*ATR

    def test_profitable_short_credits_balance(self):
        sim = make_sim(funding_8h=0.0)
        sim.try_open(100.0, atr=2.0, bar_time=0, signal=buy("s"))
        qty = sim.position.qty
        sim._close(90.0, bar_time=HOUR, trigger="SIGNAL")
        trade = sim.trades[-1]
        gross = (100.0 - 90.0) * qty
        fee = (100.0 + 90.0) * qty * 0.0005
        self.assertAlmostEqual(trade.pnl, gross - fee, places=6)
        self.assertGreater(trade.pnl, 0)
        self.assertAlmostEqual(sim.balance, 1000.0 + trade.pnl, places=6)

    def test_funding_deducted_by_hold_time(self):
        sim = make_sim()
        sim.try_open(100.0, atr=2.0, bar_time=0, signal=buy("s"))
        qty = sim.position.qty
        sim._close(100.0, bar_time=16 * HOUR, trigger="SIGNAL")  # 16h = 2 funding periods
        trade = sim.trades[-1]
        fee = (100.0 + 100.0) * qty * 0.0005
        funding = 100.0 * qty * 0.0001 * 2
        self.assertAlmostEqual(trade.pnl, -(fee + funding), places=6)

    def test_gap_through_stop_closes_at_open(self):
        sim = make_sim()
        sim.try_open(100.0, atr=2.0, bar_time=0, signal=buy("s"))
        sim.on_bar(
            open_p=105.0, high=106.0, low=104.0, close=105.5, atr=2.0,
            zone="X", bar_time=HOUR, signal=hold("h"),
        )
        self.assertFalse(sim.has_position)
        self.assertEqual(sim.trades[-1].trigger, "STOP_LOSS")
        self.assertLess(sim.trades[-1].pnl, 0)

    def test_trailing_ratchets_down_only(self):
        sim = make_sim()
        sim.try_open(100.0, atr=2.0, bar_time=0, signal=buy("s"))
        initial_sl = sim.position.stop_loss
        # Price falls: lowest 90 -> candidate SL 90 + 2*2 = 94 < 104
        sim.on_bar(open_p=95.0, high=96.0, low=90.0, close=91.0, atr=2.0,
                   zone="X", bar_time=HOUR, signal=hold("h"))
        self.assertLess(sim.position.stop_loss, initial_sl)
        sl_after_drop = sim.position.stop_loss
        # Price bounces: trailing must NOT move back up
        sim.on_bar(open_p=92.0, high=93.0, low=91.5, close=92.5, atr=2.0,
                   zone="X", bar_time=2 * HOUR, signal=hold("h"))
        self.assertEqual(sim.position.stop_loss, sl_after_drop)

    def test_sell_signal_covers(self):
        sim = make_sim()
        sim.try_open(100.0, atr=2.0, bar_time=0, signal=buy("s"))
        sim.on_bar(open_p=95.0, high=96.0, low=94.0, close=95.0, atr=2.0,
                   zone="X", bar_time=HOUR, signal=sell("cover"))
        self.assertFalse(sim.has_position)
        self.assertEqual(sim.trades[-1].trigger, "SIGNAL")
        self.assertGreater(sim.trades[-1].pnl, 0)

    def test_sl_breach_counts_above_price(self):
        sim = make_sim()
        sim.try_open(100.0, atr=2.0, bar_time=0, signal=buy("s"))
        for _ in range(3):
            sim.update_sl_breach(104.5)  # above SL 104
        self.assertTrue(sim.sl_confirmed)
        sim.update_sl_breach(99.0)
        self.assertFalse(sim.sl_confirmed)


if __name__ == "__main__":
    unittest.main()
