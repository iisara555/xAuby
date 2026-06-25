"""Volatility-breakout chart indicator plugin."""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from xauby.strategies.indicators.base import Indicator
from xauby.strategies.indicators.registry import register


@register("vol_breakout")
class VolatilityBreakoutIndicator(Indicator):
    display_config: Dict[str, Any] = {
        "chart_type": "vol_breakout",
        "label": "Volatility Breakout",
        "zone_column": "vol_breakout_zone",
        "zone_label": "Breakout State",
        "buy_zones": ["BREAKOUT"],
        "zones": [
            {"key": "BREAKOUT", "label": "Breakout", "color": (52, 211, 153)},
            {"key": "EXPANSION", "label": "ATR Expansion", "color": (251, 191, 36)},
            {"key": "NEUTRAL", "label": "Neutral", "color": (148, 163, 184)},
        ],
        "lines": [
            {"key": "range_high", "label": "Range High", "color": (34, 211, 238)},
            {"key": "exit_ema", "label": "Exit EMA", "color": (168, 85, 247)},
        ],
        "metrics": [
            {"key": "vol_breakout_zone", "label": "Breakout State"},
            {"key": "atr_expansion_ratio", "label": "ATR Expansion"},
            {"key": "volume_ratio", "label": "Vol Ratio"},
        ],
    }

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def compute(self, df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
        merged = {**self.config, **(config or {})}
        lookback = max(1, int(merged.get("lookback", 20)))
        atr_n = max(1, int(merged.get("atr_period", 14)))
        atr_ma_n = max(1, int(merged.get("atr_ma_period", 50)))
        expansion_mult = float(merged.get("atr_expansion_mult", 1.2))
        vol_n = max(1, int(merged.get("vol_ma_period", 20)))
        vol_min = float(merged.get("vol_min_ratio", 1.2))
        exit_ema_n = max(1, int(merged.get("exit_ema_period", 20)))

        out = df.copy()
        high = pd.to_numeric(out["high"], errors="coerce").astype(float)
        low = pd.to_numeric(out["low"], errors="coerce").astype(float)
        close = pd.to_numeric(out["close"], errors="coerce").astype(float)
        volume = pd.to_numeric(out["volume"], errors="coerce").astype(float)
        prev_close = close.shift(1).fillna(close)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(atr_n).mean()
        atr_avg = atr.fillna(0.0).rolling(atr_ma_n).mean()
        out["range_high"] = high.shift(1).rolling(lookback).max()
        out["exit_ema"] = close.ewm(span=exit_ema_n, adjust=False).mean()
        out["atr_expansion_ratio"] = atr / atr_avg.replace(0.0, pd.NA)
        out["volume_ratio"] = volume / volume.rolling(vol_n).mean().replace(0.0, pd.NA)

        breakout = close > out["range_high"]
        expansion = out["atr_expansion_ratio"] >= expansion_mult
        volume_ok = out["volume_ratio"] >= vol_min
        out["vol_breakout_zone"] = "NEUTRAL"
        out.loc[expansion.fillna(False), "vol_breakout_zone"] = "EXPANSION"
        out.loc[(breakout & expansion & volume_ok).fillna(False), "vol_breakout_zone"] = "BREAKOUT"
        return out
