"""Manual order preview sizing (xauby/saas/order_sizing.py).

Audit F-3: the manual preview used to risk-size off a synthetic mark*2% stop
distance for every non-CDC-pure pair, while the certified strategy stops off
its own ATR*sl_atr_mult — making a manual BTC preview ~1.5x larger than the
engine would open for the same signal. These are pure functions (no FastAPI
dependency) so they're covered directly.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.saas.order_sizing import resolve_pair_atr, risk_based_stop_distance


class TestResolvePairAtr(unittest.TestCase):
    def test_prefers_first_known_key_present(self):
        self.assertEqual(resolve_pair_atr({"atr": 120.5, "atr_4h": 999.0}), 120.5)

    def test_falls_back_to_strategy_specific_key(self):
        # xauby_actionzone (CDC) names its ATR indicator "atr_4h", not "atr".
        self.assertEqual(resolve_pair_atr({"atr_4h": 45.2}), 45.2)

    def test_ignores_zero_or_negative(self):
        self.assertEqual(resolve_pair_atr({"atr": 0.0, "atr_4h": 30.0}), 30.0)
        self.assertEqual(resolve_pair_atr({"atr": -5.0, "atr_4h": 30.0}), 30.0)

    def test_no_known_key_returns_zero(self):
        self.assertEqual(resolve_pair_atr({"ema": 100.0, "rsi": 55.0}), 0.0)

    def test_non_mapping_returns_zero(self):
        self.assertEqual(resolve_pair_atr(None), 0.0)
        self.assertEqual(resolve_pair_atr("not a dict"), 0.0)  # type: ignore[arg-type]

    def test_non_numeric_value_is_skipped(self):
        self.assertEqual(resolve_pair_atr({"atr": "n/a", "atr_4h": 12.0}), 12.0)


class TestRiskBasedStopDistance(unittest.TestCase):
    def test_uses_atr_times_mult_when_available(self):
        distance, basis = risk_based_stop_distance(mark_price=64000.0, atr=800.0, sl_atr_mult=3.0)
        self.assertEqual(distance, 2400.0)
        self.assertEqual(basis, "atr")

    def test_matches_btc_certified_config(self):
        # Live BTC preset: supertrend_ema200, atr_period=10, sl_atr_mult=3.0.
        distance, basis = risk_based_stop_distance(mark_price=64834.2, atr=612.3, sl_atr_mult=3.0)
        self.assertAlmostEqual(distance, 612.3 * 3.0, places=6)
        self.assertEqual(basis, "atr")

    def test_falls_back_to_fixed_pct_when_atr_missing(self):
        distance, basis = risk_based_stop_distance(mark_price=1000.0, atr=0.0, sl_atr_mult=3.0)
        self.assertEqual(distance, 20.0)  # 2% fallback
        self.assertEqual(basis, "fixed_pct")

    def test_falls_back_to_fixed_pct_when_sl_atr_mult_unset(self):
        # A preset that never declared sl_atr_mult -> don't assume one; use
        # the old fixed-percent heuristic rather than an invented multiplier.
        distance, basis = risk_based_stop_distance(mark_price=1000.0, atr=25.0, sl_atr_mult=0.0)
        self.assertEqual(distance, 20.0)
        self.assertEqual(basis, "fixed_pct")

    def test_custom_fallback_pct_is_honored(self):
        distance, basis = risk_based_stop_distance(
            mark_price=1000.0, atr=0.0, sl_atr_mult=3.0, fallback_pct=0.05,
        )
        self.assertEqual(distance, 50.0)
        self.assertEqual(basis, "fixed_pct")

    def test_never_negative_on_bad_mark_price(self):
        distance, basis = risk_based_stop_distance(mark_price=-10.0, atr=0.0, sl_atr_mult=3.0)
        self.assertEqual(distance, 0.0)
        self.assertEqual(basis, "fixed_pct")

    def test_atr_based_sizing_is_smaller_than_old_fixed_heuristic_for_btc(self):
        # This is the audit's concrete failure mode: BTC's real ATR-based stop
        # (3x ATR ~ 2%/ATR_ratio) vs the old synthetic 2% of mark. Assert the
        # new distance reflects actual volatility, not a blanket percentage.
        mark = 64834.2
        atr = 400.0  # a calmer ATR than the fixed 2% heuristic would assume
        distance, basis = risk_based_stop_distance(mark_price=mark, atr=atr, sl_atr_mult=3.0)
        old_fixed = mark * 0.02
        self.assertEqual(basis, "atr")
        self.assertLess(distance, old_fixed)  # calmer market -> smaller stop -> larger safe size


if __name__ == "__main__":
    unittest.main()
