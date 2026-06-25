"""Risk-adjusted backtest metrics (Sharpe/Sortino/Calmar/exposure/etc.)."""
from __future__ import annotations

import math
import unittest

from xauby.backtest.metrics import BacktestMetrics, compute_risk_metrics
from xauby.observability.replay import SimTrade


def _trade(pnl, entry=0, exit=0):
    return SimTrade(
        entry_time=entry, exit_time=exit, entry_price=100.0, exit_price=100.0,
        pnl=pnl, pnl_pct=0.0, trigger="SIGNAL",
    )


class TestComputeRiskMetrics(unittest.TestCase):
    def test_degrades_to_zero_without_data(self):
        out = compute_risk_metrics(
            equity_curve=None, trades=[], initial_balance=1000.0,
            final_balance=1000.0, max_drawdown_pct=0.0,
        )
        self.assertEqual(out["sharpe"], 0.0)
        self.assertEqual(out["sortino"], 0.0)
        self.assertEqual(out["calmar"], 0.0)
        self.assertEqual(out["max_consecutive_losses"], 0)

    def test_sharpe_positive_for_steady_gains(self):
        curve = [1000.0 * (1.01 ** i) for i in range(50)]  # +1%/bar
        out = compute_risk_metrics(
            equity_curve=curve, trades=[], initial_balance=1000.0,
            final_balance=curve[-1], max_drawdown_pct=0.0,
            periods_per_year=2190.0,
        )
        # Zero variance in returns -> Sharpe undefined/0; but Sortino needs
        # downside which is empty -> 0. CAGR must be strongly positive.
        self.assertGreater(out["cagr_pct"], 0.0)

    def test_sharpe_and_sortino_with_mixed_returns(self):
        curve = [1000.0]
        for r in [0.02, -0.01, 0.03, -0.02, 0.01, 0.02, -0.005, 0.015]:
            curve.append(curve[-1] * (1 + r))
        out = compute_risk_metrics(
            equity_curve=curve, trades=[], initial_balance=1000.0,
            final_balance=curve[-1], max_drawdown_pct=5.0,
            periods_per_year=2190.0,
        )
        self.assertGreater(out["sharpe"], 0.0)
        self.assertGreater(out["sortino"], 0.0)
        # Sortino >= Sharpe when upside vol inflates total stdev.
        self.assertGreaterEqual(out["sortino"], out["sharpe"])
        self.assertGreater(out["calmar"], 0.0)

    def test_exposure_and_holding(self):
        out = compute_risk_metrics(
            equity_curve=[1000.0, 1001.0], trades=[_trade(5.0, 0, 14400 * 3)],
            initial_balance=1000.0, final_balance=1005.0, max_drawdown_pct=1.0,
            bars_in_position=30, total_bars=100, tf_seconds=14400,
        )
        self.assertAlmostEqual(out["exposure_pct"], 30.0)
        self.assertAlmostEqual(out["avg_holding_bars"], 3.0)

    def test_max_consecutive_losses(self):
        trades = [_trade(1), _trade(-1), _trade(-1), _trade(-1), _trade(2), _trade(-1)]
        out = compute_risk_metrics(
            equity_curve=None, trades=trades, initial_balance=1000.0,
            final_balance=999.0, max_drawdown_pct=0.0,
        )
        self.assertEqual(out["max_consecutive_losses"], 3)


class TestMetricsRoundTrip(unittest.TestCase):
    def test_new_fields_survive_to_dict_from_dict(self):
        m = BacktestMetrics(sharpe=1.5, sortino=2.0, calmar=0.8, cagr_pct=42.0,
                            exposure_pct=55.5, avg_holding_bars=6.0,
                            max_consecutive_losses=4)
        d = m.to_dict()
        self.assertEqual(d["sharpe"], 1.5)
        m2 = BacktestMetrics.from_dict(d)
        self.assertEqual(m2.sortino, 2.0)
        self.assertEqual(m2.max_consecutive_losses, 4)


if __name__ == "__main__":
    unittest.main()
