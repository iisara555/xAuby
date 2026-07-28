"""The statistics have to be right, and they have to refuse the wrong questions.

Two of these tests exist because the failure mode is a *plausible number*: a
shuffle test on an order-invariant statistic always answers "not significant",
and a deflated Sharpe with n_trials=1 deflates nothing while looking rigorous.
Both would pass review as long as nobody checked what they measured.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xauby.backtest.significance import (  # noqa: E402
    OrderInvariantStatistic,
    benjamini_hochberg,
    bonferroni,
    bootstrap_returns,
    compound,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    longest_losing_streak,
    max_drawdown,
    normal_cdf,
    normal_ppf,
    probabilistic_sharpe_ratio,
    sample_kurtosis,
    sample_skew,
    shuffle_pvalue,
)


class TestDistributions(unittest.TestCase):
    def test_normal_cdf_known_points(self):
        self.assertAlmostEqual(normal_cdf(0.0), 0.5, places=12)
        self.assertAlmostEqual(normal_cdf(1.959963985), 0.975, places=8)
        self.assertAlmostEqual(normal_cdf(-1.959963985), 0.025, places=8)

    def test_normal_ppf_is_the_inverse(self):
        for p in (0.001, 0.01, 0.025, 0.1, 0.5, 0.9, 0.975, 0.99, 0.9999):
            with self.subTest(p=p):
                self.assertAlmostEqual(normal_cdf(normal_ppf(p)), p, places=10)

    def test_normal_ppf_tail_accuracy(self):
        # expected_max_sharpe evaluates this at 1 - 1/N for large N, so the tail
        # is not an academic corner.
        for n in (100, 1000, 10000, 100000):
            p = 1.0 - 1.0 / n
            with self.subTest(n=n):
                self.assertAlmostEqual(normal_cdf(normal_ppf(p)), p, places=10)

    def test_normal_ppf_rejects_out_of_range(self):
        for p in (0.0, 1.0, -0.1, 1.1):
            with self.subTest(p=p):
                with self.assertRaises(ValueError):
                    normal_ppf(p)


class TestPathStatistics(unittest.TestCase):
    def test_compound_is_multiplicative(self):
        self.assertAlmostEqual(compound([10.0, 10.0]), 21.0, places=9)

    def test_max_drawdown_measures_peak_to_trough(self):
        self.assertAlmostEqual(max_drawdown([10.0, -20.0, 5.0]), 20.0, places=9)
        self.assertEqual(max_drawdown([1.0, 2.0, 3.0]), 0.0)

    def test_longest_losing_streak(self):
        self.assertEqual(longest_losing_streak([1, -1, -1, -1, 2, -1]), 3)


class TestBootstrap(unittest.TestCase):
    def test_is_deterministic_for_a_seed(self):
        values = [1.0, -2.0, 3.0, 0.5, -1.5, 4.0, 2.0]
        self.assertEqual(bootstrap_returns(values, samples=500).to_dict(),
                         bootstrap_returns(values, samples=500).to_dict())

    def test_refuses_a_handful_of_observations(self):
        result = bootstrap_returns([1.0, 2.0], samples=100)
        self.assertEqual(result.samples, 0)
        self.assertIn("fewer than", result.note)

    def test_interval_brackets_the_median(self):
        values = [2.0, -1.0, 3.0, 1.0, -2.0, 4.0, 0.5, 1.5]
        result = bootstrap_returns(values, samples=2000)
        self.assertLessEqual(result.p05_pct, result.median_pct)
        self.assertLessEqual(result.median_pct, result.p95_pct)

    def test_all_positive_returns_give_certainty(self):
        result = bootstrap_returns([1.0] * 10, samples=500)
        self.assertEqual(result.prob_profitable, 1.0)

    def test_block_bootstrap_is_labelled(self):
        values = [1.0, -1.0, 2.0, -2.0, 3.0, 0.0, 1.0]
        self.assertIn("block", bootstrap_returns(values, samples=200, block=3).note)


class TestShuffle(unittest.TestCase):
    def test_refuses_an_order_invariant_statistic(self):
        # The trap: compounding commutes, so this test can never reject. Before
        # the guard it returned p ≈ 1.0 and read as a clean "not significant".
        values = [3.0, -2.0, 5.0, -1.0, 2.0, -4.0, 1.0]
        with self.assertRaises(OrderInvariantStatistic):
            shuffle_pvalue(values, compound, samples=100)

    def test_refuses_a_sum_style_statistic(self):
        values = [3.0, -2.0, 5.0, -1.0, 2.0, -4.0, 1.0]
        with self.assertRaises(OrderInvariantStatistic):
            shuffle_pvalue(values, lambda r: float(sum(r)), samples=100)

    def test_drawdown_is_testable(self):
        values = [3.0, -2.0, 5.0, -1.0, 2.0, -4.0, 1.0, -3.0, 6.0]
        result = shuffle_pvalue(values, max_drawdown, samples=500)
        self.assertGreater(result.p_value, 0.0)
        self.assertLessEqual(result.p_value, 1.0)

    def test_a_front_loaded_loss_run_looks_bad_among_shuffles(self):
        # All the losses first is close to the worst ordering available, so few
        # permutations do worse and the p-value must be near 1.
        values = [-5.0] * 5 + [6.0] * 5
        result = shuffle_pvalue(values, max_drawdown, samples=1000)
        self.assertGreater(result.p_value, 0.8)
        self.assertLess(result.mean_shuffled, result.observed)

    def test_p_value_can_never_be_zero(self):
        # The observed ordering is one of the permutations; a p of exactly 0
        # would claim it was impossible.
        values = [1.0, 2.0, -3.0, 4.0, -1.0, 5.0, -2.0]
        result = shuffle_pvalue(values, max_drawdown, samples=200)
        self.assertGreater(result.p_value, 0.0)

    def test_a_sample_that_cannot_move_the_statistic_is_refused_too(self):
        # Not a property of max_drawdown but of this series: with one losing
        # trade the drawdown is identical in every ordering, so the test has
        # nothing to say and must not report p = 1.0 as "not significant".
        values = [1.0, 2.0, 3.0, 4.0, -1.0, 5.0]
        with self.assertRaises(OrderInvariantStatistic) as ctx:
            shuffle_pvalue(values, max_drawdown, samples=100)
        self.assertIn("single losing trade", str(ctx.exception))

    def test_is_deterministic_for_a_seed(self):
        values = [1.0, -3.0, 2.0, 4.0, -2.0, 1.0, -1.0]
        self.assertEqual(shuffle_pvalue(values, max_drawdown, samples=300).to_dict(),
                         shuffle_pvalue(values, max_drawdown, samples=300).to_dict())


class TestSharpeUnderSelection(unittest.TestCase):
    def test_psr_of_the_benchmark_itself_is_a_half(self):
        # A Sharpe exactly equal to the benchmark is a coin flip by definition.
        self.assertAlmostEqual(
            probabilistic_sharpe_ratio(0.5, 100, benchmark=0.5), 0.5, places=9)

    def test_psr_rises_with_sample_length(self):
        short = probabilistic_sharpe_ratio(0.2, 30)
        long = probabilistic_sharpe_ratio(0.2, 300)
        self.assertGreater(long, short)

    def test_negative_skew_and_fat_tails_reduce_psr(self):
        plain = probabilistic_sharpe_ratio(0.3, 200)
        ugly = probabilistic_sharpe_ratio(0.3, 200, skew=-1.5, kurtosis=8.0)
        self.assertLess(ugly, plain)

    def test_psr_reports_nan_when_the_expansion_breaks_down(self):
        # Rather than returning a confident number from a formula that no longer
        # holds.
        self.assertTrue(math.isnan(
            probabilistic_sharpe_ratio(2.0, 100, skew=5.0, kurtosis=3.0)))

    def test_expected_max_sharpe_grows_with_trials(self):
        one = expected_max_sharpe(1, 0.25)
        ten = expected_max_sharpe(10, 0.25)
        thousand = expected_max_sharpe(1000, 0.25)
        self.assertEqual(one, 0.0)
        self.assertGreater(ten, one)
        self.assertGreater(thousand, ten)

    def test_expected_max_sharpe_scales_with_spread(self):
        self.assertAlmostEqual(expected_max_sharpe(144, 1.0),
                               expected_max_sharpe(144, 0.25) * 2.0, places=9)

    def test_searching_harder_lowers_the_deflated_sharpe(self):
        # The whole point: the same result, found after more searching, means
        # less. The actionzone sweep scored 144 configurations.
        returns = [1.2, -0.4, 0.9, 1.6, -0.8, 2.1, 0.3, -0.2, 1.1, 0.7] * 5
        single = deflated_sharpe_ratio(returns, n_trials=1, sharpe_variance=0.25)
        searched = deflated_sharpe_ratio(returns, n_trials=144, sharpe_variance=0.25)
        self.assertEqual(single.deflated_sharpe, single.probabilistic_sharpe)
        self.assertLess(searched.deflated_sharpe, single.deflated_sharpe)

    def test_a_trial_count_of_one_says_so(self):
        returns = [1.0, -0.5, 0.8, 1.2, -0.3, 0.6]
        result = deflated_sharpe_ratio(returns, n_trials=1, sharpe_variance=0.25)
        self.assertIn("nothing was deflated", result.note)

    def test_skew_and_kurtosis_helpers(self):
        symmetric = [-2.0, -1.0, 0.0, 1.0, 2.0]
        self.assertAlmostEqual(sample_skew(symmetric), 0.0, places=9)
        # Normal-ish reference: kurtosis is reported non-excess.
        self.assertGreater(sample_kurtosis(symmetric), 1.0)

    def test_degenerate_inputs_do_not_raise(self):
        self.assertIn("too few", deflated_sharpe_ratio(
            [1.0], n_trials=5, sharpe_variance=0.1).note)
        self.assertIn("zero variance", deflated_sharpe_ratio(
            [1.0, 1.0, 1.0], n_trials=5, sharpe_variance=0.1).note)


class TestMultipleTesting(unittest.TestCase):
    def test_bonferroni_divides_alpha_by_the_family_size(self):
        result = bonferroni([0.01, 0.02, 0.04, 0.2], alpha=0.05)
        self.assertAlmostEqual(result.threshold, 0.0125, places=9)
        self.assertEqual(result.rejected, [True, False, False, False])

    def test_benjamini_hochberg_is_less_conservative(self):
        p_values = [0.001, 0.008, 0.02, 0.04, 0.5]
        bh = benjamini_hochberg(p_values, alpha=0.05)
        bf = bonferroni(p_values, alpha=0.05)
        self.assertGreaterEqual(sum(bh.rejected), sum(bf.rejected))

    def test_benjamini_hochberg_rejects_the_whole_prefix(self):
        # A p-value below the cutoff rank is rejected even if its own threshold
        # would not have admitted it — the step-up property.
        result = benjamini_hochberg([0.01, 0.02, 0.03, 0.04, 0.05], alpha=0.05)
        self.assertEqual(result.rejected, [True] * 5)

    def test_order_is_preserved_in_the_output(self):
        result = benjamini_hochberg([0.5, 0.001], alpha=0.05)
        self.assertEqual(result.rejected, [False, True])

    def test_empty_family_is_not_an_error(self):
        self.assertEqual(bonferroni([]).rejected, [])
        self.assertEqual(benjamini_hochberg([]).rejected, [])


if __name__ == "__main__":
    unittest.main()
