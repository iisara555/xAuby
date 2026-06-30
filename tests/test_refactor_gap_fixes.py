"""Tests for the architecture-refactor gap remediation."""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest

import yaml

from xauby.engine.orders import OrderMixin
from xauby.engine.regime_router import RegimeRouter
from xauby.engine.symbol_context import SymbolContext
from xauby.observability.event_bus import EventBus
from xauby.observability.events import Event, EventType
from xauby.observability.routing_subscriber import RoutingEventRecorder
from xauby.regime.models import GoldRegime
from xauby.runtime.pair_registry import PairSpec
from xauby.ui import state_view
from xauby.ui.tradelog_presenter import _sim_live_split_lines


class _FakeOrders(OrderMixin):
    def __init__(self, order_type="LIMIT"):
        self.config = {"execution": {"order_type": order_type}}


class TestSellOrderType(unittest.TestCase):
    def test_no_trade_force_close_uses_market(self):
        o = _FakeOrders(order_type="LIMIT")
        self.assertEqual(
            o._resolve_sell_order_type("NO_TRADE force close (6+ candles)"), "MARKET"
        )

    def test_red_zone_uses_market(self):
        o = _FakeOrders(order_type="LIMIT")
        self.assertEqual(o._resolve_sell_order_type("CDC red zone exit"), "MARKET")

    def test_normal_stop_loss_uses_configured(self):
        o = _FakeOrders(order_type="LIMIT")
        self.assertEqual(o._resolve_sell_order_type("stop loss hit"), "LIMIT")


class TestMappingValidation(unittest.TestCase):
    def test_default_mapping_targets_are_registered(self):
        router = RegimeRouter({})
        self.assertEqual(router.validate_mapping(), [])

    def test_unknown_target_is_reported(self):
        cfg = {"regime_router": {"mapping": {"BULL_BREAKOUT": "does_not_exist"}}}
        router = RegimeRouter(cfg)
        self.assertIn("BULL_BREAKOUT->does_not_exist", router.validate_mapping())


class TestConfidenceBypass(unittest.TestCase):
    def _route(self, history_count):
        cfg = {
            "architecture": {"regime_confidence_filter": True},
            "regime_router": {
                "confidence_threshold": 0.7,
                "debounce_candles": 1,
                "mapping": {"BULL_BREAKOUT": "cdc_action_zone"},
            },
        }
        router = RegimeRouter(cfg, history_count_fn=lambda: history_count)
        sc = SymbolContext(symbol="BTCUSDT", strategy_name="bbkc_squeeze")
        spec = PairSpec(symbol="BTCUSDT", strategy_name="bbkc_squeeze")
        return router.evaluate(
            "BTCUSDT",
            GoldRegime(
                regime="BULL_BREAKOUT",
                trend="UP",
                volatility="LOW",
                macro_bias="NEUTRAL",
                confidence=0.50,
            ),
            sc,
            spec,
            has_open_position=False,
        )

    def test_low_confidence_bypassed_below_30_history(self):
        route = self._route(history_count=10)
        self.assertTrue(route.strategy_changed)
        self.assertEqual(route.strategy_name, "cdc_action_zone")

    def test_low_confidence_blocks_at_30_history(self):
        route = self._route(history_count=30)
        self.assertFalse(route.strategy_changed)


class TestLiveGateDetection(unittest.TestCase):
    def test_live_gate_blocked_true_when_unconfirmed_live(self):
        spec = PairSpec(
            symbol="BTCUSDT",
            execution_mode="live",
            regime_router_enabled=True,
            regime_router_live_confirmed=False,
        )
        self.assertTrue(RegimeRouter.live_gate_blocked(spec))

    def test_live_gate_blocked_false_when_confirmed(self):
        spec = PairSpec(
            symbol="BTCUSDT",
            execution_mode="live",
            regime_router_enabled=True,
            regime_router_live_confirmed=True,
        )
        self.assertFalse(RegimeRouter.live_gate_blocked(spec))


class TestRoutingSubscriber(unittest.TestCase):
    def test_recorder_receives_routing_events(self):
        bus = EventBus()
        recorder = RoutingEventRecorder().register(bus)
        bus.dispatch(EventType.REGIME_CHANGED, "BTCUSDT", old_regime="A", new_regime="B")
        bus.dispatch(EventType.STRATEGY_SWITCHED, "BTCUSDT", new_strategy="x")
        bus.dispatch(EventType.TICK, "BTCUSDT")  # non-routing, ignored
        rows = recorder.recent()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["event"], EventType.REGIME_CHANGED)

    def test_emitter_dispatches_to_recorder_when_enabled(self):
        from xauby.observability.emitter import EventEmitter

        with tempfile.TemporaryDirectory() as tmp:
            orig = os.getcwd()
            try:
                os.chdir(tmp)
                with open("bot_config.yaml", "w", encoding="utf-8") as f:
                    yaml.dump({"architecture": {"event_bus_enabled": True}}, f)
                from xauby.observability import event_bus as eb

                eb._BUS = EventBus()
                recorder = RoutingEventRecorder().register(eb._BUS)
                emitter = EventEmitter(symbol="BTCUSDT", mode="paper", run_id="test")
                emitter.emit(EventType.REGIME_CHANGED, symbol="BTCUSDT", new_regime="B")
                self.assertEqual(len(recorder.recent()), 1)
            finally:
                eb._BUS = None
                os.chdir(orig)

    def test_emitter_state_is_thread_safe(self):
        from xauby.observability.emitter import EventEmitter

        class FakeStore:
            def __init__(self):
                self._lock = threading.Lock()
                self._seq = 0
                self.run_id = ""

            def set_run_id(self, run_id):
                with self._lock:
                    self.run_id = run_id

            def append(self, event_type, symbol, mode, run_id, payload=None, tick_id=None):
                with self._lock:
                    self._seq += 1
                    seq = self._seq
                return Event(
                    event_type=event_type,
                    symbol=symbol,
                    mode=mode,
                    run_id=run_id,
                    seq=seq,
                    payload=dict(payload or {}),
                    tick_id=tick_id,
                )

            def recent(self, limit=20, symbol=None):
                return []

        emitter = EventEmitter(store=FakeStore(), symbol="BTCUSDT", mode="paper", run_id="run-a")
        errors = []

        def writer(idx):
            try:
                for i in range(50):
                    emitter.set_tick_id(f"{idx}-{i}")
                    emitter.emit(EventType.TICK, idx=idx, i=i)
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(exc)

        def configurer():
            try:
                for i in range(50):
                    emitter.configure("XAUUSDT", "live", run_id=f"run-{i}")
                    time.sleep(0.001)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(3)]
        threads.append(threading.Thread(target=configurer))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(errors, [], f"Concurrency errors: {errors}")
        self.assertLessEqual(len(emitter.recent(limit=20)), 20)


class TestStatePresenters(unittest.TestCase):
    def test_router_banner_for_live_symbol(self):
        envelope = {
            "by_symbol": {
                "BTCUSDT": {
                    "execution_mode": "live",
                    "regime_router": {"enabled": True, "warning": ""},
                }
            }
        }
        lines = state_view.regime_router_banner_lines(envelope, 100)
        joined = "".join(lines)
        self.assertIn("RegimeRouter LIVE active on: BTCUSDT", joined)

    def test_router_banner_warning_rendered(self):
        envelope = {
            "by_symbol": {
                "BTCUSDT": {
                    "execution_mode": "sim",
                    "regime_router": {"enabled": True, "warning": "[!] forced sim"},
                }
            }
        }
        lines = state_view.regime_router_banner_lines(envelope, 100)
        self.assertIn("forced sim", "".join(lines))

    def test_router_banner_empty_when_no_live_router(self):
        envelope = {"by_symbol": {"BTCUSDT": {"execution_mode": "sim", "regime_router": {"enabled": False}}}}
        self.assertEqual(state_view.regime_router_banner_lines(envelope, 100), [])


class TestSimLiveSplit(unittest.TestCase):
    def test_split_when_mixed_modes(self):
        trades = [
            {"execution_mode": "sim", "net_pnl": 5.0},
            {"execution_mode": "live", "net_pnl": -2.0},
        ]
        lines = _sim_live_split_lines(trades, 100)
        joined = "".join(lines)
        self.assertIn("Sim PnL", joined)
        self.assertIn("Live PnL", joined)

    def test_no_split_when_single_mode(self):
        trades = [{"execution_mode": "live", "net_pnl": 1.0}]
        self.assertEqual(_sim_live_split_lines(trades, 100), [])


class TestRegimeHistoryView(unittest.TestCase):
    def test_history_lines_render_rows(self):
        class _DB:
            def get_regime_history(self, symbol=None, limit=10):
                return [
                    {
                        "timestamp": "2026-06-08T10:00:00",
                        "symbol": "BTCUSDT",
                        "old_regime": "BULL_TREND_WEAK",
                        "new_regime": "PANIC_SELL",
                        "strategy_activated": None,
                        "confidence": 0.82,
                    }
                ]

        lines = state_view.format_regime_history_lines(_DB(), "BTCUSDT", width=100)
        joined = "".join(lines)
        self.assertIn("PANIC_SELL", joined)
        self.assertIn("conf=0.82", joined)

    def test_history_lines_empty_message(self):
        class _DB:
            def get_regime_history(self, symbol=None, limit=10):
                return []

        lines = state_view.format_regime_history_lines(_DB(), width=100)
        self.assertIn("No regime changes", "".join(lines))


if __name__ == "__main__":
    unittest.main()
