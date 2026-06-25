"""Tests for live/backtest replay parity fixes."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from xauby.observability.replay import (
    ContextBuilder,
    PositionSimulator,
    ReplayEngine,
)
from xauby.strategies.signal import buy, hold, sell


def _make_primary(n: int = 120, base_ts: int = 1_700_000_000) -> pd.DataFrame:
    rows = []
    for i in range(n):
        ts = base_ts + i * 14_400
        price = 100.0 + i * 0.1
        rows.append(
            {
                "timestamp": ts,
                "open": price,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price + 0.5,
                "volume": 1000.0 + i,
            }
        )
    return pd.DataFrame(rows)


def _make_regime(primary: pd.DataFrame) -> pd.DataFrame:
    """One daily bar per four 4H bars (coarser regime series)."""
    rows = []
    for i in range(0, len(primary), 4):
        row = primary.iloc[i]
        rows.append(
            {
                "timestamp": int(row["timestamp"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
        )
    return pd.DataFrame(rows)


class TestContextRegimeSlice(unittest.TestCase):
    def test_context_slices_regime_by_timestamp(self):
        primary = _make_primary(10)
        regime = _make_regime(primary)
        # Slice primary to bar index 5 (not equal to regime row count).
        ctx = ContextBuilder.build(
            symbol="XAUTUSDT",
            timeframe_primary="4h",
            df_primary=primary,
            df_regime=regime,
            current_price=float(primary.iloc[6]["open"]),
            end_index=5,
        )
        cutoff = int(primary.iloc[5]["timestamp"])
        expected = regime[regime["timestamp"] <= cutoff]
        self.assertEqual(len(ctx.df_primary), 6)
        self.assertEqual(len(ctx.df_regime), len(expected))
        self.assertLessEqual(int(ctx.df_regime.iloc[-1]["timestamp"]), cutoff)
        # Row-count slice would keep 6 regime rows — wrong for 4:1 alignment.
        self.assertLess(len(ctx.df_regime), 6)


class TestPositionSizing(unittest.TestCase):
    def test_sizing_uses_sl_distance(self):
        sim = PositionSimulator(
            initial_balance=1000.0,
            risk_pct=0.1,
            sl_atr_mult=2.0,
            fee_pct=0.0,
        )
        signal = buy("test", volatility=5.0)
        ok = sim.try_open(entry_price=100.0, atr=5.0, bar_time=1, signal=signal)
        self.assertTrue(ok)
        # sl_distance = 5 * 2 = 10 → qty = 1000 * 0.1 / 10 = 10
        self.assertAlmostEqual(sim.position.qty, 10.0)

    def test_max_position_cap(self):
        sim = PositionSimulator(
            initial_balance=1000.0,
            risk_pct=0.5,
            sl_atr_mult=2.0,
            max_position_pct=0.28,
            fee_pct=0.0,
        )
        signal = buy("test", volatility=5.0)
        sim.try_open(entry_price=100.0, atr=5.0, bar_time=1, signal=signal)
        notional = sim.position.qty * 100.0
        self.assertLessEqual(notional, 1000.0 * 0.28 + 1e-6)


class TestSlConfirmAndFills(unittest.TestCase):
    def test_sl_confirm_delays_exit(self):
        from xauby.observability.replay import SimPosition

        sim = PositionSimulator(sl_confirm_ticks=3, fee_pct=0.0)
        sim.position = SimPosition(
            entry_price=100.0,
            stop_loss=99.0,
            highest_price=100.0,
            qty=1.0,
            entry_time=1,
        )
        hold_sig = hold("wait")
        # Open above SL, low pierces SL — one breach, not confirmed yet.
        sim.update_sl_breach(99.5)
        self.assertEqual(sim.sl_breach_bars, 0)
        self.assertFalse(sim.sl_confirmed)
        events = sim.on_bar(
            open_p=99.5,
            high=100.0,
            low=98.5,
            close=99.0,
            atr=1.0,
            zone="GREEN",
            bar_time=2,
            signal=hold_sig,
        )
        sim.update_sl_breach(98.5)
        self.assertEqual(sim.sl_breach_bars, 1)
        self.assertTrue(sim.has_position)
        self.assertNotIn("position_closed", [e.get("event_type") for e in events])

        # Three consecutive low breaches → confirmed; SELL closes.
        sim.update_sl_breach(98.5)
        sim.update_sl_breach(98.5)
        self.assertTrue(sim.sl_confirmed)
        sell_sig = sell("SL confirmed")
        events = sim.on_bar(
            open_p=99.5,
            high=100.0,
            low=98.0,
            close=99.0,
            atr=1.0,
            zone="GREEN",
            bar_time=3,
            signal=sell_sig,
        )
        self.assertFalse(sim.has_position)
        self.assertIn("position_closed", [e.get("event_type") for e in events])

    def test_same_bar_sl_after_entry(self):
        sim = PositionSimulator(sl_confirm_ticks=3, sl_atr_mult=2.0, fee_pct=0.0)
        buy_sig = buy("entry", volatility=2.0)
        # Entry at open 100, SL 96; low 95 on same bar — no gap open, no immediate low exit.
        events = sim.on_bar(
            open_p=100.0,
            high=101.0,
            low=95.0,
            close=99.0,
            atr=2.0,
            zone="GREEN",
            bar_time=1,
            signal=buy_sig,
        )
        self.assertIn("position_opened", [e.get("event_type") for e in events])
        self.assertTrue(sim.has_position)
        self.assertNotIn("position_closed", [e.get("event_type") for e in events])

        # Gap open below SL on a later bar exits without waiting for confirm ticks.
        events2 = sim.on_bar(
            open_p=95.0,
            high=96.0,
            low=94.0,
            close=95.0,
            atr=2.0,
            zone="GREEN",
            bar_time=2,
            signal=hold("hold"),
        )
        self.assertFalse(sim.has_position)
        self.assertIn("stop_loss_triggered", [e.get("event_type") for e in events2])

    def test_gap_open_immediate_sl(self):
        sim = PositionSimulator(sl_confirm_ticks=5, fee_pct=0.0)
        from xauby.observability.replay import SimPosition

        sim.position = SimPosition(
            entry_price=100.0,
            stop_loss=99.0,
            highest_price=100.0,
            qty=1.0,
            entry_time=1,
        )
        events = sim.on_bar(
            open_p=98.0,
            high=99.0,
            low=97.0,
            close=98.0,
            atr=1.0,
            zone="GREEN",
            bar_time=2,
            signal=hold("hold"),
        )
        self.assertFalse(sim.has_position)
        closed = [e for e in events if e.get("event_type") == "position_closed"]
        self.assertEqual(closed[0]["exit"], 98.0)


class TestSlConfirmAccumulatesAcrossBars(unittest.TestCase):
    """Regression: the replay loop must let sl_confirm_ticks accumulate.

    Previously the loop updated the breach counter with the bar OPEN before
    building the context, which reset the count every bar (open is usually back
    above the stop) so a strategy's ``ctx.sl_confirmed`` SELL never fired in
    backtest — a live/backtest parity break."""

    def test_confirmed_sell_fires_after_n_bars(self):
        from xauby.observability.replay import SimPosition

        # 6 bars: open always 100 (no gap through the 99 stop), low 98 breaches.
        rows = []
        for i in range(6):
            rows.append(
                {
                    "timestamp": 1_700_000_000 + i * 14_400,
                    "open": 100.0,
                    "high": 100.5,
                    "low": 98.0,
                    "close": 99.5,
                    "volume": 1000.0,
                }
            )
        df = pd.DataFrame(rows)

        # disable_stop_loss keeps the trailing logic from moving the seeded stop;
        # we are exercising the strategy-confirmed SELL path, not the gap stop.
        sim = PositionSimulator(sl_confirm_ticks=3, disable_stop_loss=True, fee_pct=0.0)
        sim.position = SimPosition(
            entry_price=100.0, stop_loss=99.0, highest_price=100.0,
            qty=1.0, entry_time=0, initial_sl=99.0,
        )

        runner = MagicMock()

        def _run(ctx):
            if ctx.has_position and ctx.sl_confirmed:
                return sell("Stop loss confirmed", volatility=0.0)
            return hold("wait", volatility=0.0)

        runner.run.side_effect = _run
        engine = ReplayEngine(runner=runner, simulator=sim, strategy_config={}, engine_config={})
        engine.replay_bars(df, min_bars=0)

        self.assertFalse(sim.has_position)
        self.assertEqual(len(sim.trades), 1)
        self.assertEqual(sim.trades[0].trigger, "STOP_LOSS")


class TestReplayEngineAtr(unittest.TestCase):
    def test_atr_from_signal_volatility(self):
        primary = _make_primary(105)
        runner = MagicMock()
        captured_atr = []

        def _run(ctx):
            sig = hold("noop", volatility=42.5)
            return sig

        runner.run.side_effect = _run
        sim = PositionSimulator(fee_pct=0.0)
        engine = ReplayEngine(
            runner=runner,
            simulator=sim,
            strategy_config={},
            engine_config={},
        )

        original_on_bar = sim.on_bar

        def _capture_on_bar(**kwargs):
            captured_atr.append(kwargs.get("atr"))
            return []

        with patch.object(sim, "on_bar", side_effect=_capture_on_bar):
            engine.replay_bars(primary, min_bars=100)

        self.assertTrue(captured_atr)
        self.assertEqual(captured_atr[0], 42.5)


if __name__ == "__main__":
    unittest.main()
