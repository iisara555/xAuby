import unittest
import tempfile
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from xauby.notifications.interface import AlertLevel
from xauby.notifications.report_formatter import (
    build_period_report_message,
    format_report_telegram,
)
from xauby.notifications.scheduler import ReportScheduler
from xauby.notifications.settings import NotificationSettings
from xauby.track_record.models import PerformanceReport


class MockNotificationService:
    def __init__(self):
        self.alerts = []

    def send_alert(self, message, level=AlertLevel.TRADE, reply_markup=None):
        self.alerts.append((message, level, reply_markup))

    def stop(self):
        pass


class _Resp:
    def __init__(self, status_code=200, text="OK", result=None):
        self.status_code = status_code
        self.text = text
        self._result = [] if result is None else result

    def json(self):
        return {"result": self._result}


class TestNotificationSettings(unittest.TestCase):
    def test_from_config_defaults(self):
        s = NotificationSettings.from_config({})
        self.assertTrue(s.send_alerts)
        self.assertTrue(s.notify_guard_blocks)

    def test_from_config_overrides(self):
        s = NotificationSettings.from_config(
            {"notifications": {"send_alerts": False, "regime_score_threshold": 5}}
        )
        self.assertFalse(s.send_alerts)
        self.assertEqual(s.regime_score_threshold, 5)


class TestReportFormatter(unittest.TestCase):
    def test_empty_trades_report(self):
        msg = build_period_report_message([], 7, "Weekly Review")
        self.assertIn("Weekly Review", msg)
        self.assertIn("Trades: `0`", msg)

    def test_format_report_telegram(self):
        report = PerformanceReport(
            report_name="7-Day",
            period_days=7,
            total_trades=2,
            win_rate=50.0,
            net_pnl=10.5,
            profit_factor=1.5,
            max_drawdown_pct=1.2,
            average_duration_hours=4.0,
            entries=[],
        )
        msg = format_report_telegram(report, "Weekly", {"regime": "RISK-OFF", "gold_score": 65})
        self.assertIn("RISK-OFF", msg)
        self.assertIn("65/100", msg)


class TestMultiPairFormat(unittest.TestCase):
    def test_multi_regime_lists_pairs(self):
        from xauby.notifications.multi_pair_format import format_multi_regime

        msg = format_multi_regime(
            ["XAUTUSDT", "BTCUSDT"],
            lambda s: {
                "XAUTUSDT": {"regime": "RISK-OFF", "gold_score": 65, "trend": "NEUTRAL", "macro_bias": "RISK-OFF", "reasons": []},
                "BTCUSDT": {"regime": "TRENDING BEARISH", "gold_score": 40, "trend": "BEARISH", "macro_bias": "RISK-OFF", "reasons": []},
            }.get(s),
        )
        self.assertIn("2` pair(s)", msg)
        self.assertIn("XAUTUSDT", msg)
        self.assertIn("BTCUSDT", msg)
        self.assertIn("RISK-OFF", msg)
        self.assertIn("TRENDING BEARISH", msg)

    def test_multi_pnl_portfolio_and_per_pair(self):
        from xauby.notifications.multi_pair_format import format_multi_pnl

        trades = [
            {"symbol": "XAUTUSDT", "net_pnl": 10.0, "closed_at": datetime.now(timezone.utc).isoformat()},
            {"symbol": "BTCUSDT", "net_pnl": -2.0, "closed_at": datetime.now(timezone.utc).isoformat()},
        ]
        msg = format_multi_pnl(
            ["XAUTUSDT", "BTCUSDT"],
            lambda sym, limit: trades if sym is None else [t for t in trades if t["symbol"] == sym],
        )
        self.assertIn("Portfolio", msg)
        self.assertIn("XAUTUSDT", msg)
        self.assertIn("BTCUSDT", msg)

    def test_multi_daily_digest(self):
        from xauby.notifications.multi_pair_format import build_multi_daily_digest

        msg = build_multi_daily_digest(
            ["XAUTUSDT", "BTCUSDT"],
            100.0,
            lambda sym, limit: [],
            lambda s: "IDLE",
            lambda s: {"regime": "RISK-OFF", "gold_score": 50},
        )
        self.assertIn("2` pair(s)", msg)
        self.assertIn("Per pair", msg)
        self.assertIn("XAUTUSDT", msg)


class TestReportScheduler(unittest.TestCase):
    def test_weekly_runs_once_per_slot(self):
        cfg = {
            "weekly_review": {
                "enabled": True,
                "day_of_week": 6,
                "hour_utc": 17,
                "period_days": 7,
                "send_telegram": True,
                "save_to_file": False,
            },
            "daily_digest": {"enabled": False},
        }
        sched = ReportScheduler(cfg)
        engine = MagicMock()
        engine.symbol = "XAUTUSDT"
        engine.db.get_closed_trades.return_value = []
        engine.current_regime = None
        engine.send_telegram_alert = MagicMock()

        sunday = datetime(2026, 5, 31, 17, 30, tzinfo=timezone.utc)  # Sunday
        sched.check_and_run(engine, sunday)
        sched.check_and_run(engine, sunday)
        self.assertEqual(engine.send_telegram_alert.call_count, 1)


class TestAlertGating(unittest.TestCase):
    def test_send_alerts_false_blocks_trade(self):
        from xauby.engine.trading import LiteTradingEngine
        from tests.mocks import MockExchangeGateway, MockDatabaseRepository

        notif = MockNotificationService()
        engine = LiteTradingEngine(
            config_path="bot_config.yaml",
            client=MockExchangeGateway(),
            db=MockDatabaseRepository(),
            notification_service=notif,
        )
        engine._notif_settings = NotificationSettings(send_alerts=False)
        engine.tg_enabled = True
        engine.send_telegram_alert("trade alert", level=AlertLevel.TRADE)
        self.assertEqual(len(notif.alerts), 0)

    def test_critical_always_sent(self):
        from xauby.engine.trading import LiteTradingEngine
        from xauby.notifications.telegram_bot import CRITICAL_ALERT_KEYBOARD
        from tests.mocks import MockExchangeGateway, MockDatabaseRepository

        notif = MockNotificationService()
        engine = LiteTradingEngine(
            config_path="bot_config.yaml",
            client=MockExchangeGateway(),
            db=MockDatabaseRepository(),
            notification_service=notif,
        )
        engine._notif_settings = NotificationSettings(send_alerts=False)
        engine.tg_enabled = True
        engine.send_telegram_alert("critical", level=AlertLevel.CRITICAL)
        self.assertEqual(len(notif.alerts), 1)
        self.assertEqual(notif.alerts[0][2], CRITICAL_ALERT_KEYBOARD)


class TestTelegramCommandPoller(unittest.TestCase):
    def _poller(self):
        from xauby.notifications.telegram_bot import TelegramCommandPoller

        engine = MagicMock()
        engine.format_telegram_status.return_value = "status text"
        engine.format_telegram_pnl.return_value = "pnl text"
        engine.format_telegram_regime.return_value = "regime text"
        engine.format_telegram_last_trades.return_value = "last text"
        engine.format_telegram_health.return_value = "health text"
        engine.set_telegram_trading_paused.side_effect = (
            lambda paused, actor="telegram": "paused" if paused else "resumed"
        )
        poller = TelegramCommandPoller("token", "123", engine)
        poller._reply = MagicMock()
        poller._answer_callback = MagicMock()
        return poller, engine

    def test_authorized_status_command_supports_bot_suffix(self):
        poller, engine = self._poller()
        poller._handle_update({
            "message": {"chat": {"id": 123}, "text": "/status@XaubyBot"},
        })

        engine.format_telegram_status.assert_called_once_with()
        poller._reply.assert_called_once_with("123", "status text")

    def test_unauthorized_command_and_callback_are_ignored(self):
        poller, engine = self._poller()
        poller._handle_update({
            "message": {"chat": {"id": 999}, "text": "/status"},
        })
        poller._handle_update({
            "callback_query": {
                "id": "cb1",
                "data": "semi_buy_confirm",
                "message": {"chat": {"id": 999}},
            },
        })

        engine.format_telegram_status.assert_not_called()
        engine.confirm_semi_auto_buy.assert_not_called()
        poller._reply.assert_not_called()
        poller._answer_callback.assert_not_called()

    def test_semi_auto_callbacks_call_engine_and_ack(self):
        poller, engine = self._poller()
        poller._handle_update({
            "callback_query": {
                "id": "cb1",
                "data": "semi_buy_confirm",
                "message": {"chat": {"id": "123"}},
            },
        })
        poller._handle_update({
            "callback_query": {
                "id": "cb2",
                "data": "semi_buy_skip",
                "message": {"chat": {"id": "123"}},
            },
        })

        engine.confirm_semi_auto_buy.assert_called_once_with()
        engine.skip_semi_auto_buy.assert_called_once_with()
        self.assertEqual(poller._answer_callback.call_count, 2)
        self.assertEqual(poller._reply.call_count, 2)

    def test_health_command_replies_with_engine_health(self):
        poller, engine = self._poller()
        poller._handle_update({
            "message": {"chat": {"id": 123}, "text": "/health"},
        })

        engine.format_telegram_health.assert_called_once_with()
        poller._reply.assert_called_once_with("123", "health text")

    def test_pause_and_resume_commands_require_confirmation(self):
        from xauby.notifications.telegram_bot import PAUSE_CONFIRM_KEYBOARD, RESUME_CONFIRM_KEYBOARD

        poller, engine = self._poller()
        poller._handle_update({
            "message": {"chat": {"id": 123}, "text": "/pause"},
        })
        poller._handle_update({
            "message": {"chat": {"id": 123}, "text": "/resume"},
        })

        engine.set_telegram_trading_paused.assert_not_called()
        self.assertEqual(poller._reply.call_args_list[0].kwargs["reply_markup"], PAUSE_CONFIRM_KEYBOARD)
        self.assertEqual(poller._reply.call_args_list[1].kwargs["reply_markup"], RESUME_CONFIRM_KEYBOARD)

    def test_pause_resume_callbacks_update_engine_control(self):
        poller, engine = self._poller()
        poller._handle_update({
            "callback_query": {
                "id": "cb1",
                "data": "op_pause_confirm",
                "message": {"chat": {"id": "123"}},
            },
        })
        poller._handle_update({
            "callback_query": {
                "id": "cb2",
                "data": "op_resume_confirm",
                "message": {"chat": {"id": "123"}},
            },
        })

        engine.set_telegram_trading_paused.assert_any_call(True, actor="telegram")
        engine.set_telegram_trading_paused.assert_any_call(False, actor="telegram")
        self.assertEqual(poller._answer_callback.call_count, 2)
        self.assertEqual(poller._reply.call_args_list[0].args, ("123", "paused"))
        self.assertEqual(poller._reply.call_args_list[1].args, ("123", "resumed"))

    def test_critical_alert_callbacks_show_status_and_ack(self):
        poller, engine = self._poller()
        poller._handle_update({
            "callback_query": {
                "id": "cb1",
                "data": "critical_status",
                "message": {"chat": {"id": "123"}},
            },
        })
        poller._handle_update({
            "callback_query": {
                "id": "cb2",
                "data": "critical_ack",
                "message": {"chat": {"id": "123"}},
            },
        })

        engine.format_telegram_health.assert_called_once_with()
        self.assertEqual(poller._reply.call_args_list[0].args, ("123", "health text"))
        self.assertIn("acknowledged", poller._reply.call_args_list[1].args[1])

    @patch("xauby.notifications.telegram_bot.requests.get")
    def test_poll_loop_continues_after_handler_exception(self, mock_get):
        from xauby.notifications.telegram_bot import TelegramCommandPoller

        engine = MagicMock()
        engine.format_telegram_status.side_effect = [RuntimeError("boom"), "ok"]
        poller = TelegramCommandPoller("token", "123", engine)
        replies = []

        def reply(chat_id, text):
            replies.append((chat_id, text))
            if text == "ok":
                poller._stop.set()

        poller._reply = MagicMock(side_effect=reply)
        mock_get.return_value = _Resp(
            200,
            result=[
                {"update_id": 1, "message": {"chat": {"id": 123}, "text": "/status"}},
                {"update_id": 2, "message": {"chat": {"id": 123}, "text": "/status"}},
            ],
        )

        poller._poll_loop()

        self.assertEqual(engine.format_telegram_status.call_count, 2)
        self.assertEqual(replies[0], ("123", "⚠️ *Telegram command failed* — check engine logs."))
        self.assertEqual(replies[1], ("123", "ok"))
        self.assertEqual(poller._offset, 3)


class TestTelegramNotificationService(unittest.TestCase):
    @patch("xauby.notifications.telegram.requests.post")
    def test_post_message_sends_markdown_payload(self, mock_post):
        from xauby.notifications.telegram import TelegramNotificationService

        mock_post.return_value = _Resp(200)
        svc = TelegramNotificationService("token", "123", enabled=True)
        try:
            self.assertTrue(svc._post_message("*hello*"))
        finally:
            svc.stop()

        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["parse_mode"], "Markdown")
        self.assertEqual(payload["text"], "*hello*")

    @patch("xauby.notifications.telegram.requests.post")
    def test_markdown_parse_error_retries_plain_text_once(self, mock_post):
        from xauby.notifications.telegram import TelegramNotificationService

        mock_post.side_effect = [
            _Resp(400, "Bad Request: can't parse Markdown entities"),
            _Resp(200),
        ]
        svc = TelegramNotificationService("token", "123", enabled=True)
        try:
            self.assertTrue(svc._post_message("*bad markdown"))
        finally:
            svc.stop()

        first = mock_post.call_args_list[0].kwargs["json"]
        second = mock_post.call_args_list[1].kwargs["json"]
        self.assertEqual(first["parse_mode"], "Markdown")
        self.assertNotIn("parse_mode", second)
        self.assertEqual(second["text"], "*bad markdown")

    @patch("xauby.notifications.telegram.requests.post")
    def test_failed_send_writes_failure_log(self, mock_post):
        from xauby.notifications.telegram import TelegramNotificationService

        mock_post.return_value = _Resp(500, "server error")
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/telegram_failures.log"
            svc = TelegramNotificationService(
                "token", "123", enabled=True, failure_log_path=path, max_retries=2
            )
            try:
                self.assertFalse(svc._post_message("hello"))
            finally:
                svc.stop()
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        self.assertIn("server error", text)
        self.assertIn("hello", text)

    def test_stop_is_idempotent_and_joins_worker(self):
        from xauby.notifications.telegram import TelegramNotificationService

        svc = TelegramNotificationService("token", "123", enabled=True)
        svc.stop()
        svc.stop()
        self.assertFalse(svc._thread.is_alive())


class TestTelegramRuntimeControl(unittest.TestCase):
    def test_pause_state_round_trips_under_runtime_root(self):
        from xauby.runtime.telegram_control import load_control_state, set_trading_paused, trading_pause_reason

        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"XAUBY_HOME": tmp}, clear=False):
            self.assertFalse(load_control_state()["trading_paused"])
            set_trading_paused(True, reason="test pause", actor="test")
            paused, reason = trading_pause_reason()
            self.assertTrue(paused)
            self.assertIn("test pause", reason)
            set_trading_paused(False, reason="test resume", actor="test")
            self.assertFalse(trading_pause_reason()[0])

    def test_execute_buy_blocks_when_telegram_paused(self):
        from xauby.engine.orders import OrderMixin
        from xauby.runtime.telegram_control import set_trading_paused

        class Stub(OrderMixin):
            def __init__(self):
                self.last_log_message = ""
                self.events = []

            def _sym(self):
                return "XAUTUSDT"

            def _emit_event(self, *args, **kwargs):
                self.events.append((args, kwargs))

        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"XAUBY_HOME": tmp}, clear=False):
            set_trading_paused(True, reason="operator pause", actor="test")
            stub = Stub()
            self.assertFalse(stub.execute_buy(100.0, 1.0, symbol="XAUTUSDT"))
            self.assertIn("Telegram pause", stub.last_log_message)
            self.assertTrue(stub.events)


if __name__ == "__main__":
    unittest.main()
