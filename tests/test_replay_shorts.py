"""Short-side support in the backtest PositionSimulator / ReplayEngine."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import pandas as pd

from xauby.observability.replay import PositionSimulator, ReplayEngine
from xauby.strategies.signal import buy, close_short, hold, open_short, sell


def _sim(**kw):
    return PositionSimulator(initial_balance=1000.0, fee_pct=0.0, slippage_bps=0.0,
                             risk_pct=0.1, sl_atr_mult=2.0, **kw)


class TestShortOpen(unittest.TestCase):
    def test_open_short_sizes_and_places_stop_above(self):
        # try_open_short directly: assert the raw entry stop before any same-bar
        # trailing (on_bar manages the position on the opening bar too).
        sim = _sim()
        ok = sim.try_open_short(entry_price=100.0, atr=5.0, bar_time=1,
                                signal=open_short("fresh red", volatility=5.0))
        self.assertTrue(ok)
        self.assertEqual(sim.position.side, -1)
        # sl_distance = 5 * 2 = 10 -> qty = 1000*0.1/10 = 10; stop ABOVE entry.
        self.assertAlmostEqual(sim.position.qty, 10.0)
        self.assertAlmostEqual(sim.position.stop_loss, 110.0)

    def test_open_short_emits_event_via_on_bar(self):
        sim = _sim()
        ev = sim.on_bar(open_p=100.0, high=100.0, low=100.0, close=100.0, atr=5.0,
                        zone="", bar_time=1, signal=open_short("fresh red", volatility=5.0))
        opened = [e for e in ev if e.get("event_type") == "position_opened"]
        self.assertEqual(opened[0]["side"], "SHORT")


class TestShortExits(unittest.TestCase):
    def _open(self, sim):
        sim.on_bar(open_p=100.0, high=100.0, low=100.0, close=100.0, atr=5.0,
                   zone="", bar_time=1, signal=open_short("e", volatility=5.0))

    def test_profitable_cover_signal(self):
        sim = _sim()
        self._open(sim)
        # Price falls; cover via close_short at the next open (90).
        sim.on_bar(open_p=90.0, high=92.0, low=89.0, close=91.0, atr=5.0,
                   zone="", bar_time=2, signal=close_short("4H GREEN"))
        self.assertFalse(sim.has_position)
        t = sim.trades[0]
        # PnL = (entry - exit) * qty = (100 - 90) * 10 = +100.
        self.assertAlmostEqual(t.pnl, 100.0)
        self.assertEqual(t.trigger, "SIGNAL")

    def test_gap_stop_above_is_a_loss(self):
        sim = _sim()
        self._open(sim)
        # Bar gaps up through the 110 stop.
        sim.on_bar(open_p=112.0, high=113.0, low=111.0, close=112.0, atr=5.0,
                   zone="", bar_time=2, signal=hold("hold"))
        self.assertFalse(sim.has_position)
        t = sim.trades[0]
        self.assertAlmostEqual(t.pnl, -120.0)  # (100-112)*10
        self.assertEqual(t.trigger, "STOP_LOSS")

    def test_fixed_tp_below_entry(self):
        sim = _sim(fixed_tp_pct=2.0)
        self._open(sim)
        # tp = 100 * (1 - 0.02) = 98; bar low pierces it.
        sim.on_bar(open_p=99.0, high=99.5, low=97.0, close=98.5, atr=5.0,
                   zone="", bar_time=2, signal=hold("hold"))
        self.assertFalse(sim.has_position)
        t = sim.trades[0]
        self.assertEqual(t.trigger, "FIXED_TP")
        self.assertAlmostEqual(t.exit_price, 98.0)
        self.assertAlmostEqual(t.pnl, 20.0)  # (100-98)*10

    def test_trailing_stop_ratchets_down(self):
        sim = _sim(trailing_atr_mult=1.0)
        self._open(sim)
        start_sl = sim.position.stop_loss  # 110
        # Price falls to a new low; trailing stop should move DOWN toward price.
        sim.on_bar(open_p=95.0, high=96.0, low=90.0, close=92.0, atr=2.0,
                   zone="", bar_time=2, signal=hold("hold"))
        self.assertTrue(sim.has_position)
        self.assertLess(sim.position.stop_loss, start_sl)
        # candidate = lowest(90) + atr(2)*1.0 = 92.
        self.assertAlmostEqual(sim.position.stop_loss, 92.0)


class TestStopAndReverseReplay(unittest.TestCase):
    def test_long_signal_close_reverses_to_short_same_bar(self):
        sim = _sim()
        sim.on_bar(open_p=100.0, high=100.0, low=100.0, close=100.0, atr=5.0,
                   zone="", bar_time=1, signal=buy("fresh green", volatility=5.0))

        ev = sim.on_bar(
            open_p=98.0, high=99.0, low=96.0, close=97.0, atr=5.0,
            zone="RED", bar_time=2,
            signal=sell(
                "4H zone turned RED",
                volatility=5.0,
                metadata={"reverse_to_position_side": "SHORT"},
            ),
        )

        event_types = [e["event_type"] for e in ev]
        self.assertEqual(event_types[-2:], ["position_closed", "position_opened"])
        self.assertTrue(sim.has_position)
        self.assertEqual(sim.position.side, -1)
        self.assertEqual(ev[-1]["side"], "SHORT")
        self.assertEqual(ev[-1]["trigger"], "REVERSE")

    def test_short_signal_close_reverses_to_long_same_bar(self):
        sim = _sim()
        sim.on_bar(open_p=100.0, high=100.0, low=100.0, close=100.0, atr=5.0,
                   zone="", bar_time=1, signal=open_short("fresh red", volatility=5.0))

        ev = sim.on_bar(
            open_p=102.0, high=103.0, low=101.0, close=102.5, atr=5.0,
            zone="GREEN", bar_time=2,
            signal=close_short(
                "4H zone turned GREEN",
                volatility=5.0,
                metadata={"reverse_to_position_side": "LONG"},
            ),
        )

        event_types = [e["event_type"] for e in ev]
        self.assertEqual(event_types[-2:], ["position_closed", "position_opened"])
        self.assertTrue(sim.has_position)
        self.assertEqual(sim.position.side, 1)
        self.assertEqual(ev[-1]["side"], "LONG")
        self.assertEqual(ev[-1]["trigger"], "REVERSE")


class TestShortReplayEngine(unittest.TestCase):
    def test_full_short_trade_via_engine(self):
        rows = []
        # Falling market: open short early, cover near the bottom.
        prices = [100, 99, 97, 95, 93, 92]
        for i, p in enumerate(prices):
            rows.append({"timestamp": 1_700_000_000 + i * 14_400, "open": float(p),
                         "high": float(p) + 0.5, "low": float(p) - 0.5,
                         "close": float(p), "volume": 1000.0})
        df = pd.DataFrame(rows)

        sim = PositionSimulator(initial_balance=1000.0, fee_pct=0.0, slippage_bps=0.0,
                                risk_pct=0.1, sl_atr_mult=5.0, disable_stop_loss=True,
                                position_pct=1.0)
        runner = MagicMock()
        state = {"covered": False}

        def _run(ctx):
            if not ctx.has_position and not state["covered"]:
                return open_short("fresh red", volatility=2.0)
            if ctx.has_position and ctx.current_price <= 93.0:
                state["covered"] = True
                return close_short("cover", volatility=2.0)
            return hold("wait", volatility=2.0)

        runner.run.side_effect = _run
        engine = ReplayEngine(runner=runner, simulator=sim, strategy_config={}, engine_config={})
        engine.replay_bars(df, min_bars=0)

        self.assertEqual(len(sim.trades), 1)
        self.assertEqual(sim.trades[0].trigger, "SIGNAL")
        self.assertGreater(sim.trades[0].pnl, 0.0)  # short made money as price fell
        self.assertGreater(sim.balance, 1000.0)


if __name__ == "__main__":
    unittest.main()
