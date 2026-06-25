"""Confidence filter gating after sufficient regime history."""
import unittest

from xauby.engine.regime_debounce import RegimeDebouncer
from xauby.engine.regime_router import RegimeRouter
from xauby.engine.symbol_context import SymbolContext
from xauby.regime.models import GoldRegime
from xauby.runtime.pair_registry import PairSpec


class TestConfidenceFilter(unittest.TestCase):
    def test_low_confidence_blocks_switch_when_filter_on(self):
        cfg = {
            "architecture": {"regime_confidence_filter": True},
            "regime_router": {
                "confidence_threshold": 0.7,
                "mapping": {"BULL_BREAKOUT": "cdc_action_zone"},
            },
        }
        router = RegimeRouter(cfg, debouncer=RegimeDebouncer(1), history_count_fn=lambda: 30)
        sc = SymbolContext(symbol="BTCUSDT", strategy_name="supertrend_ema200")
        sc.confirmed_regime = "SIDEWAYS_CHOP"
        spec = PairSpec(symbol="BTCUSDT", strategy_name="supertrend_ema200")
        route = router.evaluate(
            "BTCUSDT",
            GoldRegime(
                regime="BULL_BREAKOUT",
                trend="NEUTRAL",
                volatility="NORMAL",
                macro_bias="NEUTRAL",
                confidence=0.4,
            ),
            sc,
            spec,
            has_open_position=False,
        )
        self.assertFalse(route.strategy_changed)
        self.assertIn("blocked", route.message.lower())

    def test_low_confidence_does_not_block_no_trade_protection(self):
        # A low-confidence bear/panic read maps to NO_TRADE (target None). The
        # confidence filter must NOT suppress entering protection — safety wins.
        cfg = {
            "architecture": {"regime_confidence_filter": True},
            "regime_router": {
                "confidence_threshold": 0.7,
                "mapping": {"PANIC_SELL": None, "BULL_BREAKOUT": "cdc_action_zone"},
            },
        }
        router = RegimeRouter(cfg, debouncer=RegimeDebouncer(1), history_count_fn=lambda: 30)
        sc = SymbolContext(symbol="BTCUSDT", strategy_name="donchian_trend")
        sc.confirmed_regime = "BULL_TREND_WEAK"
        spec = PairSpec(symbol="BTCUSDT", strategy_name="donchian_trend")
        route = router.evaluate(
            "BTCUSDT",
            GoldRegime(
                regime="PANIC_SELL",
                trend="BEARISH",
                volatility="HIGH",
                macro_bias="RISK-OFF",
                confidence=0.4,  # below threshold, but protection must still fire
            ),
            sc,
            spec,
            has_open_position=False,
        )
        self.assertTrue(route.no_trade)
        self.assertNotIn("blocked", route.message.lower())


if __name__ == "__main__":
    unittest.main()
