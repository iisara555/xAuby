"""trading_halted must freeze exits as well as entries.

Regression: with the tracked side drifted (POSITION SIDE MISMATCH halt), the
tick loop kept firing execute_close_short every tick — a live reduce-only
order against the wrong direction. Every trading_halted source means the
tracked position state is unreliable, so close paths must refuse too.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from xauby.engine.trading import LiteTradingEngine
from tests.mocks import MockExchangeGateway, MockDatabaseRepository, MockNotificationService

HALT_REASON = "POSITION SIDE MISMATCH XAUUSDT: local=SHORT exchange=LONG"

SHORT_STATE = {
    "state": "bought", "position_side": "SHORT", "entry_price": 100.0,
    "stop_loss": 0.0, "take_profit": 0.0, "highest_price_seen": 100.0,
    "quantity": 1.0, "opened_at": "2026-06-21T00:00:00", "funding_paid": 0.0,
}

LONG_STATE = dict(SHORT_STATE, position_side="LONG")


class TestHaltBlocksCloses(unittest.TestCase):
    def _engine(self, sym="XAUUSDT", halted=True):
        engine = LiteTradingEngine(
            config_path="bot_config.yaml",
            client=MockExchangeGateway(),
            db=MockDatabaseRepository(),
            notification_service=MockNotificationService(),
        )
        engine._active_tick_symbol = sym
        if halted:
            engine._sc(sym).set_trading_halted(True, HALT_REASON)
        return engine, sym

    def _broker_patch(self, engine):
        # Any broker call while halted is the bug this suite guards against.
        return patch.object(
            engine, "_broker_for_symbol",
            side_effect=AssertionError("broker touched while trading halted"),
        )

    def test_close_short_blocked_while_halted(self):
        engine, sym = self._engine()
        with self._broker_patch(engine):
            self.assertFalse(
                engine.execute_close_short(dict(SHORT_STATE), 100.0, "test", symbol=sym)
            )

    def test_close_long_blocked_while_halted(self):
        engine, sym = self._engine()
        with self._broker_patch(engine):
            self.assertFalse(
                engine.execute_close_long(dict(LONG_STATE), 100.0, "test", symbol=sym)
            )

    def test_execute_sell_blocked_while_halted(self):
        engine, sym = self._engine()
        with self._broker_patch(engine):
            self.assertFalse(
                engine.execute_sell(dict(LONG_STATE), 100.0, "test", symbol=sym)
            )

    def test_partial_tp_blocked_while_halted(self):
        engine, sym = self._engine()
        with self._broker_patch(engine):
            self.assertFalse(
                engine.execute_partial_tp(
                    dict(LONG_STATE), 120.0, fraction=0.5, threshold_pct=12.0, symbol=sym
                )
            )

    def test_close_halt_reason_clear_when_not_halted(self):
        engine, sym = self._engine(halted=False)
        self.assertEqual(engine._close_halt_reason(sym), "")

    def test_halt_reason_reported(self):
        engine, sym = self._engine()
        self.assertEqual(engine._close_halt_reason(sym), HALT_REASON)


if __name__ == "__main__":
    unittest.main()
