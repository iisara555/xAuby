"""BB + RSI mean reversion chart indicator plugin."""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd
import pandas_ta as ta

from xauby.strategies.bbrsi_mean_reversion.indicators import classify_reversion_zone
from xauby.strategies.indicators.base import Indicator
from xauby.strategies.indicators.registry import register


@register("bbrsi_mean_reversion")
class BBRSIMeanReversionIndicator(Indicator):
    display_config: Dict[str, Any] = {
        "chart_type": "bbrsi_mean_reversion",
        "label": "BBRSI Mean Reversion",
        "unit": "price",
        "zone_column": "zone",
        "zone_label": "Reversion Zone",
        "buy_zones": ["OVERSOLD"],
        "zones": [
            {"key": "OVERSOLD", "label": "Oversold", "color": (52, 211, 153)},
            {"key": "NEUTRAL", "label": "Neutral", "color": (148, 163, 184)},
            {"key": "OVERBOUGHT", "label": "Overbought", "color": (251, 113, 133)},
        ],
        "lines": [
            {"key": "bb_upper", "label": "BB Upper", "color": (96, 165, 250), "glyph": "─"},
            {"key": "bb_mid", "label": "BB Mid", "color": (148, 163, 184), "glyph": "─"},
            {"key": "bb_lower", "label": "BB Lower", "color": (96, 165, 250), "glyph": "─"},
        ],
        "metrics": [
            {"key": "rsi", "label": "RSI", "min": 0, "max": 30},
            {"key": "bb_pct", "label": "BB %B", "hint": "<= 0 oversold"},
            {"key": "volume_ratio", "label": "Vol Ratio"},
        ],
    }

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})
        self._last_config = dict(self.config)

    def compute(self, df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
        merged = dict(self.config)
        merged.update(config or {})
        self._last_config = dict(merged)
        bb_length = max(5, int(merged.get("bb_length", 20)))
        bb_std = float(merged.get("bb_std", 2.0))
        rsi_period = max(2, int(merged.get("rsi_period", 14)))
        vol_ma_period = max(2, int(merged.get("volume_ma_period", 20)))
        rsi_oversold = float(merged.get("rsi_oversold", 30.0))
        rsi_overbought = float(merged.get("rsi_overbought", 70.0))

        out = df.copy()
        for col in ("open", "high", "low", "close", "volume"):
            out[col] = pd.to_numeric(out[col], errors="coerce")
        close = out["close"].astype(float)
        volume = out["volume"].astype(float)

        bb = ta.bbands(close, length=bb_length, std=bb_std)
        rsi_series = ta.rsi(close, length=rsi_period)

        if bb is not None and not bb.empty:
            for prefix, key in (("BBU", "bb_upper"), ("BBM", "bb_mid"), ("BBL", "bb_lower"), ("BBP", "bb_pct")):
                col_name = next((c for c in bb.columns if c.startswith(prefix)), None)
                if col_name:
                    out[key] = bb[col_name]
        else:
            out["bb_upper"] = pd.NA
            out["bb_mid"] = pd.NA
            out["bb_lower"] = pd.NA
            out["bb_pct"] = pd.NA

        out["rsi"] = rsi_series.fillna(50.0) if rsi_series is not None else 50.0
        prior_vol_sma = volume.shift(1).rolling(vol_ma_period).mean()
        out["volume_ratio"] = volume / prior_vol_sma.replace(0.0, pd.NA)

        zones = []
        for _, row in out.iterrows():
            zones.append(
                classify_reversion_zone(
                    float(row["close"]) if not pd.isna(row["close"]) else 0.0,
                    float(row.get("bb_lower", 0.0)) if not pd.isna(row.get("bb_lower")) else 0.0,
                    float(row.get("bb_mid", 0.0)) if not pd.isna(row.get("bb_mid")) else 0.0,
                    float(row.get("bb_upper", 0.0)) if not pd.isna(row.get("bb_upper")) else 0.0,
                    float(row.get("rsi", 50.0)) if not pd.isna(row.get("rsi")) else 50.0,
                    rsi_oversold=rsi_oversold,
                    rsi_overbought=rsi_overbought,
                )
            )
        out["zone"] = zones
        return out

    def panel_items(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        items = super().panel_items(snapshot)
        try:
            rsi_oversold = float(self._last_config.get("rsi_oversold", 30.0))
        except (TypeError, ValueError):
            rsi_oversold = 30.0

        for item in items:
            if item.get("label") != "RSI":
                continue
            try:
                value = float(item.get("value"))
            except (TypeError, ValueError):
                item["ok"] = None
            else:
                item["ok"] = value <= rsi_oversold
            item["hint"] = f"<= {rsi_oversold:.0f}"
            break
        return items
