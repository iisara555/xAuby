"""BBKC squeeze chart indicator plugin."""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd
import pandas_ta as ta

from xauby.strategies.bbkc_squeeze.indicators import classify_squeeze_zone
from xauby.strategies.indicators.base import Indicator
from xauby.strategies.indicators.registry import register


@register("bbkc_squeeze")
class BBKCSqueezeIndicator(Indicator):
    display_config: Dict[str, Any] = {
        "chart_type": "bbkc_squeeze",
        "label": "BBKC Squeeze",
        "unit": "price",
        "zone_column": "zone",
        "zone_label": "Squeeze State",
        "buy_zones": ["FIRED"],
        "zones": [
            {"key": "SQUEEZE", "label": "Compressed", "color": (148, 163, 184)},
            {"key": "FIRED", "label": "Breakout", "color": (52, 211, 153)},
            {"key": "NEUTRAL", "label": "Neutral", "color": (100, 116, 139)},
        ],
        "lines": [
            {"key": "bb_upper", "label": "BB Upper", "color": (96, 165, 250), "glyph": "─"},
            {"key": "bb_mid", "label": "BB Mid", "color": (148, 163, 184), "glyph": "─"},
            {"key": "bb_lower", "label": "BB Lower", "color": (96, 165, 250), "glyph": "─"},
            {"key": "kc_upper", "label": "KC Upper", "color": (251, 191, 36), "glyph": "┄"},
            {"key": "kc_lower", "label": "KC Lower", "color": (251, 191, 36), "glyph": "┄"},
        ],
        "metrics": [
            {"key": "momentum", "label": "Momentum", "hint": "> 0 bullish"},
            {"key": "rsi", "label": "RSI", "min": 40, "max": 75},
            {"key": "volume_ratio", "label": "Vol Ratio", "hint": ">= min ratio"},
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
        kc_length = max(5, int(merged.get("kc_length", 20)))
        kc_mult = float(merged.get("kc_mult", 1.5))
        squeeze_length = max(5, int(merged.get("squeeze_length", bb_length)))
        rsi_period = max(2, int(merged.get("rsi_period", 14)))
        vol_ma_period = max(2, int(merged.get("volume_ma_period", 20)))

        out = df.copy()
        for col in ("open", "high", "low", "close", "volume"):
            out[col] = pd.to_numeric(out[col], errors="coerce")
        high = out["high"].astype(float)
        low = out["low"].astype(float)
        close = out["close"].astype(float)
        volume = out["volume"].astype(float)

        bb = ta.bbands(close, length=bb_length, std=bb_std)
        kc = ta.kc(high, low, close, length=kc_length, scalar=kc_mult)
        squeeze = ta.squeeze(
            high,
            low,
            close,
            length=squeeze_length,
            bb_std=bb_std,
            kc_mult=kc_mult,
        )
        rsi_series = ta.rsi(close, length=rsi_period)

        if bb is not None and not bb.empty:
            for prefix, key in (("BBU", "bb_upper"), ("BBM", "bb_mid"), ("BBL", "bb_lower")):
                col_name = next((c for c in bb.columns if c.startswith(prefix)), None)
                if col_name:
                    out[key] = bb[col_name]
        else:
            out["bb_upper"] = pd.NA
            out["bb_mid"] = pd.NA
            out["bb_lower"] = pd.NA

        if kc is not None and not kc.empty:
            upper_col = next((c for c in kc.columns if c.startswith("KCU")), None)
            lower_col = next((c for c in kc.columns if c.startswith("KCL")), None)
            out["kc_upper"] = kc[upper_col] if upper_col else pd.NA
            out["kc_lower"] = kc[lower_col] if lower_col else pd.NA
        else:
            out["kc_upper"] = pd.NA
            out["kc_lower"] = pd.NA

        if squeeze is not None and not squeeze.empty:
            mom_col = next(
                (
                    c
                    for c in squeeze.columns
                    if c.startswith("SQZ_") and c not in ("SQZ_ON", "SQZ_OFF", "SQZ_NO")
                ),
                None,
            )
            out["momentum"] = squeeze[mom_col] if mom_col else 0.0
            out["squeeze_on"] = squeeze.get("SQZ_ON", 0).fillna(0).astype(int) == 1
            out["squeeze_fired"] = squeeze.get("SQZ_OFF", 0).fillna(0).astype(int) == 1
        else:
            out["momentum"] = 0.0
            out["squeeze_on"] = False
            out["squeeze_fired"] = False

        out["rsi"] = rsi_series.fillna(50.0) if rsi_series is not None else 50.0
        prior_vol_sma = volume.shift(1).rolling(vol_ma_period).mean()
        out["volume_ratio"] = volume / prior_vol_sma.replace(0.0, pd.NA)

        zones = []
        for _, row in out.iterrows():
            zones.append(
                classify_squeeze_zone(
                    bool(row.get("squeeze_on")),
                    bool(row.get("squeeze_fired")),
                )
            )
        out["zone"] = zones
        return out

    def panel_items(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        items = super().panel_items(snapshot)
        try:
            rsi_min = float(self._last_config.get("rsi_min", 40.0))
            rsi_max = float(self._last_config.get("rsi_max", 75.0))
        except (TypeError, ValueError):
            rsi_min, rsi_max = 40.0, 75.0
        try:
            vol_min_ratio = float(self._last_config.get("vol_min_ratio", 0.8))
        except (TypeError, ValueError):
            vol_min_ratio = 0.8

        for item in items:
            label = item.get("label")
            try:
                value = float(item.get("value"))
            except (TypeError, ValueError):
                value = None
            if label == "RSI":
                item["ok"] = None if value is None else rsi_min <= value <= rsi_max
                item["hint"] = f"{rsi_min:.0f}-{rsi_max:.0f}"
            elif label == "Vol Ratio":
                item["ok"] = None if value is None else value >= vol_min_ratio
                item["hint"] = f">= {vol_min_ratio:.2f}x"
        return items
