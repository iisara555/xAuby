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

    def get_balances(self):
        return {"USDT": {"available": 1000.0, "reserved": 0.0}}

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


if __name__ == "__main__":
    unittest.main()
