import unittest
from datetime import datetime, timedelta, timezone
from xauby.track_record.generator import generate_report
from xauby.track_record.validator import audit_track_record
from unittest.mock import MagicMock

class TestTrackRecord(unittest.TestCase):
    def test_report_generator(self):
        # Use dates relative to now so both trades fall inside the period window
        # regardless of the current date (generate_report filters by closed_at).
        now = datetime.now(timezone.utc)
        d1 = now - timedelta(days=10)
        d2 = now - timedelta(days=5)
        trades = [
            {"net_pnl": 50.0, "entry_cost": 500.0, "net_pnl_pct": 10.0,
             "opened_at": d1.isoformat(), "closed_at": (d1 + timedelta(hours=2)).isoformat()},
            {"net_pnl": -20.0, "entry_cost": 500.0, "net_pnl_pct": -4.0,
             "opened_at": d2.isoformat(), "closed_at": (d2 + timedelta(hours=3)).isoformat()}
        ]
        report = generate_report(trades, period_days=30, report_name="Test 30d")
        self.assertEqual(report.total_trades, 2)
        self.assertEqual(report.win_rate, 50.0)
        self.assertEqual(report.net_pnl, 30.0)
        self.assertEqual(report.average_duration_hours, 2.5) # 2 hours first, 3 hours second -> avg 2.5

    def test_auditor_pass(self):
        db = MagicMock()
        db.query_events.side_effect = lambda event_type, **kwargs: [
            {"event_type": "position_opened", "run_id": "r1", "ts": "2026-05-15T12:00:00", "seq": 1, "symbol": "XAUTUSDT", "payload": {"entry_price": 2000.0, "quantity": 1.0}}
        ] if event_type == "position_opened" else [
            {"event_type": "position_closed", "run_id": "r1", "ts": "2026-05-15T14:00:00", "seq": 2, "symbol": "XAUTUSDT", "payload": {"exit_price": 2050.0}}
        ]
        db.get_closed_trades.return_value = [
            {"net_pnl": 50.0, "amount": 1.0, "entry_price": 2000.0, "exit_price": 2050.0, "opened_at": "2026-05-15T12:00:00", "closed_at": "2026-05-15T14:00:00"}
        ]
        
        success, discrepancies, stats = audit_track_record(db)
        self.assertTrue(success)
        self.assertEqual(len(discrepancies), 0)
        self.assertEqual(stats["matching_trades"], 1)

if __name__ == "__main__":
    unittest.main()
