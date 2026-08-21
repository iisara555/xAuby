import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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


class _LiveShortClient:
    capabilities = {"swap": True, "positions": True, "reduce_only": True}

    def __init__(self, available_sequence):
        self.available_sequence = list(available_sequence)
        self.balance_calls = 0

    def set_margin_mode(self, symbol, mode):
        return None

    def set_leverage(self, symbol, leverage):
        return None

    def get_balances(self):
        self.balance_calls += 1
        if len(self.available_sequence) > 1:
            available = self.available_sequence.pop(0)
        else:
            available = self.available_sequence[0]
        return {"USDT": {"available": available, "reserved": 0.0}}


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
        self.last_log_message = ""

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

    def _quote_asset(self):
        return "USDT"

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
    def test_live_short_open_places_reduce_only_exchange_stop(self):
        engine = _ShortEngine()
        engine.client = _LiveShortClient([100.0])
        engine._execution_mode = lambda symbol: "live"
        engine.config["strategy"]["config"]["cdc_action_zone"].update(
            {"disable_stop_loss": False}
        )
        engine._place_sl_with_retry = MagicMock(return_value=("short-sl-1", 0.2))
        signal = SimpleNamespace(stop_loss_distance=5.0, stop_loss_price=105.0)

        effective = SimpleNamespace(
            strategy_name="cdc_action_zone",
            strategy={"disable_stop_loss": False},
            portfolio={
                "risk_pct": 0.01,
                "max_position_per_trade_pct": 95.0,
                "min_order_amount": 10.0,
            },
        )
        with patch("xauby.engine.orders.resolve_trading_config", return_value=effective):
            ok = engine.execute_open_short(signal, ticker_price=100.0, symbol="XAUUSDT")

        self.assertTrue(ok)
        engine._place_sl_with_retry.assert_called_once_with(
            0.2,
            105.0,
            symbol="XAUUSDT",
            position_side="SHORT",
        )
        self.assertEqual(engine.db.saved_state["stop_loss_order_id"], "short-sl-1")
        self.assertEqual(engine.db.saved_state["position_side"], "SHORT")
        self.assertEqual(engine.db.saved_state["lowest_price_seen"], 100.0)

    def test_reverse_short_waits_for_full_balance_before_opening(self):
        engine = _ShortEngine()
        engine.client = _LiveShortClient([30.0, 100.0])
        engine._execution_mode = lambda symbol: "live"
        engine.config["execution"] = {
            "reverse_balance_timeout_seconds": 0.1,
            "reverse_balance_poll_interval_seconds": 0.001,
        }
        signal = SimpleNamespace(stop_loss_distance=0.0, stop_loss_price=0.0)

        ok = engine.execute_open_short(
            signal,
            ticker_price=100.0,
            symbol="XAUUSDT",
            reverse_entry=True,
        )

        self.assertTrue(ok)
        self.assertEqual(engine.client.balance_calls, 2)
        self.assertAlmostEqual(engine.broker.notional, 65.0)
        event_names = [event for event, _payload in engine.events]
        self.assertEqual(
            [
                event for event in event_names
                if event in {
                    "risk_check_passed", "order_submitted", "order_filled",
                    "position_opened",
                }
            ],
            ["risk_check_passed", "order_submitted", "order_filled", "position_opened"],
        )
        filled = next(payload for event, payload in engine.events if event == "order_filled")
        self.assertEqual(filled["side"], "SELL")
        self.assertEqual(filled["position_side"], "SHORT")
        self.assertTrue(filled["reverse_entry"])

    def test_reverse_short_timeout_does_not_submit_partial_order(self):
        engine = _ShortEngine()
        engine.client = _LiveShortClient([30.0])
        engine._execution_mode = lambda symbol: "live"
        engine.config["execution"] = {
            "reverse_balance_timeout_seconds": 0.0,
            "reverse_balance_poll_interval_seconds": 0.001,
        }
        signal = SimpleNamespace(stop_loss_distance=0.0, stop_loss_price=0.0)

        ok = engine.execute_open_short(
            signal,
            ticker_price=100.0,
            symbol="XAUUSDT",
            reverse_entry=True,
        )

        self.assertFalse(ok)
        self.assertIsNone(engine.broker.notional)
        self.assertTrue(any(event == "reverse_open_deferred" for event, _ in engine.events))

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
