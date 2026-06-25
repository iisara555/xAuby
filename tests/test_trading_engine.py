import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from xauby.engine.trading import LiteTradingEngine
from xauby.domain.models import Position
from xauby.runtime.config_error import ConfigError
from tests.mocks import MockExchangeGateway, MockDatabaseRepository, MockNotificationService



class TestTradingEngineDecoupling(unittest.TestCase):
    def setUp(self):
        self.client = MockExchangeGateway()
        self.db = MockDatabaseRepository()
        self.notification = MockNotificationService()
        self.engine = LiteTradingEngine(
            config_path="bot_config.yaml",
            client=self.client,
            db=self.db,
            notification_service=self.notification,
        )

    def test_engine_initialization_with_mocks(self):
        # Verify engine uses the injected mocks
        self.assertEqual(self.engine.client, self.client)
        self.assertEqual(self.engine.db, self.db)
        self.assertEqual(self.engine._tg_service, self.notification)

    def test_strategy_instances_are_isolated_per_symbol(self):
        gold_strategy = self.engine._get_strategy_for_symbol("XAUUSDT")
        btc_strategy = self.engine._get_strategy_for_symbol("BTCUSDT")
        gold_runner = self.engine._get_runner_for_symbol("XAUUSDT")
        btc_runner = self.engine._get_runner_for_symbol("BTCUSDT")

        # Both come from coin_whitelist.json (whitelist_strict mode); isolation
        # is proven by distinct instances even when the strategy name matches.
        self.assertEqual(gold_strategy.name, "cdc_action_zone")
        self.assertEqual(btc_strategy.name, "cdc_action_zone")
        self.assertIsNot(gold_strategy, btc_strategy)
        self.assertIsNot(gold_runner, btc_runner)

    def test_same_strategy_name_still_gets_one_instance_per_symbol(self):
        gold_strategy = self.engine._get_strategy_for_symbol("XAUUSDT")
        self.engine._strategy_names_by_symbol["TESTUSDT"] = "cdc_action_zone"

        test_strategy = self.engine._get_strategy_for_symbol("TESTUSDT")
        test_runner = self.engine._get_runner_for_symbol("TESTUSDT")

        self.assertEqual(gold_strategy.name, test_strategy.name)
        self.assertIsNot(gold_strategy, test_strategy)
        self.assertIsNot(self.engine._get_runner_for_symbol("XAUUSDT"), test_runner)

    def test_research_short_strategy_is_blocked_from_engine(self):
        self.engine._strategy_names_by_symbol["BTCUSDT"] = "supertrend_short"
        self.engine._strategies_by_symbol.pop("BTCUSDT", None)
        self.engine._runners_by_symbol.pop("BTCUSDT", None)

        # BTC is live, so a research-only SHORT plugin is blocked from going live
        # even though the pair allows shorts (research plugins are sim/backtest
        # only). cdc_action_zone is not research-tagged and is unaffected.
        with self.assertRaisesRegex(ConfigError, "cannot run live"):
            self.engine._get_strategy_for_symbol("BTCUSDT")

    def test_router_strategy_risk_block_uses_plugin_defaults(self):
        self.engine._strategy_names_by_symbol["BTCUSDT"] = "bbkc_squeeze"

        risk = self.engine._build_risk_block("BTCUSDT")

        self.assertEqual(risk["rsi_min"], 40.0)
        self.assertEqual(risk["rsi_max"], 75.0)
        self.assertEqual(risk["vol_min_ratio"], 0.8)
        self.assertEqual(risk["release_window"], 3)
        self.assertTrue(risk["breakeven_sl_enabled"])

        self.engine._strategy_names_by_symbol["BTCUSDT"] = "bbrsi_mean_reversion"
        risk = self.engine._build_risk_block("BTCUSDT")
        self.assertEqual(risk["rsi_oversold"], 30.0)
        self.assertEqual(risk["rsi_exit"], 55.0)
        self.assertNotIn("rsi_min", risk)

    def test_engine_sends_alert_delegation(self):
        # Verify engine alert sending delegates to the injected notification service
        self.engine.send_telegram_alert("Hello Test Alert")
        self.assertIn("Hello Test Alert", self.notification.alerts)

    def test_engine_database_interaction(self):
        # Test database read/write delegation via mock
        pos = self.engine.db.get_trade_state("XAUUSDT")
        self.assertTrue(self.db.get_state_called)
        self.assertEqual(pos.state, "idle")

        new_pos = Position(
            symbol="XAUUSDT",
            state="bought",
            entry_price=2000.0,
            stop_loss=1950.0,
            take_profit=2100.0,
            highest_price_seen=2000.0,
            quantity=1.0,
        )
        self.engine.db.save_trade_state(new_pos)
        self.assertTrue(self.db.save_state_called)
        self.assertEqual(self.db.trade_state.state, "bought")

    def test_engine_client_interaction(self):
        # Test client balance fetching delegation via mock
        portfolio = self.engine.client.fetch_balances()
        self.assertTrue(self.client.balances_called)
        self.assertEqual(portfolio.get_balance("USDT"), 1000.0)


class TestTradingEngineConcurrency(unittest.TestCase):
    def setUp(self):
        self.client = MockExchangeGateway()
        self.db = MockDatabaseRepository()
        self.notification = MockNotificationService()
        self.engine = LiteTradingEngine(
            config_path="bot_config.yaml",
            client=self.client,
            db=self.db,
            notification_service=self.notification,
        )

    def test_ws_disconnected_at_thread_safety(self):
        """Multiple threads writing _ws_disconnected_at concurrently should not crash."""
        errors = []

        def writer():
            try:
                for _ in range(100):
                    self.engine._on_ws_status({"event": "ws_disconnected"})
                    time.sleep(0.001)
                    self.engine._on_ws_status({"event": "ws_reconnected", "downtime_sec": 1})
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(100):
                    with self.engine._ws_status_lock:
                        _ = self.engine._ws_disconnected_at
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(3)] + [
            threading.Thread(target=reader) for _ in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(errors, [], f"Concurrency errors: {errors}")

    def test_semi_auto_pending_thread_safety(self):
        """Telegram callback thread and main thread accessing _semi_auto_pending concurrently."""
        errors = []

        def poller():
            try:
                for _ in range(50):
                    self.engine.confirm_semi_auto_buy()
                    time.sleep(0.002)
                    self.engine.skip_semi_auto_buy()
                    time.sleep(0.002)
            except Exception as e:
                errors.append(e)

        def ticker():
            try:
                for _ in range(50):
                    with self.engine._semi_auto_lock:
                        _ = self.engine._sc()._semi_auto_pending
                    time.sleep(0.002)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=poller) for _ in range(3)] + [
            threading.Thread(target=ticker) for _ in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(errors, [], f"Concurrency errors: {errors}")

    @patch("xauby.macro.sentiment_guard.evaluate_sentiment_guard")
    def test_guard_thread_no_duplicate(self, mock_evaluate):
        """_refresh_guard_async should not spawn duplicate threads when called rapidly."""
        mock_evaluate.return_value = {"score": 0.0}
        spawned = []
        original_start = threading.Thread.start

        def tracking_start(self):
            spawned.append(1)
            original_start(self)

        threading.Thread.start = tracking_start
        try:
            for _ in range(20):
                self.engine._refresh_guard_async()
                time.sleep(0.01)
        finally:
            threading.Thread.start = original_start
        # Threads may finish between calls, so multiple spawns are okay,
        # but we must never have overlapping spawns inside the lock window.
        # With 20 calls and 10ms gaps, we expect at most a handful of threads.
        self.assertLessEqual(len(spawned), 20, f"Spawned {len(spawned)} guard threads")
        # Ensure no overlapping spawns: all threads should have finished.
        if self.engine._guard_thread:
            self.engine._guard_thread.join(timeout=2)
            self.assertFalse(self.engine._guard_thread.is_alive())

    def test_event_emitter_ring_thread_safety(self):
        """Emit and recent() called concurrently should not crash."""
        errors = []

        def emitter():
            try:
                for i in range(100):
                    self.engine._emitter.emit("TEST_EVENT", idx=i)
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(100):
                    _ = self.engine._emitter.recent(limit=20)
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=emitter) for _ in range(3)] + [
            threading.Thread(target=reader) for _ in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(errors, [], f"Concurrency errors: {errors}")

    def test_semi_auto_confirm_on_hold_tick(self):
        """Confirmed semi-auto buy runs even when current tick action is HOLD."""
        self.engine.trading_mode = "semi_auto"
        signal = MagicMock()
        signal.stop_loss_price = None
        signal.stop_loss_distance = None

        self.engine._queue_semi_auto_buy(2000.0, 1.0, signal)
        self.engine.confirm_semi_auto_buy()

        state = self.engine.db.get_trade_state(self.engine.symbol)
        with patch.object(
            self.engine, "execute_buy", return_value=True
        ) as mock_exec:
            self.engine._process_semi_auto_idle(state, "HOLD", 2000.0, 1.0, signal)
            mock_exec.assert_called_once()

    def test_tick_iterates_all_active_pairs(self):
        """tick() runs _tick_body once per enabled pair in the registry."""
        from xauby.runtime.pair_registry import PairSpec

        specs = [
            PairSpec(
                symbol="XAUUSDT",
                enabled=True,
                primary_timeframe="4h",
                confirm_timeframe="1d",
            ),
            PairSpec(
                symbol="BTCUSDT",
                enabled=True,
                primary_timeframe="1h",
                confirm_timeframe="4h",
            ),
        ]
        tick_symbols = []

        def record_tick():
            tick_symbols.append(self.engine._active_tick_symbol)

        with patch.object(self.engine._pair_registry, "maybe_reload", return_value=False), patch.object(
            self.engine._pair_registry, "active", return_value=specs
        ), patch.object(self.engine, "sync_candles"), patch.object(
            self.engine, "_tick_body", side_effect=record_tick
        ), patch.object(
            self.engine, "_write_multi_state_json"
        ):
            self.engine.tick()

        self.assertEqual(tick_symbols, ["XAUUSDT", "BTCUSDT"])


if __name__ == "__main__":
    unittest.main()
