"""RegimeRouter mapping and debounce tests."""
import unittest

from xauby.engine.regime_debounce import RegimeDebouncer
from xauby.engine.regime_router import RegimeRouter
from xauby.engine.symbol_context import SymbolContext
from xauby.regime.models import GoldRegime
from xauby.runtime.pair_registry import PairSpec


def _regime(label: str, confidence: float = 0.8) -> GoldRegime:
    return GoldRegime(
        regime=label,
        trend="NEUTRAL",
        volatility="NORMAL",
        macro_bias="NEUTRAL",
        confidence=confidence,
        reasons=[],
    )


class TestRegimeRouterMapping(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "regime_router": {
                "mapping": {
                    "BULL_BREAKOUT": "cdc_action_zone",
                    "PANIC_SELL": None,
                }
            }
        }
        self.router = RegimeRouter(self.cfg, history_count_fn=lambda: 0)

    def test_debounce_requires_three_candles(self):
        deb = RegimeDebouncer(threshold=3)
        sc = SymbolContext(symbol="BTCUSDT")
        for i in range(2):
            r = deb.update(sc, "BULL_BREAKOUT")
            self.assertFalse(r.confirmed)
        r = deb.update(sc, "BULL_BREAKOUT")
        self.assertTrue(r.confirmed)

    def test_maps_bull_to_cdc(self):
        sc = SymbolContext(symbol="BTCUSDT", strategy_name="supertrend_ema200")
        sc.confirmed_regime = "SIDEWAYS_CHOP"
        deb = RegimeDebouncer(threshold=1)
        router = RegimeRouter(self.cfg, debouncer=deb, history_count_fn=lambda: 0)
        spec = PairSpec(symbol="BTCUSDT", strategy_name="supertrend_ema200")
        route = router.evaluate("BTCUSDT", _regime("BULL_BREAKOUT"), sc, spec, has_open_position=False)
        self.assertTrue(route.strategy_changed)
        self.assertEqual(route.strategy_name, "xauby_actionzone")

    def test_no_trade_regime(self):
        sc = SymbolContext(symbol="BTCUSDT", strategy_name="cdc_action_zone")
        sc.confirmed_regime = "BULL_BREAKOUT"
        deb = RegimeDebouncer(threshold=1)
        router = RegimeRouter(self.cfg, debouncer=deb, history_count_fn=lambda: 0)
        spec = PairSpec(symbol="BTCUSDT", strategy_name="cdc_action_zone")
        route = router.evaluate("BTCUSDT", _regime("PANIC_SELL"), sc, spec, has_open_position=False)
        self.assertTrue(route.no_trade)


if __name__ == "__main__":
    unittest.main()
