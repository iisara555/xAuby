"""CDC slow-EMA slope filter: blocks counter-slope entries when enabled."""
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
        "require_slow_slope": True,
        "slow_slope_bars": 3,
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


def _ind(**over):
    base = {
        "atr_4h": 1.0,
        "close_4h": 100.0,
        "cdc_zone_d1": "OFF",
        "cdc_zone_4h": "GREEN",
        "cdc_zone_4h_prev": "RED",
        "cdc_zone_4h_green_streak": 1,
        "cdc_zone_4h_red_streak": 0,
        "rsi_4h": 50.0,
        "volume_ratio_4h": 1.0,
        "ema_fast_4h": 101.0,
        "ema_slow_4h": 100.0,
        "ema_fast_4h_prev": 99.0,
        "ema_slow_4h_prev": 100.0,
        "ema_slow_4h_slope": 0.5,
    }
    base.update(over)
    return base


def _short_ind(**over):
    base = _ind(
        cdc_zone_4h="RED",
        cdc_zone_4h_prev="GREEN",
        cdc_zone_4h_green_streak=0,
        cdc_zone_4h_red_streak=1,
        ema_fast_4h=99.0,
        ema_slow_4h=100.0,
        ema_fast_4h_prev=101.0,
        ema_slow_4h_prev=100.0,
        ema_slow_4h_slope=-0.5,
    )
    base.update(over)
    return base


class TestSlopeFilter(unittest.TestCase):
    def setUp(self):
        self.strat = CDCActionZoneStrategy()

    def test_long_allowed_when_slope_rising(self):
        with patch(PATCH, return_value=_ind(ema_slow_4h_slope=0.5)):
            sig = self.strat.analyze(_ctx())
        self.assertEqual(sig.action, "BUY")

    def test_long_blocked_when_slope_falling(self):
        with patch(PATCH, return_value=_ind(ema_slow_4h_slope=-0.5)):
            sig = self.strat.analyze(_ctx())
        self.assertEqual(sig.action, "HOLD")
        self.assertIn("slope not rising", sig.reason)

    def test_short_allowed_when_slope_falling(self):
        with patch(PATCH, return_value=_short_ind()):
            sig = self.strat.analyze(_ctx())
        # open_short() carries action SELL with intent OPEN + side SHORT.
        self.assertEqual(sig.action, "SELL")
        self.assertEqual(sig.intent, "OPEN")
        self.assertTrue(sig.is_short)

    def test_short_blocked_when_slope_rising(self):
        with patch(PATCH, return_value=_short_ind(ema_slow_4h_slope=0.5)):
            sig = self.strat.analyze(_ctx())
        self.assertEqual(sig.action, "HOLD")
        self.assertIn("slope not falling", sig.reason)

    def test_missing_slope_data_passes(self):
        with patch(PATCH, return_value=_ind(ema_slow_4h_slope=None)):
            sig = self.strat.analyze(_ctx())
        self.assertEqual(sig.action, "BUY")

    def test_filter_off_by_default(self):
        with patch(PATCH, return_value=_ind(ema_slow_4h_slope=-0.5)):
            sig = self.strat.analyze(_ctx(require_slow_slope=False))
        self.assertEqual(sig.action, "BUY")


if __name__ == "__main__":
    unittest.main()
