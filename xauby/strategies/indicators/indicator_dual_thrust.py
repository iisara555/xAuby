"""Dual Thrust chart indicator plugin.

Renders the session breakout lines and the inside/breakout zone so the terminal
chart and the xauby workspace legend/checklist match the traded strategy
exactly (it reuses ``compute_dual_thrust_frame`` from the strategy package).
"""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from xauby.strategies.dual_thrust.indicators import compute_dual_thrust_frame
from xauby.strategies.indicators.base import Indicator
from xauby.strategies.indicators.registry import register


@register("dual_thrust")
class DualThrustIndicator(Indicator):
    display_config: Dict[str, Any] = {
        "chart_type": "overlay",
        "label": "Dual Thrust",
        "unit": "price",
        "zones": [
            {"key": "LONG_BREAK", "label": "Upper break", "color": (34, 197, 94),
             "bg_color": (20, 83, 45)},
            {"key": "SHORT_BREAK", "label": "Lower break", "color": (248, 113, 113),
             "bg_color": (127, 29, 29)},
            {"key": "INSIDE", "label": "Inside range", "color": (148, 163, 184),
             "bg_color": (30, 41, 59)},
        ],
        "lines": [
            {"key": "dt_buy_line", "label": "Buy Line", "color": (34, 197, 94), "glyph": "─"},
            {"key": "dt_sell_line", "label": "Sell Line", "color": (248, 113, 113), "glyph": "─"},
        ],
        "zone_column": "dt_zone",
        "zone_label": "DT Zone",
        "buy_zones": ["LONG_BREAK"],
        "metrics": [
            {"key": "dt_buy_line", "label": "Buy Line", "hint": "open + k1*range"},
            {"key": "dt_sell_line", "label": "Sell Line", "hint": "open - k2*range"},
            {"key": "dt_range", "label": "Range", "hint": "prior N sessions"},
        ],
    }

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def compute(self, df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
        merged = dict(self.config)
        merged.update(config or {})
        return compute_dual_thrust_frame(df, merged)
