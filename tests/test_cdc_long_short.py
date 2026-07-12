"""CDC Action Zone stop-and-reverse: GREEN=long, RED=short (TradingView style).

Drives the strategy with a patched ``compute_indicators`` so the zone/EMA state
is deterministic, then asserts the long/short open/close signal semantics.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from xauby.strategies.cdc_action_zone.strategy import CDCActionZoneStrategy
from xauby.strategies.context import MarketContext
from xauby.domain.models import TradeIntent, PositionSide

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


def _ctx(*, has_position=False, position_side=None, stop_loss=0.0, current_price=100.0):
    return MarketContext(
        symbol="BTCUSDT",
        timeframe_primary="4h",
        df_primary=_df(),
        current_price=current_price,
        has_position=has_position,
        position_side=position_side,
        stop_loss=stop_loss,
        config={
            "enable_short": True,
            "require_fresh_zone": True,
            "fresh_zone_window": 1,
            "rsi_min": 0.0,
            "rsi_max": 100.0,
            "vol_min_ratio": 0.0,
            "use_d1_regime_filter": False,
            "disable_stop_loss": True,
        },
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
    }
    base.update(over)
    return base


class TestCdcLongShort(unittest.TestCase):
    def setUp(self):
        self.strat = CDCActionZoneStrategy()

    def test_fresh_green_opens_long(self):
        with patch(PATCH, return_value=_ind(cdc_zone_4h="GREEN", cdc_zone_4h_green_streak=1)):
            sig = self.strat.analyze(_ctx())
        self.assertEqual(sig.action, "BUY")
        self.assertEqual(sig.intent, TradeIntent.OPEN.value)
        self.assertEqual(sig.position_side, PositionSide.LONG.value)

    def test_fresh_red_opens_short(self):
        ind = _ind(
            cdc_zone_4h="RED",
            cdc_zone_4h_green_streak=0,
            cdc_zone_4h_red_streak=1,
            ema_fast_4h=99.0,
            ema_slow_4h=100.0,
            ema_fast_4h_prev=101.0,
            ema_slow_4h_prev=100.0,
        )
        with patch(PATCH, return_value=ind):
            sig = self.strat.analyze(_ctx())
        self.assertEqual(sig.action, "SELL")
        self.assertEqual(sig.intent, TradeIntent.OPEN.value)
        self.assertTrue(sig.is_short)
        by_label = {item["label"]: item for item in sig.checklist}
        self.assertTrue(by_label["EMA Bearish"]["ok"])
        self.assertTrue(by_label["EMA Cross"]["ok"])
        self.assertTrue(by_label["4H Zone"]["ok"])
        self.assertEqual(by_label["4H Zone"]["hint"], "Need RED")
        self.assertTrue(by_label["Fresh"]["ok"])

    def test_fresh_green_checklist_remains_long_directional(self):
        with patch(PATCH, return_value=_ind(cdc_zone_4h="GREEN", cdc_zone_4h_green_streak=1)):
            sig = self.strat.analyze(_ctx())
        by_label = {item["label"]: item for item in sig.checklist}
        self.assertTrue(by_label["EMA Bullish"]["ok"])
        self.assertTrue(by_label["4H Zone"]["ok"])
        self.assertEqual(by_label["4H Zone"]["hint"], "Need GREEN")

    def test_red_does_not_short_when_disabled(self):
        ctx = _ctx()
        ctx.config["enable_short"] = False
        ind = _ind(cdc_zone_4h="RED", cdc_zone_4h_red_streak=1)
        with patch(PATCH, return_value=ind):
            sig = self.strat.analyze(ctx)
        self.assertEqual(sig.action, "HOLD")

    def test_long_closes_on_red(self):
        with patch(PATCH, return_value=_ind(cdc_zone_4h="RED")):
            sig = self.strat.analyze(_ctx(has_position=True, position_side="LONG"))
        self.assertEqual(sig.action, "SELL")
        self.assertEqual(sig.intent, TradeIntent.CLOSE.value)
        self.assertEqual(sig.position_side, PositionSide.LONG.value)

    def test_short_covers_on_green(self):
        with patch(PATCH, return_value=_ind(cdc_zone_4h="GREEN")):
            sig = self.strat.analyze(_ctx(has_position=True, position_side="SHORT"))
        self.assertEqual(sig.action, "BUY")
        self.assertEqual(sig.intent, TradeIntent.CLOSE.value)
        self.assertEqual(sig.position_side, PositionSide.SHORT.value)

    def test_long_reverse_to_short_on_fresh_red(self):
        ind = _ind(
            cdc_zone_4h="RED",
            cdc_zone_4h_green_streak=0,
            cdc_zone_4h_red_streak=1,
            ema_fast_4h=99.0,
            ema_slow_4h=100.0,
            ema_fast_4h_prev=101.0,
            ema_slow_4h_prev=100.0,
        )
        with patch(PATCH, return_value=ind):
            sig = self.strat.analyze(_ctx(has_position=True, position_side="LONG"))
        self.assertEqual(sig.action, "SELL")
        self.assertEqual(sig.intent, TradeIntent.CLOSE.value)
        self.assertEqual(sig.position_side, PositionSide.LONG.value)
        self.assertEqual(sig.metadata["reverse_to_position_side"], PositionSide.SHORT.value)
        self.assertIn("Fresh short entry", sig.metadata["reverse_reason"])

    def test_long_stale_red_closes_without_reverse(self):
        ind = _ind(
            cdc_zone_4h="RED",
            cdc_zone_4h_green_streak=0,
            cdc_zone_4h_red_streak=2,
            ema_fast_4h=98.0,
            ema_slow_4h=100.0,
            ema_fast_4h_prev=99.0,
            ema_slow_4h_prev=100.0,
        )
        with patch(PATCH, return_value=ind):
            sig = self.strat.analyze(_ctx(has_position=True, position_side="LONG"))
        self.assertEqual(sig.action, "SELL")
        self.assertNotIn("reverse_to_position_side", sig.metadata)

    def test_long_red_with_failed_short_gate_closes_without_reverse(self):
        ind = _ind(
            cdc_zone_4h="RED",
            cdc_zone_4h_green_streak=0,
            cdc_zone_4h_red_streak=1,
            ema_fast_4h=99.0,
            ema_slow_4h=100.0,
            ema_fast_4h_prev=101.0,
            ema_slow_4h_prev=100.0,
            rsi_4h=80.0,
        )
        ctx = _ctx(has_position=True, position_side="LONG")
        ctx.config["rsi_short_max"] = 70.0
        with patch(PATCH, return_value=ind):
            sig = self.strat.analyze(ctx)
        self.assertEqual(sig.action, "SELL")
        self.assertNotIn("reverse_to_position_side", sig.metadata)

    def test_short_reverse_to_long_on_fresh_green(self):
        with patch(PATCH, return_value=_ind(cdc_zone_4h="GREEN", cdc_zone_4h_green_streak=1)):
            sig = self.strat.analyze(_ctx(has_position=True, position_side="SHORT"))
        self.assertEqual(sig.action, "BUY")
        self.assertEqual(sig.intent, TradeIntent.CLOSE.value)
        self.assertEqual(sig.position_side, PositionSide.SHORT.value)
        self.assertEqual(sig.metadata["reverse_to_position_side"], PositionSide.LONG.value)
        self.assertIn("Fresh long entry", sig.metadata["reverse_reason"])

    def test_short_holds_while_red(self):
        with patch(PATCH, return_value=_ind(cdc_zone_4h="RED")):
            sig = self.strat.analyze(_ctx(has_position=True, position_side="SHORT"))
        self.assertEqual(sig.action, "HOLD")

    def test_short_stop_loss_above_entry_covers(self):
        # Short SL sits above entry; a confirmed rise into it covers.
        with patch(PATCH, return_value=_ind(cdc_zone_4h="RED")):
            ctx = _ctx(has_position=True, position_side="SHORT", stop_loss=105.0, current_price=106.0)
            ctx.sl_confirmed = True
            sig = self.strat.analyze(ctx)
        self.assertEqual(sig.action, "BUY")
        self.assertEqual(sig.intent, TradeIntent.CLOSE.value)
        self.assertTrue(sig.is_short)


if __name__ == "__main__":
    unittest.main()
