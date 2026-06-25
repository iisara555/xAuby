"""SuperTrend chart indicator plugin."""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from xauby.strategies.supertrend_ema200.strategy import SuperTrendEMA200Strategy
from xauby.strategies.indicators.base import Indicator
from xauby.strategies.indicators.registry import register


def _supertrend_series(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    atr: pd.Series,
    mult: float,
) -> tuple[pd.Series, pd.Series]:
    hl2 = (high + low) / 2.0
    upper_basic = hl2 + mult * atr
    lower_basic = hl2 - mult * atr
    upper = upper_basic.copy()
    lower = lower_basic.copy()
    trend = pd.Series(False, index=close.index)

    for i in range(1, len(close)):
        prev_i = i - 1
        if pd.isna(atr.iloc[i]):
            trend.iloc[i] = trend.iloc[prev_i]
            continue
        if upper_basic.iloc[i] < upper.iloc[prev_i] or close.iloc[prev_i] > upper.iloc[prev_i]:
            upper.iloc[i] = upper_basic.iloc[i]
        else:
            upper.iloc[i] = upper.iloc[prev_i]
        if lower_basic.iloc[i] > lower.iloc[prev_i] or close.iloc[prev_i] < lower.iloc[prev_i]:
            lower.iloc[i] = lower_basic.iloc[i]
        else:
            lower.iloc[i] = lower.iloc[prev_i]
        trend.iloc[i] = close.iloc[i] >= lower.iloc[i] if trend.iloc[prev_i] else close.iloc[i] > upper.iloc[i]

    st_line = pd.Series(index=close.index, dtype="float64")
    st_line[trend] = lower[trend]
    st_line[~trend] = upper[~trend]
    return trend, st_line


@register("supertrend")
class SuperTrendIndicator(Indicator):
    display_config: Dict[str, Any] = {
        "chart_type": "trend_zones",
        "label": "SuperTrend",
        "unit": "price",
        "zones": [
            {"key": "BULL", "label": "ST Bull", "color": (34, 197, 94), "bg_color": (20, 83, 45)},
            {"key": "BEAR", "label": "ST Bear", "color": (248, 113, 113), "bg_color": (127, 29, 29)},
        ],
        "lines": [
            {"key": "supertrend", "label": "SuperTrend", "color": (34, 197, 94), "glyph": "●"},
        ],
        "zone_column": "supertrend_zone",
        "zone_label": "ST Zone",
        "buy_zones": ["BULL"],
        "metrics": [
            {"key": "supertrend_zone", "label": "ST Zone", "hint": "BULL = trend up"},
            {"key": "supertrend", "label": "SuperTrend"},
        ],
    }

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def compute(self, df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
        merged = dict(self.config)
        merged.update(config or {})
        atr_period = max(1, int(merged.get("atr_period", 10)))
        st_mult = float(merged.get("supertrend_mult", 3.5))
        min_required = max(atr_period + 5, 2)
        max_calc_bars = max(min_required, int(merged.get("max_calc_bars", 420)))
        out = df.copy()
        if out.empty:
            out["supertrend"] = np.nan
            out["supertrend_bull"] = False
            out["supertrend_zone"] = "BEAR"
            return out
        calc = out.tail(max_calc_bars) if len(out) > max_calc_bars else out
        high = calc["high"].to_numpy(dtype="float64", copy=False)
        low = calc["low"].to_numpy(dtype="float64", copy=False)
        close = calc["close"].to_numpy(dtype="float64", copy=False)

        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]
        tr = np.maximum.reduce([high - low, np.abs(high - prev_close), np.abs(low - prev_close)])
        atr = SuperTrendEMA200Strategy._rolling_mean_np(tr, atr_period)
        trend, st_line = SuperTrendEMA200Strategy._supertrend_np(high, low, close, atr, st_mult)

        out["supertrend"] = np.nan
        out["supertrend_bull"] = False
        out.loc[calc.index, "supertrend"] = st_line
        out.loc[calc.index, "supertrend_bull"] = trend
        out["supertrend_zone"] = np.where(out["supertrend_bull"], "BULL", "BEAR")
        return out
