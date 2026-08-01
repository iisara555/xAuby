"""UT Bot ATR chart indicator plugin.

Renders the ATR trailing stop line and the long/short zone so the terminal
chart and the xauby workspace legend/checklist match the traded strategy
exactly (it reuses ``compute_ut_bot_frame`` from the strategy package).
"""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from xauby.strategies.indicators.base import Indicator
from xauby.strategies.indicators.registry import register
from xauby.strategies.ut_bot_atr.indicators import compute_ut_bot_frame


@register("ut_bot_atr")
class UTBotATRIndicator(Indicator):
    display_config: Dict[str, Any] = {
        "chart_type": "overlay",
        "label": "UT Bot ATR",
        "unit": "price",
        "zones": [
            {"key": "LONG", "label": "UT Long", "color": (34, 197, 94), "bg_color": (20, 83, 45)},
            {"key": "SHORT", "label": "UT Short", "color": (248, 113, 113), "bg_color": (127, 29, 29)},
        ],
        "lines": [
            {"key": "ut_stop", "label": "UT Stop", "color": (250, 204, 21), "glyph": "─"},
        ],
        "zone_column": "ut_zone",
        "zone_label": "UT Zone",
        "buy_zones": ["LONG"],
        "metrics": [
            {"key": "ut_stop", "label": "Trail Stop", "hint": "flip level"},
            {"key": "ut_atr", "label": "ATR", "hint": "flip ATR"},
        ],
    }

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def compute(self, df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
        merged = dict(self.config)
        merged.update(config or {})
        return compute_ut_bot_frame(df, merged)
