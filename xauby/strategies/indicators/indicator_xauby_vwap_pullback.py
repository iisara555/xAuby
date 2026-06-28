"""Chart indicator for the xAuby EMA/VWAP pullback strategy."""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from xauby.strategies.indicators.base import Indicator
from xauby.strategies.indicators.registry import register
from xauby.strategies.xauby_vwap_pullback.strategy import (
    XAubyVWAPPullbackStrategy,
    annotate,
)


@register("xauby_vwap_pullback")
class XAubyVWAPPullbackIndicator(Indicator):
    display_config: Dict[str, Any] = {
        "chart_type": "trend_zones",
        "label": "xAuby VWAP Pullback",
        "unit": "price",
        "zone_column": "vwap_pullback_zone",
        "zone_label": "VWAP Pullback",
        "buy_zones": ["ENTRY"],
        "zones": [
            {"key": "ENTRY", "label": "Entry", "color": (52, 211, 153), "bg_color": (20, 83, 45)},
            {"key": "PULLBACK", "label": "Pullback", "color": (250, 204, 21), "bg_color": (113, 63, 18)},
            {"key": "TREND", "label": "Trend", "color": (34, 197, 94), "bg_color": (20, 83, 45)},
            {"key": "NEUTRAL", "label": "Wait", "color": (148, 163, 184)},
            {"key": "BEAR", "label": "Trend lost", "color": (251, 113, 133), "bg_color": (127, 29, 29)},
        ],
        "lines": [
            {"key": "ema_fast", "label": "EMA20", "color": (56, 189, 248), "glyph": "●"},
            {"key": "ema_slow", "label": "EMA50", "color": (250, 204, 21), "glyph": "◆"},
            {"key": "ema_trend", "label": "EMA200", "color": (244, 114, 182), "glyph": "■"},
            {"key": "vwap", "label": "VWAP", "color": (167, 139, 250), "glyph": "·"},
        ],
        "metrics": [
            {"key": "vwap_pullback_zone", "label": "VWAP Pullback"},
            {"key": "ema_fast", "label": "EMA20"},
            {"key": "ema_slow", "label": "EMA50"},
            {"key": "ema_trend", "label": "EMA200"},
            {"key": "vwap", "label": "VWAP"},
            {"key": "volume_ratio", "label": "Volume", "hint": "x avg"},
        ],
    }

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def compute(self, df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
        merged = {**XAubyVWAPPullbackStrategy.default_config(), **self.config, **(config or {})}
        return annotate(df, merged)
