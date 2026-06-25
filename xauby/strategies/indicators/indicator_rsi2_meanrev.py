"""RSI(2) mean-reversion chart indicator plugin."""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd
import pandas_ta as ta

from xauby.strategies.indicators.base import Indicator
from xauby.strategies.indicators.registry import register


@register("rsi2_meanrev")
class RSI2MeanReversionIndicator(Indicator):
    display_config: Dict[str, Any] = {
        "chart_type": "rsi2_meanrev",
        "label": "RSI(2) Mean Reversion",
        "zone_column": "rsi2_zone",
        "zone_label": "RSI2 Setup",
        "buy_zones": ["SETUP"],
        "zones": [
            {"key": "SETUP", "label": "Buy Setup", "color": (52, 211, 153)},
            {"key": "OVERSOLD", "label": "Oversold", "color": (34, 211, 238)},
            {"key": "EXIT", "label": "Exit", "color": (251, 113, 133)},
            {"key": "NEUTRAL", "label": "Neutral", "color": (148, 163, 184)},
        ],
        "lines": [
            {"key": "ema200", "label": "EMA200", "color": (168, 85, 247)},
            {"key": "sma5", "label": "SMA5", "color": (56, 189, 248)},
        ],
        "metrics": [
            {"key": "rsi2_zone", "label": "RSI2 Setup"},
            {"key": "rsi2", "label": "RSI2"},
        ],
    }

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def compute(self, df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
        merged = {**self.config, **(config or {})}
        rsi_n = max(1, int(merged.get("rsi_period", 2)))
        rsi_buy = float(merged.get("rsi_buy", 10.0))
        rsi_exit = float(merged.get("rsi_exit", 65.0))
        sma_n = max(1, int(merged.get("sma_fast_period", 5)))
        ema_n = max(1, int(merged.get("ema_period", 200)))

        out = df.copy()
        close = pd.to_numeric(out["close"], errors="coerce").astype(float)
        rsi = ta.rsi(close, length=rsi_n)
        out["rsi2"] = rsi.fillna(50.0) if rsi is not None else 50.0
        out["ema200"] = close.ewm(span=ema_n, adjust=False).mean()
        out["sma5"] = close.rolling(sma_n).mean()

        zones = []
        for idx in out.index:
            price = float(close.loc[idx])
            rsi_now = float(out.at[idx, "rsi2"])
            ema_now = out.at[idx, "ema200"]
            sma_now = out.at[idx, "sma5"]
            if rsi_now > rsi_exit:
                zone = "EXIT"
            elif rsi_now < rsi_buy and pd.notna(ema_now) and pd.notna(sma_now) and price > float(ema_now) and price < float(sma_now):
                zone = "SETUP"
            elif rsi_now < rsi_buy:
                zone = "OVERSOLD"
            else:
                zone = "NEUTRAL"
            zones.append(zone)
        out["rsi2_zone"] = zones
        return out
