"""Tests for the independent GMM regime cross-check (architecture item 4)."""
import unittest

import numpy as np

from xauby.regime.statistical import (
    DEFAULT_COMPONENTS,
    DEFAULT_MIN_BARS,
    FAMILY_DOWN,
    FAMILY_RANGE,
    FAMILY_UP,
    classify_statistical,
    crosscheck_features,
    rule_family,
)
from xauby.runtime.architecture_config import (
    regime_statistical_config,
    regime_statistical_crosscheck,
)


def _series(closes):
    return [{"close": float(c)} for c in closes]


def _uptrend(n=400, seed=0):
    rng = np.random.default_rng(seed)
    return np.cumprod(1 + 0.002 + rng.normal(0, 0.006, n)) * 1000.0


def _downtrend(n=400, seed=0):
    rng = np.random.default_rng(seed)
    return np.cumprod(1 - 0.002 + rng.normal(0, 0.006, n)) * 1000.0


def _quiet_range(n=400, seed=0):
    rng = np.random.default_rng(seed)
    return 1000.0 + rng.normal(0, 0.6, n)


class TestStatisticalLabelling(unittest.TestCase):
    def test_uptrend_labels_up(self):
        r = classify_statistical(_series(_uptrend()))
        self.assertTrue(r.available)
        self.assertEqual(r.family, FAMILY_UP)
        self.assertEqual(r.label, "STAT_TREND_UP")

    def test_downtrend_labels_down(self):
        r = classify_statistical(_series(_downtrend()))
        self.assertTrue(r.available)
        self.assertEqual(r.family, FAMILY_DOWN)

    def test_quiet_range_labels_range(self):
        r = classify_statistical(_series(_quiet_range()))
        self.assertTrue(r.available)
        self.assertEqual(r.family, FAMILY_RANGE)

    def test_posterior_and_stay_prob_are_probabilities(self):
        r = classify_statistical(_series(_uptrend()))
        self.assertGreaterEqual(r.posterior, 0.0)
        self.assertLessEqual(r.posterior, 1.0)
        self.assertGreaterEqual(r.stay_prob, 0.0)
        self.assertLessEqual(r.stay_prob, 1.0)


class TestStatisticalDegradation(unittest.TestCase):
    def test_insufficient_bars_unavailable(self):
        r = classify_statistical(_series(_uptrend(n=120)))
        self.assertFalse(r.available)
        self.assertIn("insufficient", r.reason)

    def test_bad_input_unavailable(self):
        r = classify_statistical([{"nope": 1.0}] * 300)
        self.assertFalse(r.available)

    def test_never_raises_on_degenerate_flat_series(self):
        # A perfectly flat series has zero volatility everywhere; must not crash.
        r = classify_statistical(_series([1000.0] * 400))
        self.assertIn(r.available, (True, False))


class TestStatisticalDeterminism(unittest.TestCase):
    def test_same_input_same_output(self):
        s = _series(_uptrend())
        a = classify_statistical(s)
        b = classify_statistical(s)
        self.assertEqual(a.label, b.label)
        self.assertEqual(a.cluster, b.cluster)
        self.assertAlmostEqual(a.posterior, b.posterior, places=9)


class TestCrossCheck(unittest.TestCase):
    def test_agreement_true_when_families_match(self):
        stat = classify_statistical(_series(_uptrend()))
        feats = crosscheck_features("BULL_TREND_STRONG", stat)
        self.assertTrue(feats["stat_agreement"])
        self.assertEqual(feats["stat_rule_family"], FAMILY_UP)

    def test_agreement_false_when_families_differ(self):
        stat = classify_statistical(_series(_uptrend()))
        feats = crosscheck_features("BEAR_TREND_STRONG", stat)
        self.assertFalse(feats["stat_agreement"])

    def test_agreement_unknown_when_stat_unavailable(self):
        stat = classify_statistical(_series(_uptrend(n=100)))
        feats = crosscheck_features("BULL_TREND_STRONG", stat)
        self.assertFalse(feats["stat_available"])
        self.assertEqual(feats["stat_agreement"], "unknown")

    def test_rule_family_mapping(self):
        self.assertEqual(rule_family("PANIC_SELL"), "HIGH_VOL")
        self.assertEqual(rule_family("SIDEWAYS_CHOP"), FAMILY_RANGE)
        self.assertEqual(rule_family("nonexistent"), "")


class TestStatisticalConfig(unittest.TestCase):
    def test_flag_defaults_off(self):
        self.assertFalse(regime_statistical_crosscheck({}))
        self.assertFalse(regime_statistical_crosscheck(None))

    def test_flag_reads_architecture_block(self):
        cfg = {"architecture": {"regime_statistical_crosscheck": True}}
        self.assertTrue(regime_statistical_crosscheck(cfg))

    def test_config_defaults(self):
        c = regime_statistical_config({})
        self.assertEqual(c["components"], DEFAULT_COMPONENTS)
        self.assertEqual(c["min_bars"], DEFAULT_MIN_BARS)

    def test_config_override_and_clamp(self):
        cfg = {"regime_statistical": {"components": 99, "min_bars": 10}}
        c = regime_statistical_config(cfg)
        self.assertEqual(c["components"], 8)    # clamped to max
        self.assertEqual(c["min_bars"], 50)     # clamped to min

    def test_config_bad_values_fall_back(self):
        cfg = {"regime_statistical": {"components": "x", "min_bars": None}}
        c = regime_statistical_config(cfg)
        self.assertEqual(c["components"], DEFAULT_COMPONENTS)
        self.assertEqual(c["min_bars"], DEFAULT_MIN_BARS)


if __name__ == "__main__":
    unittest.main()
