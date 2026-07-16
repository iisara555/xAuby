from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from xauby.database.db import LiteDB
from xauby.engine.exchange_close import build_confirmed_trade, match_position_history
from xauby.engine.reconcile import ReconcileMixin
from xauby.engine.symbol_context import SymbolContext
from scripts import reconcile_exchange_close


OPENED_AT = "2026-07-08T01:50:15.777000+00:00"
HISTORY_ROW = {
    "exchange_close_id": "okx:3708019708102549504:1784206521546",
    "exchange_position_id": "3708019708102549504",
    "exchange": "okx",
    "symbol": "XAUUSDT",
    "position_side": "SHORT",
    "quantity": 0.033,
    "entry_price": 4115.3,
    "exit_price": 4000.5,
    "realized_pnl": 3.8292371028625467,
    "gross_pnl": 3.7884,
    "fee_cost": 0.1339107,
    "funding_fee": 0.1747478028625467,
    "close_type": "2",
    "opened_timestamp": 1783475415777,
    "closed_timestamp": 1784206521546,
    "opened_at": OPENED_AT,
    "closed_at": "2026-07-16T12:55:21.546000+00:00",
}


class _Client:
    def __init__(self, history=None, error: Exception | None = None):
        self.history = list(history or [])
        self.error = error

    def get_positions(self, symbols=None):
        del symbols
        return []

    def get_position_history(self, symbol, since=None, limit=100):
        del symbol, since, limit
        if self.error:
            raise self.error
        return list(self.history)


class _Engine(ReconcileMixin):
    def __init__(self, db: LiteDB, client: _Client):
        self.db = db
        self.client = client
        self.config = {
            "exchange": {
                "provider": "ccxt",
                "ccxt_id": "okx",
                "market_type": "swap",
            }
        }
        self.focus_symbol = "XAUUSDT"
        self.context = SymbolContext(symbol="XAUUSDT")
        self.alerts: list[str] = []
        self.events: list[tuple[str, dict]] = []
        self.last_signal_meta = {}

    def _sc(self, symbol=None):
        del symbol
        return self.context

    def _strategy_name_for_symbol(self, symbol):
        del symbol
        return "xauby_actionzone"

    def _execution_mode(self, symbol):
        del symbol
        return "live"

    def _cancel_orphan_orders(self, **kwargs):
        del kwargs

    def _emit_event(self, event, **fields):
        self.events.append((event, fields))

    def send_telegram_alert(self, message, level=None):
        del level
        self.alerts.append(message)


def _open_state(db: LiteDB, *, quantity: float = 0.033) -> None:
    db.save_trade_state(
        symbol="XAUUSDT",
        state="bought",
        entry_price=4115.3,
        stop_loss=4180.0,
        take_profit=4000.5,
        highest_price_seen=4115.3,
        quantity=quantity,
        opened_at=OPENED_AT,
        last_transition_at=OPENED_AT,
        position_side="SHORT",
        leverage=1.0,
        margin_mode="isolated",
        exchange_position_id="3708019708102549504",
    )


class ExchangeCloseReconciliationTests(unittest.TestCase):
    def test_recovery_dry_run_does_not_open_tenant_database(self):
        client = MagicMock()
        client.get_position_history.return_value = [HISTORY_ROW]
        with (
            patch.object(
                reconcile_exchange_close,
                "load_bot_config",
                return_value={"exchange": {"market_type": "swap"}},
            ),
            patch.object(
                reconcile_exchange_close,
                "resolve_exchange_credentials",
                return_value=("key", "secret", None),
            ),
            patch.object(
                reconcile_exchange_close,
                "create_exchange_client",
                return_value=client,
            ),
            patch.object(reconcile_exchange_close, "LiteDB") as db_class,
        ):
            result = reconcile_exchange_close.main(["--symbol", "XAUUSDT"])

        self.assertEqual(result, 0)
        db_class.assert_not_called()
        client.close.assert_called_once()

    def test_exchange_flat_closes_local_state_with_verified_pnl_and_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = LiteDB(os.path.join(tmp, "tenant.db"))
            _open_state(db)
            engine = _Engine(db, _Client([HISTORY_ROW]))

            engine._reconcile_derivative_position(
                "XAUUSDT", db.get_trade_state("XAUUSDT")
            )

            self.assertEqual(db.get_trade_state("XAUUSDT").state, "idle")
            trade = db.get_closed_trades("XAUUSDT", limit=1)[0]
            self.assertAlmostEqual(trade["net_pnl"], HISTORY_ROW["realized_pnl"])
            self.assertEqual(trade["pnl_source"], "okx_positions_history")
            self.assertEqual(trade["pnl_confirmed"], 1)
            self.assertEqual(trade["exchange_close_id"], HISTORY_ROW["exchange_close_id"])
            self.assertEqual(trade["trigger"], "Exchange Take Profit")
            self.assertEqual(db.get_pending_exchange_closures("XAUUSDT"), [])
            self.assertEqual(engine.last_signal_meta["action"], "HOLD")
            self.assertTrue(engine.context.consume_exchange_reconcile_wait())
            self.assertFalse(engine.context.consume_exchange_reconcile_wait())
            self.assertFalse(engine.context.feed_snapshot()["trading_halted"])
            self.assertEqual(len(engine.events), 1)

    def test_history_failure_keeps_flat_wait_and_blocks_entry_until_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = LiteDB(os.path.join(tmp, "tenant.db"))
            _open_state(db)
            client = _Client(error=RuntimeError("OKX history unavailable"))
            engine = _Engine(db, client)

            engine._reconcile_derivative_position(
                "XAUUSDT", db.get_trade_state("XAUUSDT")
            )

            self.assertEqual(db.get_trade_state("XAUUSDT").state, "idle")
            self.assertEqual(db.get_closed_trades("XAUUSDT"), [])
            self.assertEqual(len(db.get_pending_exchange_closures("XAUUSDT")), 1)
            self.assertTrue(engine.context.blocks_new_entries())
            self.assertTrue(engine.context.feed_snapshot()["trading_halted"])

            client.error = None
            client.history = [HISTORY_ROW]
            engine._reconcile_derivative_position(
                "XAUUSDT", db.get_trade_state("XAUUSDT")
            )

            self.assertEqual(len(db.get_closed_trades("XAUUSDT")), 1)
            self.assertEqual(db.get_pending_exchange_closures("XAUUSDT"), [])
            self.assertFalse(engine.context.blocks_new_entries())
            self.assertFalse(engine.context.feed_snapshot()["trading_halted"])

    def test_ambiguous_history_stays_pending_without_fake_pnl(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = LiteDB(os.path.join(tmp, "tenant.db"))
            _open_state(db)
            duplicate = {
                **HISTORY_ROW,
                "exchange_close_id": "okx:3708019708102549504:1784206521547",
            }
            engine = _Engine(db, _Client([HISTORY_ROW, duplicate]))

            engine._reconcile_derivative_position(
                "XAUUSDT", db.get_trade_state("XAUUSDT")
            )

            self.assertEqual(db.get_trade_state("XAUUSDT").state, "idle")
            self.assertEqual(db.get_closed_trades("XAUUSDT"), [])
            pending = db.get_pending_exchange_closures("XAUUSDT")
            self.assertEqual(len(pending), 1)
            self.assertIn("ambiguous", pending[0]["last_error"])

    def test_retry_and_repeated_reconciliation_do_not_duplicate_trade(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = LiteDB(os.path.join(tmp, "tenant.db"))
            _open_state(db)
            engine = _Engine(db, _Client([HISTORY_ROW]))

            engine._reconcile_derivative_position(
                "XAUUSDT", db.get_trade_state("XAUUSDT")
            )
            engine._reconcile_derivative_position(
                "XAUUSDT", db.get_trade_state("XAUUSDT")
            )

            self.assertEqual(len(db.get_closed_trades("XAUUSDT")), 1)

    def test_partial_tp_is_subtracted_from_cumulative_exchange_total(self):
        state = {
            "position_side": "SHORT",
            "quantity": 0.0165,
            "entry_price": 4115.3,
            "opened_at": OPENED_AT,
        }
        trade = build_confirmed_trade(
            state,
            HISTORY_ROW,
            symbol="XAUUSDT",
            strategy_name="xauby_actionzone",
            prior_trades=[{
                "opened_at": OPENED_AT,
                "net_pnl": 1.25,
                "total_fees": 0.05,
                "funding_fee": 0.02,
            }],
        )

        self.assertAlmostEqual(trade["net_pnl"], HISTORY_ROW["realized_pnl"] - 1.25)
        self.assertAlmostEqual(trade["total_fees"], HISTORY_ROW["fee_cost"] - 0.05)
        self.assertAlmostEqual(trade["funding_fee"], HISTORY_ROW["funding_fee"] - 0.02)
        self.assertAlmostEqual(trade["amount"], 0.0165)

    def test_match_rejects_reused_position_id_without_unique_close(self):
        second = {
            **HISTORY_ROW,
            "exchange_close_id": "okx:3708019708102549504:1784206521547",
        }
        selected, error = match_position_history(
            {
                "position_side": "SHORT",
                "opened_at": OPENED_AT,
                "entry_price": 4115.3,
                "exchange_position_id": "3708019708102549504",
            },
            [HISTORY_ROW, second],
        )

        self.assertIsNone(selected)
        self.assertIn("ambiguous", error)

    def test_match_fails_closed_when_position_id_or_entry_does_not_match(self):
        selected, error = match_position_history(
            {
                "position_side": "SHORT",
                "opened_at": OPENED_AT,
                "entry_price": 4115.3,
                "exchange_position_id": "different-position",
            },
            [HISTORY_ROW],
        )
        self.assertIsNone(selected)
        self.assertIn("position id", error)

        selected, error = match_position_history(
            {
                "position_side": "SHORT",
                "opened_at": OPENED_AT,
                "entry_price": 5000.0,
                "exchange_position_id": HISTORY_ROW["exchange_position_id"],
            },
            [HISTORY_ROW],
        )
        self.assertIsNone(selected)
        self.assertIn("entry price", error)


if __name__ == "__main__":
    unittest.main()
