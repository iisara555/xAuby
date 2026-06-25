"""SimpleScalp+ chart indicator plugin.

Renders the confluence zone (BULL/BEAR/NEUTRAL) plus the Hull MA, VWAP and
EMA9/21 lines. The columns come from the strategy's own ``annotate`` helper so
the chart can never drift from the live signal logic.
"""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from xauby.strategies.indicators.base import Indicator
from xauby.strategies.indicators.registry import register
from xauby.strategies.simple_scalp_plus.strategy import (
    SimpleScalpPlusStrategy,
    annotate,
)


@register("simple_scalp_plus")
class SimpleScalpPlusIndicator(Indicator):
    display_config: Dict[str, Any] = {
        "chart_type": "trend_zones",
        "label": "SimpleScalp+",
        "unit": "price",
        "zones": [
            {"key": "BULL", "label": "Buy zone", "color": (52, 211, 153), "bg_color": (20, 83, 45)},
            {"key": "BEAR", "label": "Sell zone", "color": (251, 113, 133), "bg_color": (127, 29, 29)},
            {"key": "NEUTRAL", "label": "No setup", "color": (148, 163, 184)},
        ],
        "lines": [
            {"key": "hull", "label": "Hull MA", "color": (168, 85, 247), "glyph": "●"},
            {"key": "vwap", "label": "VWAP", "color": (56, 189, 248), "glyph": "◆"},
            {"key": "ema_fast", "label": "EMA9", "color": (250, 204, 21), "glyph": "·"},
            {"key": "ema_slow", "label": "EMA21", "color": (251, 146, 60), "glyph": "·"},
        ],
        "zone_column": "scalp_zone",
        "zone_label": "Scalp Zone",
        "buy_zones": ["BULL"],
        "metrics": [
            {"key": "scalp_zone", "label": "Scalp Zone", "hint": "BULL = buy confluence"},
            {"key": "buy_count", "label": "BUY conf", "hint": "x/8"},
            {"key": "sell_count", "label": "SELL conf", "hint": "x/8"},
            {"key": "hull", "label": "Hull MA"},
            {"key": "vwap", "label": "VWAP"},
        ],
    }

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def compute(self, df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
        merged = {**SimpleScalpPlusStrategy.default_config(), **self.config, **(config or {})}
        return annotate(df, merged)
