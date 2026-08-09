"""Regression tests for the 2026-06 audit fixes (race conditions / state bugs)."""
import os
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import MagicMock, patch

from xauby.engine.trading import LiteTradingEngine
from xauby.engine.brokers.sim_broker import SimBroker
from xauby.database.db import LiteDB
from tests.mocks import (
    MockExchangeGateway,
    MockDatabaseRepository,
    MockNotificationService,
    install_test_pair,
)


def make_engine():
    engine = LiteTradingEngine(
        config_path="bot_config.yaml",
        client=MockExchangeGateway(),
        db=MockDatabaseRepository(),
        notification_service=MockNotificationService(),
    )
    install_test_pair(engine, "XAUTUSDT", execution_mode="live")
    install_test_pair(engine, "BTCUSDT", execution_mode="sim")
    install_test_pair(engine, "SOLUSDT", execution_mode="live")
    return engine


class TestPerSymbolExecutionGating(unittest.TestCase):
    """Sim-mode symbols must never touch real exchange SL/reconcile paths,
    even when the engine runs live globally (XAU live + BTC sim)."""

    def setUp(self):
        self.engine = make_engine()
        self.engine.simulate_only = False
        self.engine.live_trading_allowed = True
        # Force a live/sim mix regardless of the deployed whitelist values so
        # these tests stay valid when pair modes change in coin_whitelist.json.
        for sym, mode in (("XAUTUSDT", "live"), ("BTCUSDT", "sim")):
            spec = self.engine._pair_registry.get(sym)
            self.engine._pair_registry.update_spec(replace(spec, execution_mode=mode))

    def test_execution_mode_follows_whitelist(self):
        self.assertEqual(self.engine._execution_mode("XAUTUSDT"), "live")
        self.assertEqual(self.engine._execution_mode("BTCUSDT"), "sim")

    def test_ensure_sl_protected_noop_for_sim_symbol(self):
        state = {
            "state": "bought",
            "quantity": 0.5,
            "stop_loss": 100.0,
            "stop_loss_order_id": None,
            "entry_price": 110.0,
            "take_profit": 0.0,
            "highest_price_seen": 110.0,
            "opened_at": "2026-06-11T00:00:00",
        }
        with patch.object(self.engine, "_place_sl_with_retry") as place_sl:
            out = self.engine._ensure_sl_protected(state, symbol="BTCUSDT")
        place_sl.assert_not_called()
        self.assertIs(out, state)

    def test_cancel_orphan_orders_noop_for_sim_symbol(self):
        self.engine.client.get_open_orders = MagicMock()
        self.engine._cancel_orphan_orders(symbol="BTCUSDT")
        self.engine.client.get_open_orders.assert_not_called()

    def test_reconcile_skips_sim_symbols(self):
        with patch.object(self.engine, "_reconcile_single_symbol") as rec:
            self.engine.reconcile_startup_state()
        called_syms = [c.args[0] for c in rec.call_args_list]
        self.assertIn("XAUTUSDT", called_syms)
        self.assertNotIn("BTCUSDT", called_syms)


class TestSellDustDoesNotResurrectPosition(unittest.TestCase):
    """execute_sell dust path cleared the position, then the finally block
    used to restore it back to 'bought' (zombie position)."""

    def setUp(self):
        self.engine = make_engine()
        self.engine.simulate_only = False
        self.engine.live_trading_allowed = True
        self.engine.read_only = False

    def test_dust_clear_skips_sl_restore(self):
        self.engine.client.get_balances = lambda: {"XAUT": {"available": 1e-7, "reserved": 0.0}}
        self.engine.client.get_symbol_filters = lambda s: {"minQty": 0.001, "stepSize": 0.001}
        self.engine._should_close_long_with_broker = lambda state, symbol: False
        save_state = MagicMock()
        self.engine.db.save_trade_state = save_state
        state = {
            "state": "bought",
            "quantity": 0.0005,
            "entry_price": 2000.0,
            "stop_loss": 1950.0,
            "take_profit": 0.0,
            "highest_price_seen": 2000.0,
            "opened_at": "2026-06-11T00:00:00",
            "stop_loss_order_id": None,
        }
        with patch.object(self.engine, "_restore_stop_loss_state") as restore:
            ok = self.engine.execute_sell(state, 2000.0, "test exit", symbol="XAUTUSDT")
        self.assertFalse(ok)
        restore.assert_not_called()
        save_state.assert_called_once_with("XAUTUSDT", "idle")


class TestStopLossPlacementUsesAvailableBalance(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine()
        self.engine.simulate_only = False
        self.engine.live_trading_allowed = True

    def test_sl_qty_caps_to_available_balance_after_buy_fee(self):
        self.engine.client.get_balances = MagicMock(
            return_value={"XAUT": {"available": 0.0112887, "reserved": 0.0}}
        )
        self.engine.client.get_symbol_filters = MagicMock(
            return_value={
                "minQty": 0.0001,
                "stepSize": 0.0001,
                "tickSize": 0.01,
            }
        )
        self.engine.client.place_order = MagicMock(return_value={"orderId": "sl-1"})

        result = self.engine._place_sl_with_retry(
            qty=0.0113,
            stop_loss=4153.58,
            symbol="XAUTUSDT",
        )

        self.assertEqual(result, ("sl-1", 0.0112))
        self.engine.client.place_order.assert_called_once()
        self.assertAlmostEqual(self.engine.client.place_order.call_args.kwargs["amount"], 0.0112)

    def test_sl_does_not_place_dust_order_when_balance_locked_by_existing_stop(self):
        self.engine.client.get_balances = MagicMock(
            return_value={"SOL": {"available": 0.003319, "reserved": 0.68}}
        )
        self.engine.client.get_symbol_filters = MagicMock(
            return_value={
                "minQty": 0.001,
                "stepSize": 0.001,
                "tickSize": 0.01,
                "minNotional": 10.0,
            }
        )
        self.engine.client.place_order = MagicMock(return_value={"orderId": "dust-sl"})

        result = self.engine._place_sl_with_retry(
            qty=0.681,
            stop_loss=69.64,
            symbol="SOLUSDT",
        )

        self.assertIsNone(result)
        self.engine.client.place_order.assert_not_called()


class TestExchangeStopLossPartialFill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = make_engine()
        self.engine.simulate_only = False
        self.engine.live_trading_allowed = True
        self.engine.db = LiteDB(os.path.join(self.tmp.name, "test.db"))
        self.engine._symbol_fee_pct = lambda symbol: 0.001
        self.events = []
        self.engine._emit_event = lambda event_type, **payload: self.events.append((event_type, payload))
        self.engine.db.save_trade_state(
            symbol="XAUTUSDT",
            state="bought",
            entry_price=2000.0,
            stop_loss=1950.0,
            take_profit=0.0,
            highest_price_seen=2050.0,
            quantity=1.0,
            opened_at="2026-06-11T00:00:00",
            last_transition_at="2026-06-11T00:00:00",
            stop_loss_order_id="sl-1",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_partial_exchange_sl_fill_records_closed_trade_and_keeps_remainder(self):
        self.engine.client.get_order = MagicMock(
            return_value={
                "status": "PARTIALLY_FILLED",
                "executedQty": "0.4",
                "cummulativeQuoteQty": "780",
            }
        )
        self.engine.client.cancel_order = MagicMock()
        with patch.object(
            self.engine, "_place_sl_with_retry", return_value=("sl-2", 0.6)
        ):
            state = self.engine._handle_exchange_sl_order(
                self.engine.db.get_trade_state("XAUTUSDT"),
                1940.0,
                symbol="XAUTUSDT",
            )

        self.assertEqual(state.state, "bought")
        self.assertAlmostEqual(state.quantity, 0.6)
        self.assertEqual(state.stop_loss_order_id, "sl-2")
        trades = self.engine.db.get_closed_trades("XAUTUSDT", limit=10)
        self.assertEqual(len(trades), 1)
        self.assertAlmostEqual(trades[0]["amount"], 0.4)
        self.assertEqual(trades[0]["trigger"], "Exchange-Side Stop Loss Partial Fill")
        self.assertAlmostEqual(trades[0]["entry_fee"], 0.8, places=6)
        self.assertAlmostEqual(trades[0]["exit_fee"], 0.78, places=6)
        self.assertAlmostEqual(trades[0]["net_pnl"], -21.58, places=6)

    def test_full_exchange_sl_fill_records_and_reports_net_pnl_after_fees(self):
        self.engine.client.get_order = MagicMock(
            return_value={
                "status": "FILLED",
                "executedQty": "1.0",
                "cummulativeQuoteQty": "1950",
            }
        )

        state = self.engine._handle_exchange_sl_order(
            self.engine.db.get_trade_state("XAUTUSDT"),
            1940.0,
            symbol="XAUTUSDT",
        )

        self.assertEqual(state.state, "idle")
        trade = self.engine.db.get_closed_trades("XAUTUSDT", limit=1)[0]
        self.assertAlmostEqual(trade["entry_fee"], 2.0, places=6)
        self.assertAlmostEqual(trade["exit_fee"], 1.95, places=6)
        self.assertAlmostEqual(trade["net_pnl"], -53.95, places=6)
        closed_events = [payload for event_type, payload in self.events if str(event_type).endswith("position_closed")]
        self.assertEqual(closed_events[-1]["pnl"], -53.95)
        self.assertIn("PnL: -53.95 USDT", self.engine.last_log_message)

    def test_full_exchange_sl_caps_contract_unit_mismatch_and_uses_average_fill(self):
        self.engine.client.get_order = MagicMock(
            return_value={
                "status": "FILLED",
                "executedQty": "100.0",
                "average": "1945.0",
                "cummulativeQuoteQty": "194500",
            }
        )

        state = self.engine._handle_exchange_sl_order(
            self.engine.db.get_trade_state("XAUTUSDT"),
            1940.0,
            symbol="XAUTUSDT",
        )

        self.assertEqual(state.state, "idle")
        trade = self.engine.db.get_closed_trades("XAUTUSDT", limit=1)[0]
        self.assertAlmostEqual(trade["amount"], 1.0)
        self.assertAlmostEqual(trade["exit_price"], 1945.0)
        self.assertAlmostEqual(trade["net_pnl"], -58.945, places=6)

    def test_okx_swap_stop_uses_exchange_confirmed_realized_pnl(self):
        self.engine.client.capabilities["position_history"] = True
        self.engine.client.get_order = MagicMock(
            return_value={
                "status": "FILLED",
                "executedQty": "1.0",
                "average": "1950.0",
                "cummulativeQuoteQty": "1950.0",
            }
        )
        self.engine.client.get_position_history = MagicMock(
            return_value=[{
                "exchange_close_id": "okx:pos-1:1781136060000",
                "exchange_position_id": "pos-1",
                "exchange": "okx",
                "symbol": "XAUTUSDT",
                "position_side": "LONG",
                "quantity": 1.0,
                "entry_price": 2000.0,
                "exit_price": 1950.0,
                "realized_pnl": -53.97,
                "gross_pnl": -50.0,
                "fee_cost": 3.95,
                "funding_fee": -0.02,
                "close_type": "2",
                "opened_timestamp": 1781136000000,
                "closed_timestamp": 1781136060000,
                "opened_at": "2026-06-11T00:00:00.000Z",
                "closed_at": "2026-06-11T00:01:00.000Z",
            }]
        )

        state = self.engine._handle_exchange_sl_order(
            self.engine.db.get_trade_state("XAUTUSDT"),
            1940.0,
            symbol="XAUTUSDT",
        )

        self.assertEqual(state.state, "idle")
        trade = self.engine.db.get_closed_trades("XAUTUSDT", limit=1)[0]
        self.assertAlmostEqual(trade["amount"], 1.0)
        self.assertAlmostEqual(trade["exit_price"], 1950.0)
        self.assertAlmostEqual(trade["net_pnl"], -53.97)
        self.assertAlmostEqual(trade["funding_fee"], -0.02)
        self.assertEqual(trade["trigger"], "Exchange-Side Stop Loss")
        self.assertEqual(trade["pnl_source"], "okx_positions_history")
        self.assertEqual(trade["pnl_confirmed"], 1)
        self.assertEqual(self.engine.db.get_pending_exchange_closures("XAUTUSDT"), [])
        self.assertIn("Realized PnL -53.9700 USDT", self.engine.last_log_message)

    def test_okx_swap_stop_blocks_new_entries_while_history_is_pending(self):
        self.engine.client.capabilities["position_history"] = True
        self.engine.client.get_order = MagicMock(
            return_value={
                "status": "FILLED",
                "executedQty": "1.0",
                "average": "1950.0",
                "cummulativeQuoteQty": "1950.0",
            }
        )
        self.engine.client.get_position_history = MagicMock(return_value=[])

        state = self.engine._handle_exchange_sl_order(
            self.engine.db.get_trade_state("XAUTUSDT"),
            1940.0,
            symbol="XAUTUSDT",
        )

        self.assertEqual(state.state, "idle")
        self.assertEqual(self.engine.db.get_closed_trades("XAUTUSDT", limit=10), [])
        pending = self.engine.db.get_pending_exchange_closures("XAUTUSDT")
        self.assertEqual(len(pending), 1)
        self.assertIn("verification pending", self.engine.last_log_message)
        self.assertTrue(self.engine._sc("XAUTUSDT").exchange_reconcile_pending)


class TestSemiAutoConfirmTargetsPendingSymbol(unittest.TestCase):
    """Telegram confirm/skip arrive without a symbol; they must land on the
    pair that queued the confirmation, not whatever pair is ticking."""

    def setUp(self):
        self.engine = make_engine()

    def test_confirm_resolves_pending_symbol(self):
        signal = MagicMock()
        signal.stop_loss_price = None
        signal.stop_loss_distance = None
        self.engine._queue_semi_auto_buy(2000.0, 1.0, signal, symbol="XAUTUSDT")

        self.engine._active_tick_symbol = "BTCUSDT"
        try:
            self.engine.confirm_semi_auto_buy()
        finally:
            self.engine._active_tick_symbol = None

        self.assertTrue(self.engine.contexts["XAUTUSDT"].semi_auto_confirmed())
        self.assertFalse(self.engine.contexts["BTCUSDT"].semi_auto_confirmed())

    def test_skip_resolves_pending_symbol(self):
        signal = MagicMock()
        signal.stop_loss_price = None
        signal.stop_loss_distance = None
        self.engine._queue_semi_auto_buy(2000.0, 1.0, signal, symbol="XAUTUSDT")

        self.engine._active_tick_symbol = "BTCUSDT"
        try:
            self.engine.skip_semi_auto_buy()
        finally:
            self.engine._active_tick_symbol = None

        self.assertFalse(self.engine.contexts["XAUTUSDT"].has_semi_auto_pending())


class TestSimBrokerAtomicity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.balance_file = os.path.join(self.tmp.name, "sim_balance.json")
        self.broker = SimBroker(balance_file=self.balance_file, initial_balance=1000.0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_buy_writes_balance_and_ledger_together(self):
        res = self.broker.execute_buy("BTCUSDT", qty=0.01, price=50000.0, notional=500.0)
        self.assertTrue(res.success)
        bal = self.broker.get_usdt_balance()
        self.assertAlmostEqual(bal, 1000.0 - 500.0 - res.fees, places=6)
        ledger = self.broker.get_ledger("BTCUSDT")
        self.assertEqual(ledger.quantity, 0.01)
        self.assertEqual(ledger.entry_price, 50000.0)

    def test_insufficient_balance_changes_nothing(self):
        res = self.broker.execute_buy("BTCUSDT", qty=1.0, price=50000.0, notional=50000.0)
        self.assertFalse(res.success)
        self.assertAlmostEqual(self.broker.get_usdt_balance(), 1000.0, places=6)
        self.assertEqual(self.broker.get_ledger("BTCUSDT").quantity, 0.0)

    def test_sell_credits_and_zeroes_ledger(self):
        self.broker.execute_buy("BTCUSDT", qty=0.01, price=50000.0, notional=500.0)
        bal_after_buy = self.broker.get_usdt_balance()
        res = self.broker.execute_sell(
            "BTCUSDT", qty=0.01, price=52000.0, entry_price=50000.0, entry_cost=500.0
        )
        self.assertTrue(res.success)
        gross = 0.01 * 52000.0
        self.assertAlmostEqual(
            self.broker.get_usdt_balance(), bal_after_buy + gross - res.fees, places=6
        )
        self.assertEqual(self.broker.get_ledger("BTCUSDT").quantity, 0.0)


class TestSimSellDisplayedPnlIncludesFees(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = make_engine()
        install_test_pair(self.engine, "BTCUSDT", execution_mode="live")
        self.engine.db = LiteDB(os.path.join(self.tmp.name, "test.db"))
        self.engine.read_only = False
        self.engine.live_trading_allowed = False
        self.events = []
        self.engine._emit_event = lambda event_type, **payload: self.events.append((event_type, payload))

    def tearDown(self):
        self.tmp.cleanup()

    def _save_bought_state(self, symbol="BTCUSDT"):
        self.engine.db.save_trade_state(
            symbol=symbol,
            state="bought",
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=0.0,
            highest_price_seen=100.0,
            quantity=1.0,
            opened_at="2026-06-11T00:00:00",
            last_transition_at="2026-06-11T00:00:00",
            stop_loss_order_id=None,
        )
        return self.engine.db.get_trade_state(symbol)

    def _closed_event_payload(self):
        for event_type, payload in reversed(self.events):
            if str(event_type).endswith("position_closed") or str(event_type).endswith("POSITION_CLOSED"):
                return payload
        self.fail("POSITION_CLOSED event was not emitted")

    def test_sim_broker_sell_event_matches_db_net_pnl_after_entry_and_exit_fees(self):
        balance_file = os.path.join(self.tmp.name, "sim_balance.json")
        broker = SimBroker(balance_file=balance_file, initial_balance=1000.0, default_fee_pct=0.001)
        broker.execute_buy("BTCUSDT", qty=1.0, price=100.0, notional=100.0)
        self.engine._sim_broker = broker
        self.engine.simulate_only = False
        self.engine._use_sim_broker = lambda symbol=None: True
        self.engine._broker_for_symbol = lambda symbol=None: broker

        ok = self.engine.execute_sell(
            self._save_bought_state(),
            ticker_price=110.0,
            trigger_reason="test sim broker exit",
            symbol="BTCUSDT",
        )

        self.assertTrue(ok)
        trade = self.engine.db.get_closed_trades("BTCUSDT", limit=1)[0]
        event = self._closed_event_payload()
        self.assertAlmostEqual(trade["net_pnl"], 9.79, places=6)
        self.assertEqual(event["pnl"], round(trade["net_pnl"], 2))
        self.assertIn("PnL: +9.79 USDT", self.engine.last_log_message)

    def test_legacy_sim_sell_event_matches_db_net_pnl_after_entry_and_exit_fees(self):
        self.engine.simulate_only = True
        self.engine._use_sim_broker = lambda symbol=None: False
        self.engine._symbol_fee_pct = lambda symbol: 0.001

        ok = self.engine.execute_sell(
            self._save_bought_state(),
            ticker_price=110.0,
            trigger_reason="test legacy sim exit",
            symbol="BTCUSDT",
        )

        self.assertTrue(ok)
        trade = self.engine.db.get_closed_trades("BTCUSDT", limit=1)[0]
        event = self._closed_event_payload()
        self.assertAlmostEqual(trade["net_pnl"], 9.79, places=6)
        self.assertEqual(event["pnl"], round(trade["net_pnl"], 2))
        self.assertIn("PnL: +9.79 USDT", self.engine.last_log_message)

    def test_live_sell_event_matches_db_net_pnl_after_entry_and_exit_fees(self):
        self.engine.simulate_only = False
        self.engine.live_trading_allowed = True
        self.engine.read_only = False
        self.engine._use_sim_broker = lambda symbol=None: False
        self.engine._symbol_fee_pct = lambda symbol: 0.001
        self.engine.config.setdefault("execution", {})["order_timeout_seconds"] = 0.0
        self.engine.client.get_balances = MagicMock(
            return_value={"BTC": {"available": 1.0, "reserved": 0.0}}
        )
        self.engine.client.get_symbol_filters = MagicMock(
            return_value={"minQty": 0.0001, "stepSize": 0.0001}
        )
        self.engine.client.place_order = MagicMock(
            return_value={
                "orderId": "sell-1",
                "status": "FILLED",
                "executedQty": "1.0",
                "cummulativeQuoteQty": "110.0",
            }
        )

        ok = self.engine.execute_sell(
            self._save_bought_state(),
            ticker_price=110.0,
            trigger_reason="test live exit",
            symbol="BTCUSDT",
        )

        self.assertTrue(ok)
        trade = self.engine.db.get_closed_trades("BTCUSDT", limit=1)[0]
        event = self._closed_event_payload()
        self.assertAlmostEqual(trade["entry_fee"], 0.1, places=6)
        self.assertAlmostEqual(trade["exit_fee"], 0.11, places=6)
        self.assertAlmostEqual(trade["net_pnl"], 9.79, places=6)
        self.assertEqual(event["pnl"], round(trade["net_pnl"], 2))
        self.assertIn("PnL: +9.79 USDT", self.engine.last_log_message)

    def test_live_partial_sell_records_partial_net_pnl_after_entry_and_exit_fees(self):
        self.engine.simulate_only = False
        self.engine.live_trading_allowed = True
        self.engine.read_only = False
        self.engine._use_sim_broker = lambda symbol=None: False
        self.engine._symbol_fee_pct = lambda symbol: 0.001
        self.engine.client.get_balances = MagicMock(
            return_value={"BTC": {"available": 1.0, "reserved": 0.0}}
        )
        self.engine.client.get_symbol_filters = MagicMock(
            return_value={"minQty": 0.0001, "stepSize": 0.0001}
        )
        self.engine.client.place_order = MagicMock(
            return_value={
                "orderId": "sell-1",
                "status": "PARTIALLY_FILLED",
                "executedQty": "0.4",
                "cummulativeQuoteQty": "44.0",
            }
        )
        self.engine.client.cancel_order = MagicMock()
        self.engine.client.get_order = MagicMock(
            return_value={
                "orderId": "sell-1",
                "status": "CANCELED",
                "executedQty": "0.4",
                "cummulativeQuoteQty": "44.0",
            }
        )

        with patch.object(self.engine, "_place_sl_with_retry", return_value=("sl-2", 0.6)):
            ok = self.engine.execute_sell(
                self._save_bought_state(),
                ticker_price=110.0,
                trigger_reason="test live partial exit",
                symbol="BTCUSDT",
            )

        self.assertTrue(ok)
        state = self.engine.db.get_trade_state("BTCUSDT")
        self.assertEqual(state.state, "bought")
        self.assertAlmostEqual(state.quantity, 0.6)
        self.assertEqual(state.stop_loss_order_id, "sl-2")
        trade = self.engine.db.get_closed_trades("BTCUSDT", limit=1)[0]
        self.assertAlmostEqual(trade["entry_fee"], 0.04, places=6)
        self.assertAlmostEqual(trade["exit_fee"], 0.044, places=6)
        self.assertAlmostEqual(trade["net_pnl"], 3.916, places=6)
        self.assertIn("PnL: +3.92 USDT", self.engine.last_log_message)

    def test_live_sell_clears_untradeable_remainder_after_partial_fill(self):
        self.engine.simulate_only = False
        self.engine.live_trading_allowed = True
        self.engine.read_only = False
        self.engine._use_sim_broker = lambda symbol=None: False
        self.engine._symbol_fee_pct = lambda symbol: 0.001
        self.engine.db.save_trade_state(
            symbol="SOLUSDT",
            state="bought",
            entry_price=69.50,
            stop_loss=70.10,
            take_profit=70.89,
            highest_price_seen=70.87,
            quantity=0.681,
            opened_at="2026-06-26T13:45:00",
            last_transition_at="2026-06-26T13:45:00",
            stop_loss_order_id=None,
        )
        self.engine.client.get_balances = MagicMock(
            return_value={"SOL": {"available": 0.680319, "reserved": 0.0}}
        )
        self.engine.client.get_symbol_filters = MagicMock(
            return_value={
                "minQty": 0.001,
                "stepSize": 0.001,
                "minNotional": 5.0,
            }
        )
        self.engine.client.place_order = MagicMock(
            return_value={
                "orderId": "sell-sol-1",
                "status": "FILLED",
                "executedQty": "0.680",
                "cummulativeQuoteQty": "48.314",
            }
        )

        with patch.object(self.engine, "_place_sl_with_retry") as place_sl:
            ok = self.engine.execute_sell(
                self.engine.db.get_trade_state("SOLUSDT"),
                ticker_price=71.05,
                trigger_reason="Fixed TP reached",
                symbol="SOLUSDT",
            )

        self.assertTrue(ok)
        place_sl.assert_not_called()
        state = self.engine.db.get_trade_state("SOLUSDT")
        self.assertEqual(state.state, "idle")
        self.assertAlmostEqual(state.quantity, 0.0)
        trade = self.engine.db.get_closed_trades("SOLUSDT", limit=1)[0]
        self.assertAlmostEqual(trade["amount"], 0.680, places=6)
        self.assertIn("[LIVE SELL FILLED]", self.engine.last_log_message)

    def test_ensure_sl_protected_clears_untradeable_live_dust_state(self):
        self.engine.simulate_only = False
        self.engine.live_trading_allowed = True
        self.engine.read_only = False
        self.engine._execution_mode = lambda symbol=None: "live"
        self.engine.db.save_trade_state(
            symbol="SOLUSDT",
            state="bought",
            entry_price=69.50,
            stop_loss=70.10,
            take_profit=70.89,
            highest_price_seen=70.87,
            quantity=0.001,
            opened_at="2026-06-26T13:45:00",
            last_transition_at="2026-06-26T13:45:00",
            stop_loss_order_id=None,
        )
        self.engine.client.get_balances = MagicMock(
            return_value={"SOL": {"available": 0.000319, "reserved": 0.0}}
        )
        self.engine.client.get_symbol_filters = MagicMock(
            return_value={
                "minQty": 0.001,
                "stepSize": 0.001,
                "minNotional": 5.0,
            }
        )

        with patch.object(self.engine, "_place_sl_with_retry") as place_sl:
            state = self.engine._ensure_sl_protected(
                self.engine.db.get_trade_state("SOLUSDT"),
                symbol="SOLUSDT",
            )

        place_sl.assert_not_called()
        self.assertEqual(state.state, "idle")
        self.assertAlmostEqual(state.quantity, 0.0)


class TestSimBalanceDelegatesToBroker(unittest.TestCase):
    """Legacy {"balance"} schema must not clobber SimBroker's
    {"USDT", "ledgers"} schema when the broker is enabled."""

    def setUp(self):
        self.engine = make_engine()
        self.tmp = tempfile.TemporaryDirectory()
        balance_file = os.path.join(self.tmp.name, "sim_balance.json")
        self.engine._sim_broker = SimBroker(balance_file=balance_file, initial_balance=777.0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_get_and_save_route_through_broker(self):
        self.assertTrue(
            bool((self.engine.config.get("architecture") or {}).get("sim_broker_enabled")),
            "bot_config.yaml is expected to enable the sim broker",
        )
        self.assertAlmostEqual(self.engine.get_simulated_balance(), 777.0, places=6)
        self.engine.save_simulated_balance(555.0)
        self.assertAlmostEqual(self.engine._sim_broker.get_usdt_balance(), 555.0, places=6)
        self.assertAlmostEqual(self.engine.get_simulated_balance(), 555.0, places=6)


if __name__ == "__main__":
    unittest.main()
