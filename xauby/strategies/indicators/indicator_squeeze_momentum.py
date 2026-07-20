"""Squeeze Momentum (LazyBear) chart indicator plugin.

Renders the momentum histogram column + squeeze state so the terminal chart and
the xauby workspace legend/checklist match the traded strategy exactly (it reuses
``compute_squeeze_frame`` from the strategy package).
"""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from xauby.strategies.indicators.base import Indicator
from xauby.strategies.indicators.registry import register
from xauby.strategies.squeeze_momentum.indicators import compute_squeeze_frame


@register("squeeze_momentum")
class SqueezeMomentumIndicator(Indicator):
    display_config: Dict[str, Any] = {
        "chart_type": "histogram",
        "label": "Squeeze Momentum",
        "unit": "momentum",
        "zones": [
            {"key": "GREEN", "label": "Mom +", "color": (34, 197, 94), "bg_color": (20, 83, 45)},
            {"key": "RED", "label": "Mom -", "color": (248, 113, 113), "bg_color": (127, 29, 29)},
        ],
        "lines": [
            {"key": "mom", "label": "Momentum", "color": (34, 197, 94), "glyph": "▮"},
        ],
        "zone_column": "mom_zone",
        "zone_label": "Mom Zone",
        "buy_zones": ["GREEN"],
        "metrics": [
            {"key": "mom", "label": "Momentum", "hint": "green>0, red<0"},
            {"key": "sqz_state", "label": "Squeeze", "hint": "ON=coiled / OFF=fired"},
        ],
    }

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def compute(self, df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
        merged = dict(self.config)
        merged.update(config or {})
        return compute_squeeze_frame(df, merged)
