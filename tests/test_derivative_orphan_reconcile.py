from __future__ import annotations

import unittest

from xauby.engine.reconcile import ReconcileMixin
from xauby.engine.symbol_context import SymbolContext


class _DB:
    def __init__(self, state: dict):
        self.state = dict(state)
        self.saved: list[dict] = []

    def save_trade_state(self, *args, **kwargs):
        if args and len(args) >= 2 and args[1] == "idle":
            self.state = {"symbol": args[0], "state": "idle"}
            self.saved.append(dict(self.state))
            return
        self.state.update(kwargs)
        self.saved.append(dict(kwargs))


class _Client:
    def __init__(self, positions: list[dict]):
        self.positions = positions

    def get_positions(self, symbols=None):
        del symbols
        return list(self.positions)


class _Engine(ReconcileMixin):
    def __init__(self, state: dict, positions: list[dict]):
        self.db = _DB(state)
        self.client = _Client(positions)
        self.context = SymbolContext(symbol="XAUUSDT")
        self.alerts: list[str] = []

    def _sc(self, symbol=None):
        del symbol
        return self.context

    def send_telegram_alert(self, message, level=None):
        del level
        self.alerts.append(message)

    def _cancel_orphan_orders(self, **kwargs):
        del kwargs


class DerivativeOrphanReconcileTests(unittest.TestCase):
    def test_manual_short_is_adopted_and_visible_to_runtime(self):
        engine = _Engine(
            {"symbol": "XAUUSDT", "state": "idle"},
            [{
                "symbol": "XAU/USDT:USDT",
                "position_side": "SHORT",
                "quantity": 0.25,
                "entry_price": 2400.0,
                "mark_price": 2390.0,
                "leverage": 1,
                "margin_mode": "isolated",
            }],
        )

        engine._reconcile_derivative_position("XAUUSDT", engine.db.state)

        saved = engine.db.saved[-1]
        self.assertEqual(saved["state"], "bought")
        self.assertEqual(saved["position_side"], "SHORT")
        self.assertEqual(saved["management_mode"], "manual")
        self.assertEqual(saved["quantity"], 0.25)
        self.assertEqual(saved["entry_price"], 2400.0)
        self.assertFalse(engine.context.feed_snapshot()["trading_halted"])
        self.assertIn("MANUAL DERIVATIVE POSITION DETECTED", engine.alerts[-1])

    def test_manual_position_side_change_is_reconciled_without_halt(self):
        engine = _Engine(
            {
                "symbol": "XAUUSDT",
                "state": "bought",
                "position_side": "LONG",
                "quantity": 0.1,
                "entry_price": 2350.0,
                "management_mode": "manual",
            },
            [{
                "symbol": "XAUUSDT",
                "position_side": "SHORT",
                "quantity": 0.2,
                "entry_price": 2410.0,
                "leverage": 1,
                "margin_mode": "isolated",
            }],
        )

        engine._reconcile_derivative_position("XAUUSDT", engine.db.state)

        saved = engine.db.saved[-1]
        self.assertEqual(saved["position_side"], "SHORT")
        self.assertEqual(saved["quantity"], 0.2)
        self.assertEqual(saved["management_mode"], "manual")
        self.assertFalse(engine.context.feed_snapshot()["trading_halted"])

    def test_strategy_position_side_mismatch_remains_fail_closed(self):
        engine = _Engine(
            {
                "symbol": "XAUUSDT",
                "state": "bought",
                "position_side": "LONG",
                "quantity": 0.1,
                "entry_price": 2350.0,
                "management_mode": "strategy",
            },
            [{
                "symbol": "XAUUSDT",
                "position_side": "SHORT",
                "quantity": 0.2,
                "entry_price": 2410.0,
            }],
        )

        engine._reconcile_derivative_position("XAUUSDT", engine.db.state)

        self.assertEqual(engine.db.saved, [])
        self.assertTrue(engine.context.feed_snapshot()["trading_halted"])


if __name__ == "__main__":
    unittest.main()
