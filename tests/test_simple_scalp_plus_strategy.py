"""Tests for the SimpleScalp+ confluence scalping strategy plugin."""
import unittest

import numpy as np
import pandas as pd

from xauby.strategies.registry import load_strategy
from xauby.strategies.context import MarketContext
from xauby.strategies.signal import ACTION_BUY, ACTION_SELL, ACTION_HOLD


def _frame(start: float, end: float, n: int = 220, noise: float = 0.25, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base = np.linspace(start, end, n) + rng.normal(0, noise, n)
    return pd.DataFrame(
        {
            "open": base,
            "high": base + 0.6,
            "low": base - 0.6,
            "close": base,
            "volume": np.linspace(1000, 3000, n),
        }
    )


class TestSimpleScalpPlusStrategy(unittest.TestCase):
    def setUp(self) -> None:
        self.strat = load_strategy("simple_scalp_plus", {})

    def _ctx(self, df, **kw) -> MarketContext:
        return MarketContext(
            symbol="BTCUSDT",
            timeframe_primary="5m",
            df_primary=df,
            current_price=float(df["close"].iloc[-1]),
            **kw,
        )

    def test_uptrend_emits_buy_with_atr_stop(self):
        sig = self.strat.analyze(self._ctx(_frame(100, 160)))
        self.assertEqual(sig.action, ACTION_BUY)
        self.assertGreaterEqual(sig.indicators["buy_count"], 4)
        self.assertIsNotNone(sig.stop_loss_distance)
        self.assertGreater(sig.stop_loss_distance, 0)
        self.assertGreaterEqual(sig.confidence, 0.70)

    def test_downtrend_flat_holds_without_position(self):
        sig = self.strat.analyze(self._ctx(_frame(160, 100)))
        self.assertEqual(sig.action, ACTION_HOLD)

    def test_downtrend_with_long_position_sells(self):
        sig = self.strat.analyze(
            self._ctx(_frame(160, 100), has_position=True, position_side="LONG")
        )
        self.assertEqual(sig.action, ACTION_SELL)
        self.assertGreaterEqual(sig.indicators["sell_count"], 3)

    def test_uptrend_holds_long_no_churn(self):
        # Regression: direction-neutral ADX/Volume must NOT count toward the
        # long-exit, else the strategy churns out of a healthy uptrend instead
        # of riding it. A clean uptrend with an open long must HOLD, not SELL.
        sig = self.strat.analyze(
            self._ctx(_frame(100, 160, noise=0.15), has_position=True, position_side="LONG")
        )
        self.assertEqual(sig.action, ACTION_HOLD)
        self.assertLess(sig.indicators["sell_count"], 3)

    def test_sl_confirmed_forces_sell(self):
        sig = self.strat.analyze(
            self._ctx(_frame(100, 160), has_position=True, position_side="LONG", sl_confirmed=True)
        )
        self.assertEqual(sig.action, ACTION_SELL)
        self.assertIn("SL confirmed", sig.reason)

    def test_mtf_filter_halves_confidence_when_macro_bearish(self):
        df = _frame(100, 160)
        bear_regime = _frame(200, 120, n=80)
        base = self.strat.analyze(self._ctx(df))
        gated = self.strat.analyze(
            self._ctx(df, timeframe_regime="15m", df_regime=bear_regime)
        )
        self.assertEqual(base.action, ACTION_BUY)
        # Bearish macro must lower confidence vs the no-regime case.
        self.assertLess(gated.confidence, base.confidence)
        if gated.action == ACTION_BUY:
            self.assertIn("MTF misaligned", gated.reason)

    def test_require_macro_bull_hard_gate_blocks_counter_trend(self):
        # With the hard trend gate on, a bullish micro setup must be blocked
        # when the higher-timeframe (regime) trend is bearish.
        strat = load_strategy("simple_scalp_plus", {"require_macro_bull": True})
        bear_regime = _frame(200, 120, n=80)
        sig = strat.analyze(
            self._ctx(_frame(100, 160), timeframe_regime="1h", df_regime=bear_regime)
        )
        self.assertEqual(sig.action, ACTION_HOLD)
        self.assertIn("blocked", sig.reason)
        # Same setup WITHOUT a regime (macro unknown) must NOT be blocked.
        sig2 = strat.analyze(self._ctx(_frame(100, 160)))
        self.assertEqual(sig2.action, ACTION_BUY)

    def test_high_confirmation_threshold_blocks_entry(self):
        strict = load_strategy("simple_scalp_plus", {"min_confirmations_buy": 8})
        sig = strict.analyze(self._ctx(_frame(100, 160)))
        self.assertEqual(sig.action, ACTION_HOLD)

    def test_insufficient_bars_holds(self):
        sig = self.strat.analyze(self._ctx(_frame(100, 110, n=30)))
        self.assertEqual(sig.action, ACTION_HOLD)

    def test_validate_config_flags_bad_ranges(self):
        bad = load_strategy(
            "simple_scalp_plus", {"rsi_buy_min": 70, "rsi_buy_max": 30, "ema_fast": 30, "ema_slow": 9}
        )
        warnings = bad.validate_config()
        self.assertTrue(any("rsi_buy_min must" in w for w in warnings))
        self.assertTrue(any("ema_fast must" in w for w in warnings))
        self.assertTrue(bad.config_errors())  # "must" -> fatal under strict mode

    def test_not_research_tagged(self):
        self.assertNotIn("research", self.strat.tags)


if __name__ == "__main__":
    unittest.main()
