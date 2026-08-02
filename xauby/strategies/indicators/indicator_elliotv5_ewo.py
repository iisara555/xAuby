"""ElliotV5 / SMAOffset (EWO) chart indicator plugin.

Renders the MA offset entry/exit lines and the EWO zone so the terminal chart
and the xauby workspace legend/checklist match the traded strategy exactly (it
reuses ``compute_elliotv5_frame`` from the strategy package).
"""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from xauby.strategies.elliotv5_ewo.indicators import compute_elliotv5_frame
from xauby.strategies.indicators.base import Indicator
from xauby.strategies.indicators.registry import register


@register("elliotv5_ewo")
class ElliotV5EWOIndicator(Indicator):
    display_config: Dict[str, Any] = {
        "chart_type": "overlay",
        "label": "ElliotV5 EWO",
        "unit": "price",
        "zones": [
            {"key": "IMPULSE", "label": "EWO impulse", "color": (34, 197, 94),
             "bg_color": (20, 83, 45)},
            {"key": "CAPITULATION", "label": "EWO capitulation",
             "color": (248, 113, 113), "bg_color": (127, 29, 29)},
            {"key": "NEUTRAL", "label": "EWO neutral", "color": (148, 163, 184),
             "bg_color": (30, 41, 59)},
        ],
        "lines": [
            {"key": "buy_line", "label": "Buy offset", "color": (34, 197, 94),
             "glyph": "─"},
            {"key": "sell_line", "label": "Sell offset", "color": (248, 113, 113),
             "glyph": "─"},
        ],
        "zone_column": "ewo_zone",
        "zone_label": "EWO Zone",
        "buy_zones": ["IMPULSE", "CAPITULATION"],
        "metrics": [
            {"key": "ewo", "label": "EWO", "hint": "EMA spread %"},
            {"key": "buy_line", "label": "Buy offset", "hint": "SMA x low_offset"},
            {"key": "sell_line", "label": "Sell offset", "hint": "SMA x high_offset"},
        ],
    }

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def compute(self, df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
        merged = dict(self.config)
        merged.update(config or {})
        return compute_elliotv5_frame(df, merged)
