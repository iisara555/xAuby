"""EMA200 chart indicator plugin."""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from xauby.strategies.indicators.base import Indicator
from xauby.strategies.indicators.registry import register


def _ema_np(values: np.ndarray, period: int) -> np.ndarray:
    ema = np.empty(len(values), dtype="float64")
    if len(values) == 0:
        return ema
    alpha = 2.0 / (period + 1.0)
    ema[0] = values[0]
    for i in range(1, len(values)):
        ema[i] = (values[i] * alpha) + (ema[i - 1] * (1.0 - alpha))
    return ema


@register("ema200")
class EMA200Indicator(Indicator):
    display_config: Dict[str, Any] = {
        "chart_type": "line",
        "label": "EMA200",
        "unit": "price",
        "lines": [
            {"key": "ema200", "label": "EMA200", "color": (251, 191, 36), "glyph": "◆"},
        ],
        "metrics": [
            {"key": "ema200", "label": "EMA200"},
        ],
    }

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def compute(self, df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
        merged = dict(self.config)
        merged.update(config or {})
        period = max(1, int(merged.get("ema_period", 200)))
        out = df.copy()
        close = out["close"].astype(float).to_numpy()
        out["ema200"] = _ema_np(close, period)
        return out
