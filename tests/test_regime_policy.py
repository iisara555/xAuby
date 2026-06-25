"""P2-1 tests: regime policy layer adjustments."""
import unittest
from types import SimpleNamespace

from xauby.engine.regime_policy import (
    apply_regime_policy,
    regime_policy_enabled,
)


def _regime(**kw):
    base = {
        "regime": "RANGE_NEUTRAL",
        "risk_state": "NORMAL",
        "trend_strength": "NEUTRAL",
        "volatility_state": "NORMAL",
    }
    base.update(kw)
    return SimpleNamespace(**base)


class TestRegimePolicy(unittest.TestCase):
    def test_disabled_by_default(self):
        self.assertFalse(regime_policy_enabled({}))
        self.assertFalse(regime_policy_enabled({"regime_policy": {}}))
        self.assertTrue(regime_policy_enabled({"regime_policy": {"enabled": True}}))

    def test_panic_regime_blocks_buy(self):
        d = apply_regime_policy(_regime(regime="PANIC_SELL"), "BUY")
        self.assertEqual(d.action, "HOLD")
        self.assertTrue(d.adjusted)

    def test_panic_risk_state_blocks_buy(self):
        d = apply_regime_policy(_regime(risk_state="PANIC_RISK"), "BUY")
        self.assertEqual(d.action, "HOLD")

    def test_panic_never_blocks_sell(self):
        d = apply_regime_policy(_regime(regime="PANIC_SELL"), "SELL")
        self.assertEqual(d.action, "SELL")
        self.assertFalse(d.adjusted)

    def test_choppy_halves_risk(self):
        d = apply_regime_policy(_regime(trend_strength="CHOPPY"), "BUY")
        self.assertEqual(d.action, "BUY")
        self.assertEqual(d.risk_pct_multiplier, 0.5)
        self.assertTrue(d.adjusted)

    def test_extreme_expansion_widens_sl(self):
        d = apply_regime_policy(_regime(volatility_state="EXTREME_EXPANSION"), "BUY")
        self.assertEqual(d.action, "BUY")
        self.assertEqual(d.sl_atr_mult_delta, 0.5)

    def test_normal_regime_untouched(self):
        d = apply_regime_policy(_regime(), "BUY")
        self.assertEqual(d.action, "BUY")
        self.assertEqual(d.risk_pct_multiplier, 1.0)
        self.assertEqual(d.sl_atr_mult_delta, 0.0)
        self.assertFalse(d.adjusted)

    def test_none_regime_untouched(self):
        d = apply_regime_policy(None, "BUY")
        self.assertEqual(d.action, "BUY")
        self.assertFalse(d.adjusted)


if __name__ == "__main__":
    unittest.main()
