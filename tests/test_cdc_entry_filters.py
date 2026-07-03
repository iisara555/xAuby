"""CDC per-side RSI bands and entry-thrust filter."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from xauby.strategies.cdc_action_zone.strategy import CDCActionZoneStrategy
from xauby.strategies.context import MarketContext

PATCH = "xauby.strategies.cdc_action_zone.strategy.compute_indicators"


def _df() -> pd.DataFrame:
    n = 120
    return pd.DataFrame(
        {
            "open": [100.0] * n,
            "high": [100.0] * n,
            "low": [100.0] * n,
            "close": [100.0] * n,
            "volume": [1.0] * n,
            "timestamp": list(range(n)),
        }
    )


def _ctx(**config_over) -> MarketContext:
    config = {
        "enable_short": True,
        "require_fresh_zone": True,
        "fresh_zone_window": 1,
        "rsi_min": 0.0,
        "rsi_max": 100.0,
        "vol_min_ratio": 0.0,
        "use_d1_regime_filter": False,
        "disable_stop_loss": True,
    }
    config.update(config_over)
    return MarketContext(
        symbol="XAUUSDT",
        timeframe_primary="4h",
        df_primary=_df(),
        current_price=100.0,
        has_position=False,
        config=config,
        extras={"last_bar_is_forming": False},
    )


def _long_ind(**over):
    base = {
        "atr_4h": 1.0,
        "close_4h": 100.0,
        "cdc_zone_d1": "OFF",
        "cdc_zone_4h": "GREEN",
        "cdc_zone_4h_prev": "RED",
        "cdc_zone_4h_green_streak": 1,
        "cdc_zone_4h_red_streak": 0,
        "rsi_4h": 60.0,
        "volume_ratio_4h": 1.0,
        "ema_fast_4h": 101.0,
        "ema_slow_4h": 100.0,
        "ema_fast_4h_prev": 99.0,
        "ema_slow_4h_prev": 100.0,
        "ema_slow_4h_slope": None,
        "bar_close_pos_4h": 0.8,
    }
    base.update(over)
    return base


def _short_ind(**over):
    base = _long_ind(
        cdc_zone_4h="RED",
        cdc_zone_4h_prev="GREEN",
        cdc_zone_4h_green_streak=0,
        cdc_zone_4h_red_streak=1,
        ema_fast_4h=99.0,
        ema_slow_4h=100.0,
        ema_fast_4h_prev=101.0,
        ema_slow_4h_prev=100.0,
        rsi_4h=40.0,
        bar_close_pos_4h=0.2,
    )
    base.update(over)
    return base


class TestShortRsiBand(unittest.TestCase):
    def setUp(self):
        self.strat = CDCActionZoneStrategy()

    def test_short_uses_dedicated_band(self):
        # Long-biased shared band (45-70) would block RSI 40; the short band allows it.
        ctx = _ctx(rsi_min=45.0, rsi_max=70.0, rsi_short_min=25.0, rsi_short_max=55.0)
        with patch(PATCH, return_value=_short_ind(rsi_4h=40.0)):
            sig = self.strat.analyze(ctx)
        self.assertEqual(sig.intent, "OPEN")
        self.assertTrue(sig.is_short)

    def test_short_band_blocks_out_of_range(self):
        ctx = _ctx(rsi_short_min=25.0, rsi_short_max=55.0)
        with patch(PATCH, return_value=_short_ind(rsi_4h=60.0)):
            sig = self.strat.analyze(ctx)
        self.assertEqual(sig.action, "HOLD")
        self.assertIn("short", sig.reason)

    def test_short_falls_back_to_shared_band(self):
        ctx = _ctx(rsi_min=45.0, rsi_max=70.0)  # no short band configured
        with patch(PATCH, return_value=_short_ind(rsi_4h=40.0)):
            sig = self.strat.analyze(ctx)
        self.assertEqual(sig.action, "HOLD")

    def test_long_unaffected_by_short_band(self):
        ctx = _ctx(rsi_short_min=25.0, rsi_short_max=55.0)
        with patch(PATCH, return_value=_long_ind(rsi_4h=60.0)):
            sig = self.strat.analyze(ctx)
        self.assertEqual(sig.action, "BUY")


class TestEntryThrust(unittest.TestCase):
    def setUp(self):
        self.strat = CDCActionZoneStrategy()

    def test_long_needs_strong_close(self):
        ctx = _ctx(entry_thrust_min=0.6)
        with patch(PATCH, return_value=_long_ind(bar_close_pos_4h=0.4)):
            sig = self.strat.analyze(ctx)
        self.assertEqual(sig.action, "HOLD")
        self.assertIn("Weak close for long", sig.reason)
        with patch(PATCH, return_value=_long_ind(bar_close_pos_4h=0.75)):
            sig = self.strat.analyze(ctx)
        self.assertEqual(sig.action, "BUY")

    def test_short_needs_weak_close(self):
        ctx = _ctx(entry_thrust_min=0.6)
        with patch(PATCH, return_value=_short_ind(bar_close_pos_4h=0.7)):
            sig = self.strat.analyze(ctx)
        self.assertEqual(sig.action, "HOLD")
        self.assertIn("Weak close for short", sig.reason)
        with patch(PATCH, return_value=_short_ind(bar_close_pos_4h=0.2)):
            sig = self.strat.analyze(ctx)
        self.assertEqual(sig.intent, "OPEN")
        self.assertTrue(sig.is_short)

    def test_missing_close_pos_passes(self):
        ctx = _ctx(entry_thrust_min=0.6)
        with patch(PATCH, return_value=_long_ind(bar_close_pos_4h=None)):
            sig = self.strat.analyze(ctx)
        self.assertEqual(sig.action, "BUY")

    def test_disabled_by_default(self):
        with patch(PATCH, return_value=_long_ind(bar_close_pos_4h=0.05)):
            sig = self.strat.analyze(_ctx())
        self.assertEqual(sig.action, "BUY")


class TestClosePosIndicator(unittest.TestCase):
    def test_compute_close_position(self):
        from xauby.strategies.cdc_action_zone.indicators import compute_indicators

        n = 120
        df = pd.DataFrame(
            {
                "open": [100.0] * n,
                "high": [110.0] * n,
                "low": [90.0] * n,
                "close": [105.0] * n,
                "volume": [1.0] * n,
                "timestamp": list(range(n)),
            }
        )
        res = compute_indicators(df, None, last_bar_is_forming=False)
        self.assertAlmostEqual(res["bar_close_pos_4h"], 0.75)

    def test_zero_range_bar_is_none(self):
        from xauby.strategies.cdc_action_zone.indicators import compute_indicators

        n = 120
        df = pd.DataFrame(
            {
                "open": [100.0] * n,
                "high": [100.0] * n,
                "low": [100.0] * n,
                "close": [100.0] * n,
                "volume": [1.0] * n,
                "timestamp": list(range(n)),
            }
        )
        res = compute_indicators(df, None, last_bar_is_forming=False)
        self.assertIsNone(res["bar_close_pos_4h"])


if __name__ == "__main__":
    unittest.main()
