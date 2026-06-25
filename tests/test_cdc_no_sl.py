import unittest
from types import SimpleNamespace

import yaml

from xauby.observability.replay import PositionSimulator
from xauby.runtime.trading_config import resolve_trading_config
from xauby.strategies.cdc_action_zone.strategy import CDCActionZoneStrategy
from xauby.strategies.signal import buy, hold, sell


class TestCdcNoStopLossConfig(unittest.TestCase):
    def test_xaut_effective_config_enables_cdc_pure_mode(self):
        with open("bot_config.yaml", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)

        effective = resolve_trading_config(
            cfg,
            strategy_name="cdc_action_zone",
            symbol="XAUTUSDT",
            for_live=True,
        )

        self.assertTrue(effective.strategy["require_fresh_zone"])
        self.assertEqual(effective.strategy["fresh_zone_window"], 1)
        self.assertTrue(effective.strategy["disable_stop_loss"])
        self.assertEqual(effective.strategy["position_pct"], 1.0)
        self.assertEqual(effective.strategy["fixed_tp_pct"], 0.0)
        self.assertFalse(effective.strategy["breakeven_sl_enabled"])

    @staticmethod
    def _checklist(*, rsi_min, rsi_max, vol_min_ratio, use_d1_regime):
        strategy = CDCActionZoneStrategy()
        indicators = {
            "cdc_zone_4h": "GREEN",
            "cdc_zone_d1": "GREEN",
            "rsi_4h": 55.0,
            "volume_ratio_4h": 1.2,
            "ema_fast_4h": 101.0,
            "ema_slow_4h": 100.0,
            "ema_fast_4h_prev": 99.0,
            "ema_slow_4h_prev": 100.0,
            "cdc_zone_4h_green_streak": 1,
        }
        return strategy._build_checklist(
            ctx=SimpleNamespace(has_position=False),
            indicators=indicators,
            rsi_min=rsi_min,
            rsi_max=rsi_max,
            vol_min_ratio=vol_min_ratio,
            use_d1_regime=use_d1_regime,
            require_fresh_zone=True,
            fresh_zone_window=1,
        )

    def test_cdc_pure_checklist_hides_disabled_filters(self):
        items = self._checklist(
            rsi_min=0.0,
            rsi_max=100.0,
            vol_min_ratio=0.0,
            use_d1_regime=False,
        )

        self.assertEqual(
            [item["label"] for item in items],
            ["EMA Bullish", "EMA Cross", "4H Zone", "Fresh"],
        )

    def test_filtered_cdc_checklist_keeps_enabled_filters(self):
        items = self._checklist(
            rsi_min=45.0,
            rsi_max=70.0,
            vol_min_ratio=1.0,
            use_d1_regime=True,
        )
        labels = [item["label"] for item in items]

        self.assertIn("RSI(14)", labels)
        self.assertIn("Vol Ratio", labels)
        self.assertIn("D1 Regime", labels)


class TestCdcNoStopLossSimulator(unittest.TestCase):
    def test_fixed_fraction_has_no_stop_or_trailing_exit(self):
        sim = PositionSimulator(
            initial_balance=1000.0,
            fee_pct=0.001,
            slippage_bps=10.0,
            disable_stop_loss=True,
            position_pct=0.5,
            fixed_tp_pct=0.0,
        )

        opened = sim.try_open(
            entry_price=100.0,
            atr=2.0,
            bar_time=1,
            signal=buy("fresh green", volatility=2.0),
        )

        self.assertTrue(opened)
        self.assertAlmostEqual(sim.position.entry_price, 100.1)
        self.assertAlmostEqual(sim.position.qty * sim.position.entry_price, 500.0)
        self.assertEqual(sim.position.stop_loss, 0.0)

        events = sim.on_bar(
            open_p=90.0,
            high=150.0,
            low=50.0,
            close=90.0,
            atr=10.0,
            zone="GREEN",
            bar_time=2,
            signal=hold("still green"),
        )
        self.assertTrue(sim.has_position)
        self.assertNotIn("stop_loss_updated", [event["event_type"] for event in events])

        sim.on_bar(
            open_p=90.0,
            high=91.0,
            low=89.0,
            close=90.0,
            atr=10.0,
            zone="RED",
            bar_time=3,
            signal=sell("red zone"),
        )
        self.assertFalse(sim.has_position)
        self.assertEqual(sim.trades[0].trigger, "SIGNAL")


if __name__ == "__main__":
    unittest.main()
