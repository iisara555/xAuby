"""Smart Money Concepts chart indicator plugin."""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from xauby.strategies.indicators.base import Indicator
from xauby.strategies.indicators.registry import register
from xauby.strategies.smc_luxalgo.strategy import SMCLuxAlgoStrategy


@register("xauby_smc_pro")
class SMCLuxAlgoIndicator(Indicator):
    display_config: Dict[str, Any] = {
        "chart_type": "smc_luxalgo",
        "label": "xAuby SMC Pro",
        "unit": "price",
        "zone_column": "smc_zone",
        "zone_label": "SMC State",
        "buy_zones": ["BULLISH_BOS", "BULLISH_CHOCH", "BULLISH"],
        "zones": [
            {"key": "BULLISH_BOS", "label": "Bullish BOS", "color": (8, 153, 129), "bg_color": (6, 78, 59)},
            {"key": "BULLISH_CHOCH", "label": "Bullish CHoCH", "color": (34, 197, 94), "bg_color": (20, 83, 45)},
            {"key": "BEARISH_BOS", "label": "Bearish BOS", "color": (242, 54, 69), "bg_color": (127, 29, 29)},
            {"key": "BEARISH_CHOCH", "label": "Bearish CHoCH", "color": (248, 113, 113), "bg_color": (127, 29, 29)},
            {"key": "BULLISH", "label": "Bullish Bias", "color": (45, 212, 191), "bg_color": (19, 78, 74)},
            {"key": "BEARISH", "label": "Bearish Bias", "color": (251, 113, 133), "bg_color": (136, 19, 55)},
            {"key": "NEUTRAL", "label": "Neutral", "color": (148, 163, 184), "bg_color": (51, 65, 85)},
        ],
        "lines": [
            {"key": "smc_swing_high", "label": "Swing High", "color": (248, 113, 113), "glyph": "▴"},
            {"key": "smc_swing_low", "label": "Swing Low", "color": (52, 211, 153), "glyph": "▾"},
            {"key": "smc_internal_high", "label": "Internal High", "color": (251, 191, 36), "glyph": "•"},
            {"key": "smc_internal_low", "label": "Internal Low", "color": (96, 165, 250), "glyph": "•"},
            {"key": "smc_equilibrium", "label": "Equilibrium", "color": (148, 163, 184), "glyph": "·"},
        ],
        "metrics": [
            {"key": "smc_zone", "label": "SMC State"},
            {"key": "smc_bull_confluence_score", "label": "Bull Score"},
            {"key": "smc_bear_confluence_score", "label": "Bear Score"},
            {"key": "smc_volume_ratio", "label": "Vol Ratio"},
        ],
    }

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})
        self._last_config = dict(self.config)

    def compute(self, df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
        merged = dict(self.config)
        merged.update(config or {})
        self._last_config = dict(merged)
        return SMCLuxAlgoStrategy.compute_smc(df, merged)

    def panel_items(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        items = super().panel_items(snapshot)
        try:
            min_score = float(self._last_config.get("confluence_min_score", 1.0))
        except (TypeError, ValueError):
            min_score = 1.0
        try:
            vol_min = float(self._last_config.get("vol_min_ratio", 0.0))
        except (TypeError, ValueError):
            vol_min = 0.0
        for item in items:
            label = item.get("label")
            try:
                value = float(item.get("value"))
            except (TypeError, ValueError):
                value = None
            if label in ("Bull Score", "Bear Score"):
                item["ok"] = None if value is None else value >= min_score
                item["hint"] = f">= {min_score:.1f}"
            elif label == "Vol Ratio":
                item["ok"] = None if value is None else (vol_min <= 0.0 or value >= vol_min)
                item["hint"] = "off" if vol_min <= 0.0 else f">= {vol_min:.2f}x"
        return items
