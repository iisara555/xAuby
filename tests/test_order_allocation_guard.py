import unittest
from types import SimpleNamespace

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


class _ShortDB:
    def __init__(self):
        self.saved_state = None

    def save_trade_state(self, **kwargs):
        self.saved_state = kwargs


class _ShortBroker:
    def __init__(self):
        self.notional = None

    def execute_open(self, symbol, side, qty, price, notional, leverage):
        self.notional = notional
        return SimpleNamespace(success=True, price=price, qty=qty, error="")


class _ShortEngine(OrderMixin):
    def __init__(self):
        self.config = {
            "portfolio": {
                "position_sizing": {
                    "risk_pct": 0.02,
                    "max_position_per_trade_pct": 95.0,
                    "min_order_amount": 10.0,
                },
                "symbols": {
                    "XAUUSDT": {
                        "allocation_pct": 65.0,
                        "position_sizing": {
                            "risk_pct": 0.01,
                            "max_position_per_trade_pct": 95.0,
                            "min_order_amount": 10.0,
                        },
                    },
                },
            },
            "strategy": {
                "active": "cdc_action_zone",
                "config": {
                    "cdc_action_zone": {
                        "disable_stop_loss": True,
                        "position_pct": 0.95,
                    },
                },
            },
            "risk": {"take_profit_pct": 0.0},
        }
        spec = SimpleNamespace(
            allowed_sides=("long", "short"),
            manual_allowed_sides=("long", "short"),
            short_live_enabled=True,
            manual_short_live_enabled=True,
            leverage=1.0,
        )
        self._pair_registry = SimpleNamespace(get=lambda symbol: spec)
        self.db = _ShortDB()
        self.broker = _ShortBroker()
        self.events = []

    def _sym(self):
        return "XAUUSDT"

    def _sc(self, symbol):
        return SimpleNamespace(feed_snapshot=lambda: {
            "trading_halted": False,
            "halt_reason": "",
            "candle_stale": False,
            "feed_degraded": False,
        })

    def _execution_mode(self, symbol):
        return "sim"

    def _strategy_name_for_symbol(self, symbol):
        return "cdc_action_zone"

    def _use_sim_broker(self, symbol):
        return False

    def _broker_for_symbol(self, symbol):
        return self.broker

    def get_equity(self, ticker_price=None, symbol=None):
        return 100.0

    def _emit_event(self, event_type, **payload):
        self.events.append((event_type, payload))

    def send_telegram_alert(self, *args, **kwargs):
        return None


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
    def test_cdc_short_is_capped_by_pair_allocation(self):
        engine = _ShortEngine()
        signal = SimpleNamespace(stop_loss_distance=0.0, stop_loss_price=0.0)

        ok = engine.execute_open_short(
            signal, ticker_price=100.0, symbol="XAUUSDT", manual=True
        )

        self.assertTrue(ok)
        self.assertAlmostEqual(engine.broker.notional, 65.0)
        self.assertAlmostEqual(engine.db.saved_state["quantity"], 0.65)

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
