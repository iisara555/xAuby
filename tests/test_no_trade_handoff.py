"""RegimeRouter NO_TRADE entry blocking and live gate tests."""
import unittest

from xauby.engine.regime_router import RegimeRouter
from xauby.engine.symbol_context import SymbolContext
from xauby.regime.models import GoldRegime
from xauby.runtime.pair_registry import PairRegistry, PairSpec


class TestNoTradeHandoff(unittest.TestCase):
    def test_blocks_new_entries_in_no_trade(self):
        sc = SymbolContext(symbol="BTCUSDT")
        sc.no_trade_state = "NO_TRADE"
        self.assertTrue(sc.blocks_new_entries())
        sc.no_trade_state = "HANDOFF"
        self.assertTrue(sc.blocks_new_entries())

    def test_live_gate_persists_forced_sim_in_registry(self):
        cfg = {
            "architecture": {"whitelist_strict": True},
            "data": {"hybrid_dynamic_coin_config": {}},
        }
        registry = PairRegistry(cfg, project_root=".")
        spec = PairSpec(
            symbol="BTCUSDT",
            strategy_name="supertrend_ema200",
            execution_mode="live",
            regime_router_enabled=True,
            regime_router_live_confirmed=False,
        )
        registry.update_spec(spec)
        adjusted = RegimeRouter.apply_live_confirmation(registry.get("BTCUSDT"))
        registry.update_spec(adjusted)
        stored = registry.get("BTCUSDT")
        self.assertEqual(stored.execution_mode, "sim")
        self.assertTrue(stored.degraded)

    def _switch_regime(self, cfg, *, has_open_position):
        router = RegimeRouter(cfg, history_count_fn=lambda: 0)
        sc = SymbolContext(symbol="BTCUSDT", strategy_name="cdc_action_zone")
        sc.confirmed_regime = "BULL_BREAKOUT"
        spec = PairSpec(symbol="BTCUSDT", strategy_name="cdc_action_zone")
        regime = GoldRegime(
            regime="VOLATILITY_EXPANSION",
            trend="NEUTRAL",
            volatility="HIGH",
            macro_bias="NEUTRAL",
            confidence=0.9,
        )
        switched_on = None
        for candle in range(1, 6):
            route = router.evaluate(
                "BTCUSDT", regime, sc, spec, has_open_position=has_open_position
            )
            if route.strategy_changed:
                switched_on = candle
                break
        return switched_on

    def test_instant_switch_when_flat(self):
        cfg = {
            "regime_router": {
                "mapping": {
                    "BULL_BREAKOUT": "cdc_action_zone",
                    "VOLATILITY_EXPANSION": "supertrend_ema200",
                },
                "debounce_candles": 3,
            }
        }
        # Flat: switch on the very first confirming candle.
        self.assertEqual(self._switch_regime(cfg, has_open_position=False), 1)

    def test_debounce_kept_when_position_open(self):
        cfg = {
            "regime_router": {
                "mapping": {
                    "BULL_BREAKOUT": "cdc_action_zone",
                    "VOLATILITY_EXPANSION": "supertrend_ema200",
                },
                "debounce_candles": 3,
            }
        }
        # With an open position the 3-candle debounce still applies; the switch
        # only becomes a real strategy change via the handoff path once flat,
        # so strategy_changed must not fire before the debounce completes.
        switched = self._switch_regime(cfg, has_open_position=True)
        self.assertIsNone(switched)

    def test_instant_switch_disabled_via_config(self):
        cfg = {
            "regime_router": {
                "mapping": {
                    "BULL_BREAKOUT": "cdc_action_zone",
                    "VOLATILITY_EXPANSION": "supertrend_ema200",
                },
                "debounce_candles": 3,
                "instant_switch_when_flat": False,
            }
        }
        # Flat but instant switching disabled → must wait out the debounce.
        self.assertEqual(self._switch_regime(cfg, has_open_position=False), 3)

    def test_force_close_after_six_candles(self):
        cfg = {
            "regime_router": {
                "mapping": {"PANIC_SELL": None},
                "force_close_candles": 6,
                "debounce_candles": 1,
            }
        }
        router = RegimeRouter(cfg, history_count_fn=lambda: 0)
        sc = SymbolContext(symbol="BTCUSDT", strategy_name="cdc_action_zone")
        sc.no_trade_state = "NO_TRADE"
        sc.no_trade_candle_count = 6
        spec = PairSpec(symbol="BTCUSDT", strategy_name="cdc_action_zone")
        route = router.evaluate(
            "BTCUSDT",
            GoldRegime(
                regime="PANIC_SELL",
                trend="NEUTRAL",
                volatility="HIGH",
                macro_bias="NEUTRAL",
                confidence=0.9,
            ),
            sc,
            spec,
            has_open_position=True,
        )
        self.assertTrue(route.force_close)


if __name__ == "__main__":
    unittest.main()
