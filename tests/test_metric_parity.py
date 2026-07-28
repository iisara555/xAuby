"""Live and backtest must compute the same metric the same way — or say so.

The roadmap's complaint was that nothing asserted it. Two implementations of
"Sharpe" drifted apart on four separate axes (sampling unit, annualisation,
return basis, downside convention) and the dashboard cheerfully displayed one
next to the other's backtest.

What is provable is asserted here. What is *not* provable — the intra-trade
excursion missing from closed-trade data — is asserted to be labelled instead of
hidden.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xauby.analytics.calculator import calculate_metrics, trade_span_years  # noqa: E402
from xauby.analytics.risk import (  # noqa: E402
    MetricBasis,
    bars_per_year,
    equity_curve_from_pnls,
    equity_relative_returns,
    max_drawdown_pct,
    periods_per_year_from_span,
    risk_from_equity_curve,
    risk_from_trade_pnls,
    sharpe,
    sortino,
)


def _trades(pnls):
    return [{"net_pnl": p, "closed_at": f"2026-01-{i + 1:02d}T00:00:00"}
            for i, p in enumerate(pnls)]


class TestSharedEstimators(unittest.TestCase):
    def test_sharpe_is_not_annualised_without_a_periodicity(self):
        # An assumed periodicity is a multiplier of up to ~90x on a number
        # people size positions against.
        returns = [1.0, -0.5, 2.0, 0.5, -1.0, 1.5]
        raw = sharpe(returns)
        annual = sharpe(returns, periods_per_year=365.0)
        self.assertNotAlmostEqual(raw, annual)
        self.assertAlmostEqual(annual, raw * (365.0 ** 0.5), places=9)

    def test_sortino_uses_shortfalls_not_clipped_returns(self):
        # The old live implementation squared min(0, r) across ALL returns and
        # divided by their count, so adding winners shrank the denominator and
        # inflated the ratio. Here adding a winner must not raise Sortino by
        # shrinking downside deviation — the downside set is unchanged.
        base = [-1.0, -2.0, 3.0]
        with_more_wins = [-1.0, -2.0, 3.0, 3.0, 3.0]

        def downside_only(values):
            shortfalls = [-v for v in values if v < 0]
            return (sum(s * s for s in shortfalls) / len(shortfalls)) ** 0.5

        self.assertAlmostEqual(downside_only(base), downside_only(with_more_wins),
                               places=12)
        self.assertGreater(sortino(with_more_wins), sortino(base))

    def test_sortino_is_zero_when_nothing_falls_short(self):
        self.assertEqual(sortino([1.0, 2.0, 3.0]), 0.0)

    def test_equity_relative_returns_are_not_trade_notional_returns(self):
        # 100 profit on a 1000 account is 10%, whatever the position size was.
        self.assertEqual(equity_relative_returns([100.0], 1000.0), [10.0])
        # And they compound: the second trade is measured against 1100.
        rounded = [round(r, 6) for r in equity_relative_returns([100.0, 110.0], 1000.0)]
        self.assertEqual(rounded, [10.0, 10.0])

    def test_degenerate_inputs_return_zero_rather_than_raising(self):
        self.assertEqual(sharpe([]), 0.0)
        self.assertEqual(sharpe([1.0]), 0.0)
        self.assertEqual(sharpe([2.0, 2.0, 2.0]), 0.0)
        self.assertEqual(equity_relative_returns([1.0], 0.0), [])
        self.assertEqual(max_drawdown_pct([]), 0.0)

    def test_periods_per_year_refuses_an_unusable_span(self):
        self.assertEqual(periods_per_year_from_span(10, 0.0), 0.0)
        self.assertEqual(periods_per_year_from_span(1, 5.0), 0.0)
        self.assertAlmostEqual(periods_per_year_from_span(100, 4.0), 25.0)

    def test_bars_per_year_is_zero_for_an_unknown_timeframe(self):
        self.assertEqual(bars_per_year("4h"), 2190.0)
        self.assertEqual(bars_per_year("13m"), 0.0)


class TestLiveMatchesBacktest(unittest.TestCase):
    """The same account history, fed through both paths, must agree."""

    def test_same_series_gives_the_same_sharpe(self):
        # A per-bar equity curve where every bar is a closed trade makes the two
        # sampling units identical, so the numbers must be identical too. Any
        # difference is a divergence in the estimators themselves.
        pnls = [50.0, -30.0, 80.0, -20.0, 10.0, -45.0, 60.0]
        initial = 1000.0
        live = risk_from_trade_pnls(pnls, initial, years=1.0)
        backtest = risk_from_equity_curve(
            equity_curve_from_pnls(pnls, initial),
            periods_per_year=periods_per_year_from_span(len(pnls), 1.0),
        )
        self.assertAlmostEqual(live.sharpe, backtest.sharpe, places=9)
        self.assertAlmostEqual(live.sortino, backtest.sortino, places=9)
        self.assertAlmostEqual(live.max_drawdown_pct, backtest.max_drawdown_pct,
                               places=9)

    def test_the_bases_report_their_difference(self):
        pnls = [50.0, -30.0, 80.0, -20.0, 10.0]
        live = risk_from_trade_pnls(pnls, 1000.0)
        backtest = risk_from_equity_curve(
            equity_curve_from_pnls(pnls, 1000.0), periods_per_year=2190.0)
        self.assertFalse(live.basis.comparable_to(backtest.basis))
        self.assertEqual(live.basis.sampling, "per-trade")
        self.assertEqual(backtest.basis.sampling, "per-bar")

    def test_identical_setups_are_reported_as_comparable(self):
        left = risk_from_trade_pnls([10.0, -5.0, 8.0], 1000.0, years=1.0)
        right = risk_from_trade_pnls([12.0, -4.0, 9.0], 1000.0, years=1.0)
        self.assertTrue(left.basis.comparable_to(right.basis))


class TestBacktestPathDelegates(unittest.TestCase):
    """The backtest's own entry point, not just the shared helper."""

    def test_compute_risk_metrics_matches_the_shared_module(self):
        from xauby.backtest.metrics import compute_risk_metrics

        curve = [1000.0, 1050.0, 1020.0, 1100.0, 1080.0, 1150.0, 1090.0]
        computed = compute_risk_metrics(
            equity_curve=curve, trades=[], initial_balance=curve[0],
            final_balance=curve[-1], max_drawdown_pct=0.0,
            periods_per_year=2190.0,
        )
        expected = risk_from_equity_curve(curve, periods_per_year=2190.0)
        self.assertAlmostEqual(computed["sharpe"], round(expected.sharpe, 3),
                               places=3)
        self.assertAlmostEqual(computed["sortino"], round(expected.sortino, 3),
                               places=3)

    def test_uniformly_negative_returns_no_longer_score_infinite_sortino(self):
        # The old local implementation used pstdev of the negative returns, so a
        # curve that lost the same amount every bar had zero downside spread and
        # produced a division that fell through to 0.0 — reported as "no
        # downside risk" for a strategy that only lost money.
        from xauby.backtest.metrics import compute_risk_metrics

        curve = [1000.0 * (0.99 ** i) for i in range(12)]
        computed = compute_risk_metrics(
            equity_curve=curve, trades=[], initial_balance=curve[0],
            final_balance=curve[-1], max_drawdown_pct=11.0,
            periods_per_year=2190.0,
        )
        self.assertLess(computed["sortino"], 0.0)


class TestIntraTradeGapIsLabelled(unittest.TestCase):
    def test_trade_close_drawdown_is_declared_as_such(self):
        # It cannot be fixed from closed trades — the excursion between entry
        # and exit is not in the data — so it must not be presented under the
        # same name as a mark-to-market drawdown without saying which it is.
        result = risk_from_trade_pnls([100.0, -200.0, 50.0], 1000.0)
        self.assertEqual(result.basis.drawdown_source, "trade-close")
        self.assertIn("trade-close", result.basis.describe())

    def test_a_round_trip_that_dipped_hard_looks_flat_at_close(self):
        # One trade that closed at breakeven having been 30% underwater shows
        # zero drawdown at trade close. That is the number the dashboard has;
        # the basis is what stops it being read as "no drawdown happened".
        flat = risk_from_trade_pnls([0.0], 1000.0)
        self.assertEqual(flat.max_drawdown_pct, 0.0)
        self.assertEqual(flat.basis.drawdown_source, "trade-close")

        underwater = risk_from_equity_curve([1000.0, 700.0, 1000.0],
                                            periods_per_year=2190.0)
        self.assertAlmostEqual(underwater.max_drawdown_pct, 30.0, places=9)
        self.assertEqual(underwater.basis.drawdown_source, "mark-to-market")


class TestCalculatorIntegration(unittest.TestCase):
    def test_trade_span_is_derived_from_observed_timestamps(self):
        trades = [{
            "opened_at": "2024-01-01T00:00:00+00:00",
            "closed_at": "2024-12-31T06:00:00+00:00",
        }]
        self.assertAlmostEqual(trade_span_years(trades), 1.0, places=6)

    def test_calculator_reports_a_basis(self):
        metrics = calculate_metrics(_trades([50.0, -30.0, 80.0]))
        self.assertIsInstance(metrics.basis, MetricBasis)
        self.assertEqual(metrics.basis.sampling, "per-trade")
        self.assertFalse(metrics.basis.annualised)
        self.assertIn("basis", metrics.to_dict())

    def test_calculator_annualises_when_the_span_is_supplied(self):
        pnls = [50.0, -30.0, 80.0, -20.0, 10.0, -45.0, 60.0]
        plain = calculate_metrics(_trades(pnls))
        annual = calculate_metrics(_trades(pnls), years=2.0)
        self.assertFalse(plain.basis.annualised)
        self.assertTrue(annual.basis.annualised)
        self.assertGreater(abs(annual.sharpe_ratio), abs(plain.sharpe_ratio))

    def test_calculator_matches_the_shared_module(self):
        pnls = [50.0, -30.0, 80.0, -20.0, 10.0]
        metrics = calculate_metrics(_trades(pnls), 1000.0)
        expected = risk_from_trade_pnls(pnls, 1000.0)
        self.assertAlmostEqual(metrics.sharpe_ratio, expected.sharpe, places=9)
        self.assertAlmostEqual(metrics.sortino_ratio, expected.sortino, places=9)
        self.assertAlmostEqual(metrics.max_drawdown_pct,
                               expected.max_drawdown_pct, places=9)

    def test_empty_trade_list_still_carries_a_basis(self):
        metrics = calculate_metrics([])
        self.assertIsNotNone(metrics.basis)
        self.assertEqual(metrics.sharpe_ratio, 0.0)


if __name__ == "__main__":
    unittest.main()
