"""Chart indicator for the SOL EMA20/50 pullback strategy."""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from xauby.strategies.indicators.base import Indicator
from xauby.strategies.indicators.registry import register
from xauby.strategies.sol_ema_pullback.strategy import SOLEMAPullbackStrategy, annotate


@register("sol_ema_pullback")
class SOLEMAPullbackIndicator(Indicator):
    display_config: Dict[str, Any] = {
        "chart_type": "trend_zones",
        "label": "SOL EMA Pullback",
        "unit": "price",
        "zone_column": "ema_pullback_zone",
        "zone_label": "EMA Pullback",
        "buy_zones": ["ENTRY"],
        "zones": [
            {"key": "ENTRY", "label": "Entry", "color": (52, 211, 153), "bg_color": (20, 83, 45)},
            {"key": "PULLBACK", "label": "Pullback", "color": (250, 204, 21), "bg_color": (113, 63, 18)},
            {"key": "NEUTRAL", "label": "Wait", "color": (148, 163, 184)},
            {"key": "BEAR", "label": "Trend lost", "color": (251, 113, 133), "bg_color": (127, 29, 29)},
        ],
        "lines": [
            {"key": "ema20", "label": "EMA20", "color": (56, 189, 248), "glyph": "●"},
            {"key": "ema50", "label": "EMA50", "color": (250, 204, 21), "glyph": "◆"},
            {"key": "swing_low", "label": "Swing Low", "color": (251, 113, 133), "glyph": "·"},
        ],
        "metrics": [
            {"key": "ema_pullback_zone", "label": "EMA Pullback"},
            {"key": "ema20", "label": "EMA20"},
            {"key": "ema50", "label": "EMA50"},
            {"key": "vol_ratio", "label": "Volume", "hint": "x avg"},
            {"key": "swing_low", "label": "Swing Low"},
        ],
    }

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def compute(self, df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
        merged = {**SOLEMAPullbackStrategy.default_config(), **self.config, **(config or {})}
        return annotate(df, merged)
