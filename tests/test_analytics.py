import unittest
from xauby.analytics.calculator import calculate_metrics

class TestAnalyticsCalculator(unittest.TestCase):
    def test_empty_trades(self):
        metrics = calculate_metrics([])
        self.assertEqual(metrics.net_pnl, 0.0)
        self.assertEqual(metrics.win_rate, 0.0)
        self.assertEqual(metrics.profit_factor, 0.0)
        self.assertEqual(metrics.sharpe_ratio, 0.0)

    def test_sample_trades(self):
        trades = [
            {"net_pnl": 100.0, "entry_cost": 1000.0, "net_pnl_pct": 10.0, "closed_at": "2026-05-01T12:00:00"},
            {"net_pnl": -50.0, "entry_cost": 1000.0, "net_pnl_pct": -5.0, "closed_at": "2026-05-02T12:00:00"},
            {"net_pnl": 200.0, "entry_cost": 2000.0, "net_pnl_pct": 10.0, "closed_at": "2026-05-03T12:00:00"}
        ]
        metrics = calculate_metrics(trades, initial_balance=1000.0)
        self.assertEqual(metrics.net_pnl, 250.0)
        self.assertEqual(metrics.gross_profit, 300.0)
        self.assertEqual(metrics.gross_loss, 50.0)
        self.assertEqual(metrics.profit_factor, 6.0)
        self.assertAlmostEqual(metrics.win_rate, 66.6666666, places=4)
        self.assertEqual(metrics.consecutive_wins, 1)
        self.assertEqual(metrics.consecutive_losses, 1)
        self.assertTrue(metrics.sharpe_ratio > 0)
        self.assertTrue(metrics.sortino_ratio > 0)

if __name__ == "__main__":
    unittest.main()
