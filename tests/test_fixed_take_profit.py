import unittest
from threading import Lock
from unittest.mock import patch

from xauby.engine.loop import LoopMixin
from xauby.engine.orders import OrderMixin


class _TPDB:
    def __init__(self):
        self.state = {"symbol": "BTCUSDT", "state": "idle", "quantity": 0.0}
        self.saved = None

    def get_trade_state(self, symbol):
        return dict(self.state)

    def save_trade_state(self, **kwargs):
        self.saved = dict(kwargs)
        self.state = dict(kwargs)

    def get_closed_trades(self, symbol=None, limit=1):
        return []


class _TPEngine(OrderMixin, LoopMixin):
    def __init__(self, *, strategy_name="btc_ema_pullback", strategy_cfg=None):
        self.config = {
            "architecture": {"whitelist_strict": False},
            "strategy": {
                "active": strategy_name,
                "config": {
                    strategy_name: {
                        "sl_atr_mult": 2.0,
                        **(strategy_cfg or {}),
                    }
                },
            },
            "portfolio": {
                "position_sizing": {
                    "risk_pct": 0.02,
                    "max_position_per_trade_pct": 100.0,
                    "min_order_amount": 10.0,
                }
            },
        }
        self.db = _TPDB()
        self.simulate_only = True
        self.read_only = False
        self.last_log_message = ""
        self.events = []
        self.alerts = []
        self.sim_balance = 10000.0
        self._sim_balance_lock = Lock()

    def _sym(self):
        return "BTCUSDT"

    def _strategy_name_for_symbol(self, symbol):
        return (self.config.get("strategy") or {}).get("active", "btc_ema_pullback")

    def _use_sim_broker(self, symbol):
        return False

    def _get_base_asset(self, symbol):
        return symbol[:-4] if symbol.endswith("USDT") else symbol

    def _quote_asset(self):
        return "USDT"

    def get_equity(self, ticker_price=None, symbol=None):
        return 1000.0

    def check_daily_protections(self, equity, symbol=None):
        return True, ""

    def check_drawdown_guard(self, equity):
        return True, ""

    def get_simulated_balance(self):
        return self.sim_balance

    def save_simulated_balance(self, balance):
        self.sim_balance = balance

    def _symbol_fee_pct(self, symbol):
        return 0.0

    def _emit_event(self, event_type, **payload):
        self.events.append((event_type, payload))

    def send_telegram_alert(self, message, *args, **kwargs):
        self.alerts.append(message)


class TestFixedTakeProfit(unittest.TestCase):
    def setUp(self):
        # These tests drive execute_buy with a fully synthetic config. Isolate
        # them from the repo's real coin_whitelist.json, whose live BTCUSDT entry
        # would otherwise overlay its strategy_params (e.g. donchian sl_atr_mult /
        # fixed_tp_pct) via the legacy pair merge in strategy_config_block.
        p = patch(
            "xauby.runtime.trading_config.merge_strategy_config",
            side_effect=lambda merged, *a, **k: merged,
        )
        p.start()
        self.addCleanup(p.stop)

    def test_buy_sets_default_fixed_tp_for_pullback_strategy(self):
        engine = _TPEngine(strategy_name="btc_ema_pullback")

        ok = engine.execute_buy(ticker_price=100.0, atr=1.0, symbol="BTCUSDT")

        self.assertTrue(ok)
        self.assertAlmostEqual(engine.db.saved["take_profit"], 101.5)

    def test_config_can_disable_default_fixed_tp(self):
        engine = _TPEngine(
            strategy_name="btc_ema_pullback",
            strategy_cfg={"fixed_tp_pct": 0.0},
        )

        ok = engine.execute_buy(ticker_price=100.0, atr=1.0, symbol="BTCUSDT")

        self.assertTrue(ok)
        self.assertEqual(engine.db.saved["take_profit"], 0.0)

    def test_config_can_enable_fixed_tp_for_cdc_symbol(self):
        engine = _TPEngine(
            strategy_name="cdc_action_zone",
            strategy_cfg={"fixed_tp_pct": 3.0},
        )

        ok = engine.execute_buy(ticker_price=100.0, atr=1.0, symbol="BTCUSDT")

        self.assertTrue(ok)
        self.assertAlmostEqual(engine.db.saved["take_profit"], 103.0)

    def test_fixed_tp_exit_turns_hold_into_sell(self):
        engine = _TPEngine()

        action, reason = engine._apply_fixed_tp_exit(
            {"state": "bought", "take_profit": 101.5},
            "HOLD",
            "waiting",
            101.5,
            sl_confirmed=False,
        )

        self.assertEqual(action, "SELL")
        self.assertIn("Fixed TP reached", reason)

    def test_fixed_tp_exit_does_not_override_sl_sell(self):
        engine = _TPEngine()

        action, reason = engine._apply_fixed_tp_exit(
            {"state": "bought", "take_profit": 101.5},
            "HOLD",
            "waiting",
            102.0,
            sl_confirmed=True,
        )

        self.assertEqual(action, "HOLD")
        self.assertEqual(reason, "waiting")

    def test_existing_strategy_sell_is_not_overwritten_by_fixed_tp(self):
        engine = _TPEngine()

        action, reason = engine._apply_fixed_tp_exit(
            {"state": "bought", "take_profit": 101.5},
            "SELL",
            "Strategy exit",
            102.0,
            sl_confirmed=False,
        )

        self.assertEqual(action, "SELL")
        self.assertEqual(reason, "Strategy exit")


if __name__ == "__main__":
    unittest.main()
