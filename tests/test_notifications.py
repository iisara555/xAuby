import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

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


if __name__ == "__main__":
    unittest.main()
