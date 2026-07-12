"""Live/engine-side partial take-profit: order path, state flag, loop gating.

Complements tests/test_partial_tp.py (backtest simulator side). The engine
path must: close `fraction` via the broker abstraction, record ONE partial
closed trade with side-aware PnL, persist the reduced quantity plus the
one-shot `partial_tp_taken` flag, and never fire twice or beat a full exit.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock

from xauby.database.db import LiteDB
from xauby.engine.brokers.base import FillResult
from xauby.engine.brokers.sim_broker import SimBroker
from xauby.engine.loop import LoopMixin
from xauby.engine.orders import OrderMixin


class _FakeBroker:
    def __init__(self, fill_price=None, fill_qty=None, fees=0.0, fail=False):
        self.fill_price = fill_price
        self.fill_qty = fill_qty
        self.fees = fees
        self.fail = fail
        self.calls = []

    def execute_close(self, symbol, position_side, qty, price, entry_price, entry_cost, funding_paid=0.0):
        self.calls.append(
            {"symbol": symbol, "side": position_side, "qty": qty, "price": price,
             "funding": funding_paid}
        )
        if self.fail:
            return FillResult(False, error="broker down")
        return FillResult(True, qty=self.fill_qty or qty, price=self.fill_price or price, fees=self.fees)


class _Engine(OrderMixin):
    """OrderMixin with only the collaborators execute_partial_tp touches."""

    def __init__(self, db, broker):
        self.db = db
        self.read_only = False
        self.simulate_only = False
        self.live_trading_allowed = True
        self.client = MagicMock()
        self.config = {}
        self.last_log_message = ""
        self.events = []
        self.alerts = []
        self._broker = broker

    def _sym(self, symbol=None):
        return "XAUUSDT"

    def _symbol_filters_cached(self, symbol):
        return {"stepSize": 0.0001, "minQty": 0.0001, "minNotional": 1.0}

    def _use_sim_broker(self, symbol=None):
        return False

    def _sim_fill_price(self, symbol, price):
        return price

    def _broker_for_symbol(self, symbol=None):
        return self._broker

    def _symbol_fee_pct(self, symbol):
        return 0.001

    def _strategy_name_for_symbol(self, symbol):
        return "cdc_action_zone"

    def _execution_mode(self, symbol=None):
        return "live"

    def _get_base_asset(self, symbol):
        return "XAU"

    def _place_sl_with_retry(self, qty, stop, symbol=None):
        return None

    def _report_slippage(self, side, ref_price, fill_price, symbol=None, kind="order"):
        return 0.0

    def _emit_event(self, event_type, **payload):
        self.events.append((str(event_type), payload))

    def send_telegram_alert(self, msg, level=None):
        self.alerts.append(msg)


def _bought_state(db, *, side="LONG", qty=2.0, entry=2000.0, funding=4.0):
    db.save_trade_state(
        symbol="XAUUSDT", state="bought", entry_price=entry, quantity=qty,
        opened_at="2026-07-01T00:00:00", position_side=side,
        margin_mode="isolated", funding_paid=funding,
    )
    return db.get_trade_state("XAUUSDT")


class TestExecutePartialTp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = LiteDB(os.path.join(self.tmp.name, "t.db"))
        self.broker = _FakeBroker()
        self.eng = _Engine(self.db, self.broker)

    def tearDown(self):
        self.tmp.cleanup()

    def test_long_partial_banks_and_keeps_remainder(self):
        state = _bought_state(self.db, qty=2.0, entry=2000.0, funding=4.0)
        ok = self.eng.execute_partial_tp(
            state, 2240.0, fraction=0.5, threshold_pct=12.0, symbol="XAUUSDT"
        )
        self.assertTrue(ok)
        call = self.broker.calls[0]
        self.assertEqual((call["side"], call["qty"]), ("LONG", 1.0))
        # Recorded ONE partial trade: (2240-2000)*1 − fees(2+2.24) − funding share 2.0
        trade = self.db.get_closed_trades("XAUUSDT", limit=1)[0]
        self.assertEqual(trade["trigger"], "Partial TP +12.0% (banked 50%)")
        self.assertAlmostEqual(trade["net_pnl"], 240.0 - (2.0 + 2.24) - 2.0, places=6)
        st = self.db.get_trade_state("XAUUSDT")
        self.assertEqual(st.state, "bought")
        self.assertAlmostEqual(st.quantity, 1.0)
        self.assertTrue(st.partial_tp_taken)
        self.assertAlmostEqual(st.funding_paid, 2.0)
        self.assertTrue(any("PARTIAL TP" in a for a in self.eng.alerts))

    def test_short_partial_records_inverted_pnl(self):
        state = _bought_state(self.db, side="SHORT", qty=2.0, entry=2000.0, funding=0.0)
        ok = self.eng.execute_partial_tp(
            state, 1760.0, fraction=0.5, threshold_pct=12.0, symbol="XAUUSDT"
        )
        self.assertTrue(ok)
        self.assertEqual(self.broker.calls[0]["side"], "SHORT")
        trade = self.db.get_closed_trades("XAUUSDT", limit=1)[0]
        self.assertEqual(trade["side"], "SHORT")
        # (2000-1760)*1 − fees(2.0 + 1.76)
        self.assertAlmostEqual(trade["net_pnl"], 240.0 - 3.76, places=6)
        self.assertGreater(trade["net_pnl"], 0)
        st = self.db.get_trade_state("XAUUSDT")
        self.assertEqual(st.position_side, "SHORT")
        self.assertTrue(st.partial_tp_taken)

    def test_full_close_resets_partial_tp_flag(self):
        state = _bought_state(self.db, qty=2.0, entry=2000.0)
        self.assertTrue(
            self.eng.execute_partial_tp(
                state, 2240.0, fraction=0.5, threshold_pct=12.0, symbol="XAUUSDT"
            )
        )
        self.assertTrue(self.db.get_trade_state("XAUUSDT").partial_tp_taken)

        closed = self.db.close_position_atomic(
            "XAUUSDT",
            side="BUY",
            amount=1.0,
            entry_price=2000.0,
            exit_price=2100.0,
            entry_cost=2000.0,
            gross_exit=2100.0,
            net_pnl=90.0,
            net_pnl_pct=4.5,
            trigger="full close",
        )
        self.assertTrue(closed)
        st = self.db.get_trade_state("XAUUSDT")
        self.assertEqual(st.state, "idle")
        self.assertFalse(st.partial_tp_taken)

    def test_short_close_partial_fill_keeps_short_remainder(self):
        self.broker.fill_qty = 0.75
        state = _bought_state(self.db, side="SHORT", qty=2.0, entry=2000.0, funding=4.0)
        ok = self.eng.execute_close_short(
            state, 1900.0, trigger_reason="reverse close", symbol="XAUUSDT"
        )
        self.assertTrue(ok)
        st = self.db.get_trade_state("XAUUSDT")
        self.assertEqual(st.state, "bought")
        self.assertEqual(st.position_side, "SHORT")
        self.assertAlmostEqual(st.quantity, 1.25)
        trade = self.db.get_closed_trades("XAUUSDT", limit=1)[0]
        self.assertEqual(trade["side"], "SHORT")
        self.assertAlmostEqual(trade["amount"], 0.75)
        self.assertTrue(any(payload.get("partial") for _, payload in self.eng.events))

    def test_one_shot_flag_blocks_second_fire(self):
        state = _bought_state(self.db)
        self.assertTrue(self.eng.execute_partial_tp(
            state, 2240.0, fraction=0.5, threshold_pct=12.0, symbol="XAUUSDT"))
        refreshed = self.db.get_trade_state("XAUUSDT")
        self.assertFalse(self.eng.execute_partial_tp(
            refreshed, 2500.0, fraction=0.5, threshold_pct=12.0, symbol="XAUUSDT"))
        self.assertEqual(len(self.broker.calls), 1)

    def test_broker_failure_leaves_state_untouched(self):
        self.broker.fail = True
        state = _bought_state(self.db)
        ok = self.eng.execute_partial_tp(
            state, 2240.0, fraction=0.5, threshold_pct=12.0, symbol="XAUUSDT"
        )
        self.assertFalse(ok)
        st = self.db.get_trade_state("XAUUSDT")
        self.assertAlmostEqual(st.quantity, 2.0)
        self.assertFalse(st.partial_tp_taken)
        self.assertEqual(self.db.get_closed_trades("XAUUSDT", limit=5), [])

    def test_unsplittable_position_marks_taken_without_order(self):
        # Remainder would fall below the tradeable minimum -> skip but flag.
        state = _bought_state(self.db, qty=0.0004, entry=2000.0)
        ok = self.eng.execute_partial_tp(
            state, 2240.0, fraction=0.5, threshold_pct=12.0, symbol="XAUUSDT"
        )
        self.assertFalse(ok)
        self.assertEqual(self.broker.calls, [])
        st = self.db.get_trade_state("XAUUSDT")
        self.assertTrue(st.partial_tp_taken)
        self.assertAlmostEqual(st.quantity, 0.0004)

    def test_read_only_never_trades(self):
        self.eng.read_only = True
        state = _bought_state(self.db)
        self.assertFalse(self.eng.execute_partial_tp(
            state, 2500.0, fraction=0.5, threshold_pct=12.0, symbol="XAUUSDT"))
        self.assertEqual(self.broker.calls, [])


class _LoopStub(LoopMixin):
    def __init__(self, db):
        self.db = db
        self.calls = []
        self.result = True

    def execute_partial_tp(self, state, price, *, fraction, threshold_pct, symbol):
        self.calls.append((price, fraction, threshold_pct, symbol))
        return self.result


CONF = {"partial_tp_pct": 12.0, "partial_tp_fraction": 0.5}


class TestLoopGating(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = LiteDB(os.path.join(self.tmp.name, "t.db"))
        self.loop = _LoopStub(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fires_at_threshold_and_refreshes_state(self):
        state = _bought_state(self.db, qty=2.0, entry=2000.0)
        out = self.loop._maybe_take_partial_tp(state, "HOLD", 2240.0, CONF, symbol="XAUUSDT")
        self.assertEqual(len(self.loop.calls), 1)
        self.assertEqual(out.symbol, "XAUUSDT")  # refreshed from DB

    def test_below_threshold_holds(self):
        state = _bought_state(self.db)
        self.loop._maybe_take_partial_tp(state, "HOLD", 2100.0, CONF, symbol="XAUUSDT")
        self.assertEqual(self.loop.calls, [])

    def test_full_exit_wins_the_tick(self):
        state = _bought_state(self.db)
        self.loop._maybe_take_partial_tp(state, "SELL", 2500.0, CONF, symbol="XAUUSDT")
        self.assertEqual(self.loop.calls, [])

    def test_short_threshold_measured_downward(self):
        state = _bought_state(self.db, side="SHORT")
        self.loop._maybe_take_partial_tp(state, "HOLD", 1760.0, CONF, symbol="XAUUSDT")
        self.assertEqual(len(self.loop.calls), 1)

    def test_disabled_config_is_noop(self):
        state = _bought_state(self.db)
        self.loop._maybe_take_partial_tp(state, "HOLD", 2500.0, {}, symbol="XAUUSDT")
        self.assertEqual(self.loop.calls, [])


class TestSwapLongClose(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = LiteDB(os.path.join(self.tmp.name, "t.db"))
        self.broker = _FakeBroker(fill_price=2240.0, fees=2.24)
        self.eng = _Engine(self.db, self.broker)

    def tearDown(self):
        self.tmp.cleanup()

    def test_live_swap_long_sell_uses_reduce_only_broker_close(self):
        state = _bought_state(self.db, side="LONG", qty=1.0, entry=2000.0, funding=0.0)

        ok = self.eng.execute_sell(state, 2240.0, "4H zone turned RED", symbol="XAUUSDT")

        self.assertTrue(ok)
        self.assertEqual(self.broker.calls[0]["side"], "LONG")
        trade = self.db.get_closed_trades("XAUUSDT", limit=1)[0]
        self.assertEqual(trade["side"], "BUY")
        self.assertEqual(trade["trigger"], "4H zone turned RED")
        self.assertAlmostEqual(trade["exit_price"], 2240.0)
        self.assertEqual(self.db.get_trade_state("XAUUSDT").state, "idle")


class TestSimBrokerPartialShort(unittest.TestCase):
    def test_partial_then_full_close_conserves_margin(self):
        with tempfile.TemporaryDirectory() as d:
            broker = SimBroker(os.path.join(d, "bal.json"), initial_balance=1000.0,
                               default_fee_pct=0.0)
            res = broker.execute_open("XAUUSDT", "SHORT", 2.0, 100.0, 200.0, leverage=1.0)
            self.assertTrue(res.success)
            self.assertAlmostEqual(broker.get_usdt_balance(), 800.0)

            # Close half at 90: pnl +10, release half margin (100) -> +110
            res = broker.execute_close("XAUUSDT", "SHORT", 1.0, 90.0, 100.0, 100.0, 0.0)
            self.assertTrue(res.success)
            self.assertAlmostEqual(broker.get_usdt_balance(), 910.0)
            ledger = broker.get_ledger("XAUUSDT")
            self.assertAlmostEqual(ledger.quantity, 1.0)
            self.assertAlmostEqual(ledger.margin_reserved, 100.0)

            # Close the rest at 80: pnl +20, release remaining margin -> +120
            res = broker.execute_close("XAUUSDT", "SHORT", 1.0, 80.0, 100.0, 100.0, 0.0)
            self.assertTrue(res.success)
            self.assertAlmostEqual(broker.get_usdt_balance(), 1030.0)
            self.assertAlmostEqual(broker.get_ledger("XAUUSDT").quantity, 0.0)


if __name__ == "__main__":
    unittest.main()
