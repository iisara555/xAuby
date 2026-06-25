"""CDC Action Zone chart indicator plugin."""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd
import pandas_ta as ta

from xauby.strategies.cdc_action_zone.indicators import _action_price, classify_zone
from xauby.strategies.indicators.base import Indicator
from xauby.strategies.indicators.registry import register


@register("cdc_action_zone")
class CDCActionZoneIndicator(Indicator):
    display_config: Dict[str, Any] = {
        "chart_type": "cdc_zones",
        "label": "CDC Action Zone",
        "unit": "price",
        "zones": [
            {"key": "GREEN", "label": "Buy zone", "color": (52, 211, 153)},
            {"key": "BLUE", "label": "PreBuy 2", "color": (129, 140, 248)},
            {"key": "LBLUE", "label": "PreBuy 1", "color": (34, 211, 238)},
            {"key": "YELLOW", "label": "PreSell 1", "color": (253, 224, 71)},
            {"key": "ORANGE", "label": "PreSell 2", "color": (251, 146, 60)},
            {"key": "RED", "label": "Sell zone", "color": (251, 113, 133)},
        ],
        "lines": [
            {"key": "ema12", "label": "EMA12 (AP)", "color": (168, 85, 247), "glyph": "●"},
            {"key": "ema26", "label": "EMA26 (AP)", "color": (56, 189, 248), "glyph": "◆"},
        ],
        "cross_glyph": "✦",
        "zone_column": "zone",
        "zone_label": "Zone",
        "buy_zones": ["GREEN"],
        "metrics": [
            {"key": "zone", "label": "Zone", "hint": "GREEN = buy zone"},
            {"key": "ema12", "label": "EMA12"},
            {"key": "ema26", "label": "EMA26"},
        ],
    }

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def compute(self, df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
        merged = dict(self.config)
        merged.update(config or {})
        ap_smoothing = max(1, int(merged.get("ap_smoothing", 1)))
        out = df.copy()
        close = out["close"].astype(float)
        ap_series = _action_price(close, ap_smoothing)
        out["ap"] = ap_series
        out["ema12"] = ta.ema(ap_series, length=12).fillna(out["close"])
        out["ema26"] = ta.ema(ap_series, length=26).fillna(out["close"])
        zones = []
        for _, row in out.iterrows():
            ap_val = float(row["ap"]) if not pd.isna(row["ap"]) else float(row["close"])
            zones.append(classify_zone(ap_val, float(row["ema12"]), float(row["ema26"])))
        out["zone"] = zones
        return out
