"""Entry market fallback: an unfilled LIMIT entry should top up the remainder
with a MARKET order (when enabled) instead of cancelling and skipping the trade,
keeping live entries in parity with the market-fill backtest."""

import unittest

from xauby.engine.orders import OrderMixin


class _IdleDB:
    def __init__(self):
        self.saved_state = None

    def get_trade_state(self, symbol):
        return {"symbol": symbol, "state": "idle", "quantity": 0.0}

    def save_trade_state(self, **kwargs):
        self.saved_state = kwargs


class _FallbackClient:
    """LIMIT entry never fills; the MARKET fallback fills the full remainder."""

    def __init__(self, *, fill_limit=False):
        self.fill_limit = fill_limit
        self.orders = []  # (order_type, side, amount)
        self.cancelled = []
        self.balance_calls = 0
        self.filter_calls = 0

    def get_balances(self):
        self.balance_calls += 1
        return {"USDT": {"available": 1000.0, "reserved": 0.0}}

    def get_symbol_filters(self, symbol):
        self.filter_calls += 1
        return {"minQty": 0.001, "stepSize": 0.001, "minNotional": 10.0}

    def place_order(self, symbol, side, order_type, amount, price=None, client_id=None, **kwargs):
        self.orders.append((order_type.upper(), side.upper(), float(amount)))
        if order_type.upper() == "MARKET":
            # Quote-denominated buy: spend the whole remainder at ~price.
            px = price or 100.0
            return {
                "orderId": "mkt-1",
                "status": "FILLED",
                "executedQty": float(amount) / px,
                "cummulativeQuoteQty": float(amount),
                "price": px,
            }
        # LIMIT
        if self.fill_limit:
            return {
                "orderId": "lim-1",
                "status": "FILLED",
                "executedQty": float(amount) / (price or 100.0),
                "cummulativeQuoteQty": float(amount),
                "price": price,
            }
        return {"orderId": "lim-1", "status": "NEW", "executedQty": 0.0, "cummulativeQuoteQty": 0.0}

    def get_order(self, symbol, order_id):
        # After cancellation the LIMIT order shows no fill.
        return {"orderId": order_id, "status": "CANCELED", "executedQty": 0.0, "cummulativeQuoteQty": 0.0}

    def cancel_order(self, symbol, order_id):
        self.cancelled.append(order_id)


class _FallbackEngine(OrderMixin):
    def __init__(self, client, *, entry_market_fallback=True):
        self.config = {
            "execution": {
                "order_type": "limit",
                "order_timeout_seconds": 0,  # poll exits immediately → straight to cancel
                "entry_market_fallback": entry_market_fallback,
                "max_slippage_bps": 0.0,
            },
            "portfolio": {
                "position_sizing": {
                    "risk_pct": 0.02,
                    "max_position_per_trade_pct": 100.0,
                    "min_order_amount": 10.0,
                },
            },
            "strategy": {
                "active": "cdc_action_zone",
                "config": {
                    "cdc_action_zone": {
                        "sl_atr_mult": 2.0,
                        "disable_stop_loss": True,  # skip exchange SL placement
                        "position_pct": 0.5,
                        "fixed_tp_pct": 0.0,
                    }
                },
            },
        }
        self.client = client
        self.db = _IdleDB()
        self.simulate_only = False
        self.read_only = False
        self.last_log_message = ""
        self.events = []
        self._latency_metrics = {}

    def _sym(self):
        return "BTCUSDT"

    def _use_sim_broker(self, symbol):
        return False

    def _get_base_asset(self, symbol):
        return symbol[:-4] if symbol.endswith("USDT") else symbol

    def _quote_asset(self):
        return "USDT"

    def _strategy_name_for_symbol(self, symbol=None):
        return "cdc_action_zone"

    def get_equity(self, ticker_price=None, symbol=None):
        return 1000.0

    def check_daily_protections(self, equity, symbol=None):
        return True, ""

    def check_drawdown_guard(self, equity):
        return True, ""

    def _emit_event(self, event_type, **payload):
        self.events.append((event_type, payload))

    def send_telegram_alert(self, *args, **kwargs):
        return None


class TestEntryMarketFallback(unittest.TestCase):
    def test_unfilled_limit_falls_back_to_market(self):
        client = _FallbackClient(fill_limit=False)
        engine = _FallbackEngine(client, entry_market_fallback=True)

        ok = engine.execute_buy(ticker_price=100.0, atr=1.0, symbol="BTCUSDT")

        self.assertTrue(ok)
        order_types = [o[0] for o in client.orders]
        self.assertEqual(order_types, ["LIMIT", "MARKET"])
        self.assertTrue(client.cancelled)  # the stale LIMIT was cancelled first
        # Position recorded from the market fill (fixed-fraction: 50% of 1000).
        self.assertIsNotNone(engine.db.saved_state)
        self.assertEqual(engine.db.saved_state["state"], "bought")
        self.assertAlmostEqual(engine.db.saved_state["quantity"], 5.0, places=4)

    def test_disabled_flag_keeps_legacy_cancel_behaviour(self):
        client = _FallbackClient(fill_limit=False)
        engine = _FallbackEngine(client, entry_market_fallback=False)

        ok = engine.execute_buy(ticker_price=100.0, atr=1.0, symbol="BTCUSDT")

        self.assertFalse(ok)
        order_types = [o[0] for o in client.orders]
        self.assertEqual(order_types, ["LIMIT"])  # no market fallback
        self.assertTrue(client.cancelled)
        self.assertIsNone(engine.db.saved_state)

    def test_limit_fill_skips_fallback(self):
        client = _FallbackClient(fill_limit=True)
        engine = _FallbackEngine(client, entry_market_fallback=True)

        ok = engine.execute_buy(ticker_price=100.0, atr=1.0, symbol="BTCUSDT")

        self.assertTrue(ok)
        order_types = [o[0] for o in client.orders]
        self.assertEqual(order_types, ["LIMIT"])  # filled outright, no fallback
        self.assertFalse(client.cancelled)

    def test_fast_entry_uses_market_order_and_records_latency(self):
        client = _FallbackClient(fill_limit=False)
        engine = _FallbackEngine(client, entry_market_fallback=True)
        engine.config["execution"].update(
            {
                "fast_entry_enabled": True,
                "fast_entry_order_type": "MARKET",
            }
        )

        ok = engine.execute_buy(ticker_price=100.0, atr=1.0, symbol="BTCUSDT")

        self.assertTrue(ok)
        self.assertEqual([o[0] for o in client.orders], ["MARKET"])
        self.assertIn("order_total_ms", engine._latency_metrics)
        filled_events = [payload for event, payload in engine.events if str(event).endswith("order_filled")]
        self.assertTrue(filled_events)
        self.assertEqual(filled_events[-1]["order_path"], "market_fast")
        self.assertIn("order_ack_ms", filled_events[-1])

    def test_fast_limit_uses_fast_timeout(self):
        client = _FallbackClient(fill_limit=False)
        engine = _FallbackEngine(client, entry_market_fallback=False)
        engine.config["execution"].update(
            {
                "fast_entry_enabled": True,
                "fast_entry_order_type": "LIMIT",
                "order_timeout_seconds": 99.0,
                "limit_timeout_fast_seconds": 0.0,
            }
        )

        ok = engine.execute_buy(ticker_price=100.0, atr=1.0, symbol="BTCUSDT")

        self.assertFalse(ok)
        self.assertEqual([o[0] for o in client.orders], ["LIMIT"])
        self.assertTrue(client.cancelled)

    def test_live_buy_uses_fresh_balance_cache_on_hot_path(self):
        client = _FallbackClient(fill_limit=True)
        engine = _FallbackEngine(client, entry_market_fallback=True)
        engine._balance_cache = {"USDT": {"available": 1000.0, "reserved": 0.0}}
        engine._balance_last_update = 100.0
        engine._balance_cache_ttl = 30.0
        engine._balance_cache_snapshot = lambda: (engine._balance_cache, 1.0)

        ok = engine.execute_buy(ticker_price=100.0, atr=1.0, symbol="BTCUSDT")

        self.assertTrue(ok)
        self.assertEqual(client.balance_calls, 0)

    def test_symbol_filters_are_cached_for_order_helpers(self):
        client = _FallbackClient(fill_limit=True)
        engine = _FallbackEngine(client, entry_market_fallback=True)

        first = engine._min_tradeable_base_qty("BTCUSDT", 100.0)
        second = engine._min_tradeable_base_qty("BTCUSDT", 100.0)

        self.assertAlmostEqual(first, second)
        self.assertEqual(client.filter_calls, 1)


if __name__ == "__main__":
    unittest.main()
