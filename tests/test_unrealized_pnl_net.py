import time
import unittest
from types import SimpleNamespace

from xauby.engine.loop import LoopMixin, estimate_net_unrealized_pnl


class EstimateNetUnrealizedPnlTest(unittest.TestCase):
    def test_long_unrealized_pnl_is_net_of_entry_exit_fees_and_funding(self):
        state = {
            "state": "bought",
            "entry_price": 3350.0,
            "quantity": 1.0,
            "position_side": "LONG",
            "funding_paid": 0.0,
        }

        result = estimate_net_unrealized_pnl(state, mark_price=3351.0, fee_pct=0.0005)

        self.assertAlmostEqual(result["gross_pnl"], 1.0)
        self.assertAlmostEqual(result["entry_fee"], 1.675)
        self.assertAlmostEqual(result["exit_fee"], 1.6755)
        self.assertAlmostEqual(result["net_pnl"], -2.3505)
        self.assertAlmostEqual(result["net_pnl_pct"], (-2.3505 / 3350.0) * 100.0)

    def test_short_unrealized_pnl_subtracts_fees_and_applies_funding_sign(self):
        state = {
            "state": "bought",
            "entry_price": 100.0,
            "quantity": 2.0,
            "position_side": "SHORT",
            "funding_paid": -0.5,
        }

        result = estimate_net_unrealized_pnl(state, mark_price=90.0, fee_pct=0.001)

        self.assertAlmostEqual(result["gross_pnl"], 20.0)
        self.assertAlmostEqual(result["entry_fee"], 0.2)
        self.assertAlmostEqual(result["exit_fee"], 0.18)
        self.assertAlmostEqual(result["net_pnl"], 20.12)
        self.assertAlmostEqual(result["net_pnl_pct"], (20.12 / 200.0) * 100.0)


class _StateExporter:
    def build(self, **payload):
        return payload


class _Client:
    last_request = {}
    last_latency = 0.0

    def get_balances(self):
        return {
            "USDT": {"available": 1000.0, "reserved": 0.0},
            "XAU": {"available": 0.0, "reserved": 0.0},
        }


class _Context:
    primary_timeframe = "4h"
    confirm_timeframe = "1d"
    current_regime = None
    degraded = False
    strategy_name = "cdc_action_zone"
    last_signal_meta = {}

    def get_tick_snapshot(self):
        return {
            "last": 3351.0,
            "bid": 3350.9,
            "ask": 3351.1,
            "percent_change_24h": 0.0,
            "monotonic_ts": time.monotonic(),
        }

    def feed_snapshot(self):
        return {"feed_degraded": False, "degrade_reason": ""}

    def candle_snapshot(self):
        return {"last_candle_timestamp": 0}


class _Registry:
    def get(self, symbol):
        return SimpleNamespace(regime_router_enabled=False, regime_router_live_confirmed=False)


class _LoopHarness(LoopMixin):
    def __init__(self):
        self.config = {"exchange": {"name": "okx", "market_type": "swap"}, "trading": {}}
        self.client = _Client()
        self._latency_metrics = {}
        self._exchange_maker_fee_pct_by_symbol = {}
        self._exchange_fee_pct_by_symbol = {}
        self._state_exporter = _StateExporter()
        self._pair_registry = _Registry()
        self._ctx = _Context()
        self.engine_started_at = "2026-07-08T00:00:00"
        self.simulate_only = False
        self.read_only = False
        self.run_id = "test-run"
        self.last_log_message = ""

    def _sym(self):
        return "XAUUSDT"

    def _sc(self, symbol):
        return self._ctx

    def _get_base_asset(self, symbol):
        return "XAU"

    def _quote_asset(self):
        return "USDT"

    def _use_sim_broker(self, symbol):
        return False

    def _balance_cache_snapshot(self):
        return {}, 0.0

    def get_symbol_equity_breakdown(self, symbol):
        return {"portfolio_total_usdt": 1000.0}

    def _symbol_fee_pct(self, symbol):
        return 0.0005

    def _get_strategy_config(self, symbol):
        return {}

    def _get_strategy_for_symbol(self, symbol):
        return SimpleNamespace(name="cdc_action_zone", version="test")

    def _build_risk_block(self, symbol):
        return {}

    def _build_macro_guard_for_symbol(self, symbol):
        return {}

    def get_recent_events(self, limit, symbol=None):
        return []

    def _execution_mode(self, symbol):
        return "live"


class StateExportUnrealizedPnlTest(unittest.TestCase):
    def test_state_export_uses_net_unrealized_pnl_breakdown(self):
        state = {
            "state": "bought",
            "entry_price": 3350.0,
            "stop_loss": 0.0,
            "take_profit": 0.0,
            "highest_price_seen": 3350.0,
            "quantity": 1.0,
            "opened_at": "2026-07-08T00:00:00",
            "position_side": "LONG",
            "leverage": 1.0,
            "margin_mode": "isolated",
            "liquidation_price": 0.0,
            "funding_paid": 0.0,
            "management_mode": "strategy",
        }

        snap = _LoopHarness()._build_state_dict(state, {}, symbol="XAUUSDT")
        pos = snap["position"]

        self.assertAlmostEqual(pos["unrealized_pnl_gross"], 1.0)
        self.assertAlmostEqual(pos["estimated_total_fees"], 3.3505)
        self.assertAlmostEqual(pos["unrealized_pnl"], -2.3505)
        self.assertAlmostEqual(pos["unrealized_pnl_pct"], (-2.3505 / 3350.0) * 100.0)


if __name__ == "__main__":
    unittest.main()
