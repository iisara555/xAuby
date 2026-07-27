"""The optimizer must decline rather than rank noise. Roadmap P1.2.

Measured on the shipped config (max_bars 300, split 0.7, warmup 100), neither
live pair could support a selection, and neither failure was visible:

* supertrend_ema200 needs 240 bars before it emits a signal. Both windows are
  shorter (210 in-sample, 190 out-of-sample), so every combination scored
  exactly 0.0, the "profitable in both windows" filter matched nothing, and the
  winner was whichever tuple sorted first — then saved as the pair's best
  parameters.
* xauby_actionzone produced one trade per window, and candidates were ranked on
  an annualised Sharpe computed from that single trade.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xauby.backtest.optimizer import (  # noqa: E402
    MIN_IS_TRADES,
    MIN_OOS_TRADES,
    OptimizerVerdict,
    _admissibility,
)


class _Bundle:
    def __init__(self, bars, strategy_name="xauby_actionzone", cfg=None):
        import pandas as pd

        self.df = pd.DataFrame({"close": [1.0] * bars})
        self.merged_cfg = cfg or {"backtest": {"optimizer": {
            "oos_split_ratio": 0.7, "oos_warmup_bars": 100}}}
        self.strategy_name = strategy_name


def _entry(is_trades, oos_trades):
    return {"is_trades": is_trades, "oos_trades": oos_trades,
            "is_net_profit_pct": 1.0, "oos_net_profit_pct": 1.0}


class TestDeclines(unittest.TestCase):
    def test_no_results_is_not_a_selection(self):
        verdict = _admissibility(_Bundle(400), [], True, 4)
        self.assertFalse(verdict.admissible)
        self.assertIn("no combination", verdict.reason)

    def test_a_full_window_ranking_is_not_out_of_sample(self):
        # Scored on the same bars it was tuned on.
        verdict = _admissibility(_Bundle(200), [_entry(99, 99)], False, 4)
        self.assertFalse(verdict.admissible)
        self.assertIn("out-of-sample", verdict.reason)

    def test_a_strategy_that_cannot_warm_up_in_the_window_is_refused(self):
        # supertrend_ema200: min_bars 240 against a 210-bar in-sample window.
        verdict = _admissibility(_Bundle(300, "supertrend_ema200"),
                                 [_entry(0, 0)], True, 4)
        self.assertFalse(verdict.admissible)
        self.assertEqual(verdict.strategy_min_bars, 240)
        self.assertEqual(verdict.is_bars, 210)
        self.assertIn("whichever sorted first", verdict.reason)

    def test_one_trade_per_window_is_refused(self):
        # xauby_actionzone on 300 bars. A Sharpe from a single trade must not
        # decide which parameters go live.
        verdict = _admissibility(_Bundle(300), [_entry(1, 1)], True, 4)
        self.assertFalse(verdict.admissible)
        self.assertIn(str(MIN_IS_TRADES), verdict.reason)
        self.assertIn(str(MIN_OOS_TRADES), verdict.reason)
        self.assertEqual(verdict.best_is_trades, 1)
        self.assertEqual(verdict.best_oos_trades, 1)

    def test_enough_in_sample_but_not_out_of_sample_is_still_refused(self):
        verdict = _admissibility(_Bundle(4000), [_entry(120, 5)], True, 4)
        self.assertFalse(verdict.admissible)

    def test_the_best_combination_sets_the_bar_not_the_first(self):
        results = [_entry(1, 1), _entry(80, 40), _entry(2, 2)]
        verdict = _admissibility(_Bundle(4000), results, True, 4)
        self.assertTrue(verdict.admissible)
        self.assertEqual(verdict.best_is_trades, 80)
        self.assertEqual(verdict.best_oos_trades, 40)


class TestAccepts(unittest.TestCase):
    def test_a_sample_that_clears_both_minimums_is_admissible(self):
        verdict = _admissibility(_Bundle(4000), [_entry(60, 25)], True, 12)
        self.assertTrue(verdict.admissible)
        self.assertIn("best of 12 trials", verdict.reason)

    def test_exactly_at_the_threshold_passes(self):
        verdict = _admissibility(
            _Bundle(4000), [_entry(MIN_IS_TRADES, MIN_OOS_TRADES)], True, 4)
        self.assertTrue(verdict.admissible)

    def test_one_below_the_threshold_does_not(self):
        verdict = _admissibility(
            _Bundle(4000), [_entry(MIN_IS_TRADES, MIN_OOS_TRADES - 1)], True, 4)
        self.assertFalse(verdict.admissible)

    def test_the_trial_count_is_recorded(self):
        # The winner is the best of N, and N belongs with the result — it is
        # what a deflated Sharpe needs and what a reader needs.
        verdict = _admissibility(_Bundle(4000), [_entry(60, 25)], True, 144)
        self.assertEqual(verdict.trials, 144)
        self.assertEqual(verdict.to_dict()["trials"], 144)


class TestVerdictShape(unittest.TestCase):
    def test_to_dict_carries_the_thresholds_it_applied(self):
        data = OptimizerVerdict(admissible=False).to_dict()
        self.assertEqual(data["min_is_trades"], MIN_IS_TRADES)
        self.assertEqual(data["min_oos_trades"], MIN_OOS_TRADES)

    def test_thresholds_match_the_repos_own_sweep(self):
        # Taken from scripts/actionzone_wfa_sweep.py rather than invented, so
        # the optimizer admits on the same bar the research protocol uses.
        import re

        source = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "scripts", "actionzone_wfa_sweep.py"),
            encoding="utf-8").read()
        match = re.search(r"MIN_IS_TRADES,\s*MIN_OOS_TRADES\s*=\s*(\d+),\s*(\d+)",
                          source)
        self.assertIsNotNone(match, "sweep no longer declares its minimums")
        self.assertEqual((int(match.group(1)), int(match.group(2))),
                         (MIN_IS_TRADES, MIN_OOS_TRADES))


if __name__ == "__main__":
    unittest.main()
