import unittest

from xauby.engine.orders import OrderMixin


class _IdleDB:
    def get_trade_state(self, symbol):
        return {"symbol": symbol, "state": "idle", "quantity": 0.0}


class _Client:
    def __init__(self, balances):
        self.balances = balances
        self.order_called = False

    def get_balances(self):
        return self.balances

    def place_order(self, *args, **kwargs):
        self.order_called = True
        return {}


class _OrderEngine(OrderMixin):
    def __init__(self, balances, *, allocation_pct=50.0):
        self.config = {
            "portfolio": {
                "position_sizing": {
                    "risk_pct": 0.02,
                    "max_position_per_trade_pct": 48.0,
                    "min_order_amount": 10.0,
                },
                "symbols": {
                    "BTCUSDT": {
                        "allocation_pct": allocation_pct,
                        "position_sizing": {
                            "risk_pct": 0.02,
                            "max_position_per_trade_pct": 48.0,
                            "min_order_amount": 10.0,
                        },
                    }
                },
            },
            "strategy": {
                "active": "cdc_action_zone",
                "config": {"cdc_action_zone": {"sl_atr_mult": 2.0}},
            },
        }
        self.client = _Client(balances)
        self.db = _IdleDB()
        self.simulate_only = False
        self.read_only = False
        self.last_log_message = ""
        self.events = []
        self.alerts = []

    def _sym(self):
        return "BTCUSDT"

    def _use_sim_broker(self, symbol):
        return False

    def _get_base_asset(self, symbol):
        return symbol[:-4] if symbol.endswith("USDT") else symbol

    def _quote_asset(self):
        return "USDT"

    def get_equity(self, ticker_price=None, symbol=None):
        return 100.0

    def check_daily_protections(self, equity, symbol=None):
        return True, ""

    def check_drawdown_guard(self, equity):
        return True, ""

    def _emit_event(self, event_type, **payload):
        self.events.append((event_type, payload))

    def send_telegram_alert(self, *args, **kwargs):
        self.alerts.append(args[0] if args else "")


class TestOrderAllocationGuard(unittest.TestCase):
    def test_live_buy_aborts_when_exchange_already_holds_base_asset(self):
        engine = _OrderEngine(
            {
                "USDT": {"available": 100.0, "reserved": 0.0},
                "BTC": {"available": 0.49, "reserved": 0.0},
            }
        )

        ok = engine.execute_buy(ticker_price=100.0, atr=1.0, symbol="BTCUSDT")

        self.assertFalse(ok)
        self.assertFalse(engine.client.order_called)
        self.assertIn("exchange already holds", engine.last_log_message)

    def test_live_buy_aborts_when_remaining_symbol_allocation_below_minimum(self):
        engine = _OrderEngine(
            {"USDT": {"available": 100.0, "reserved": 0.0}},
            allocation_pct=5.0,
        )

        ok = engine.execute_buy(ticker_price=100.0, atr=1.0, symbol="BTCUSDT")

        self.assertFalse(ok)
        self.assertFalse(engine.client.order_called)
        self.assertIn("remaining allocation", engine.last_log_message)

    def test_repeated_protection_block_alert_is_throttled(self):
        engine = _OrderEngine({"USDT": {"available": 100.0, "reserved": 0.0}})
        engine.check_daily_protections = lambda equity, symbol=None: (False, "daily loss limit")

        self.assertFalse(engine.execute_buy(ticker_price=100.0, atr=1.0, symbol="BTCUSDT"))
        self.assertFalse(engine.execute_buy(ticker_price=100.0, atr=1.0, symbol="BTCUSDT"))

        self.assertEqual(len(engine.alerts), 1)
        self.assertIn("daily loss limit", engine.alerts[0])


if __name__ == "__main__":
    unittest.main()
