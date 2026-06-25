"""ICT Lite chart indicator plugin."""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from xauby.strategies.ict_lite_strategy.strategy import ICTLiteStrategy
from xauby.strategies.indicators.base import Indicator
from xauby.strategies.indicators.registry import register


@register("ict_lite")
class ICTLiteIndicator(Indicator):
    display_config: Dict[str, Any] = {
        "chart_type": "ict_lite",
        "label": "ICT Lite",
        "unit": "price",
        "zone_column": "ict_zone",
        "zone_label": "ICT Setup",
        "buy_zones": ["MSS"],
        "zones": [
            {"key": "SWEEP", "label": "Sweep Low", "color": (251, 191, 36), "bg_color": (113, 63, 18)},
            {"key": "RECLAIM", "label": "Reclaim", "color": (52, 211, 153), "bg_color": (6, 78, 59)},
            {"key": "MSS", "label": "MSS", "color": (34, 197, 94), "bg_color": (20, 83, 45)},
            {"key": "NEUTRAL", "label": "Neutral", "color": (148, 163, 184), "bg_color": (51, 65, 85)},
        ],
        "lines": [
            {"key": "ema_fast", "label": "EMA Fast", "color": (34, 211, 238), "glyph": "●"},
            {"key": "ema_slow", "label": "EMA Slow", "color": (96, 165, 250), "glyph": "◆"},
            {"key": "recent_high", "label": "Recent High", "color": (248, 113, 113), "glyph": "▴"},
            {"key": "recent_low", "label": "Recent Low", "color": (52, 211, 153), "glyph": "▾"},
        ],
        "metrics": [
            {"key": "ict_zone", "label": "ICT Zone"},
            {"key": "rsi", "label": "RSI"},
            {"key": "volume_ratio", "label": "Vol Ratio"},
        ],
    }

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})
        self._last_config = dict(self.config)

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        return ICTLiteStrategy._rsi(close, period)

    def compute(self, df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
        merged = dict(self.config)
        merged.update(config or {})
        self._last_config = dict(merged)
        fast_n = max(1, int(merged.get("ema_fast", 20)))
        slow_n = max(1, int(merged.get("ema_slow", 50)))
        atr_n = max(1, int(merged.get("atr_period", 14)))
        rsi_n = max(2, int(merged.get("rsi_period", 14)))
        vol_n = max(2, int(merged.get("volume_ma_period", 20)))
        lookback = max(2, int(merged.get("swing_lookback", 20)))
        reclaim_window = max(1, int(merged.get("reclaim_window", 3)))
        mss_lookback = max(1, int(merged.get("mss_lookback", 5)))

        out = df.copy()
        for col in ("open", "high", "low", "close", "volume"):
            out[col] = pd.to_numeric(out[col], errors="coerce")
        close = out["close"].astype(float)
        high = out["high"].astype(float)
        low = out["low"].astype(float)
        volume = out["volume"].astype(float)
        out["ema_fast"] = close.ewm(span=fast_n, adjust=False).mean()
        out["ema_slow"] = close.ewm(span=slow_n, adjust=False).mean()
        prev_close = close.shift(1)
        tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        out["atr"] = tr.rolling(atr_n).mean()
        out["rsi"] = self._rsi(close, rsi_n)
        vol_ma = volume.rolling(vol_n).mean()
        out["volume_ratio"] = volume / vol_ma.replace(0.0, pd.NA)
        out["recent_high"] = high.shift(1).rolling(lookback).max()
        out["recent_low"] = low.shift(1).rolling(lookback).min()

        zones = []
        sweep_levels = []
        mss_levels = []
        for idx in range(len(out)):
            prior = out.iloc[max(0, idx - lookback): idx]
            recent_low = float(prior["low"].min()) if not prior.empty else float(out["low"].iloc[idx])
            recent_high = float(prior["high"].max()) if not prior.empty else float(out["high"].iloc[idx])
            sweep = ICTLiteStrategy._recent_sweep_low(
                out,
                end_idx=idx,
                lookback=lookback,
                window=reclaim_window,
            )
            sweep_ok = bool(sweep["ok"])
            sweep_level = float(sweep["level"]) if sweep["level"] is not None else recent_low
            reclaim_ok = sweep_ok and float(out["close"].iloc[idx]) > sweep_level
            mss = ICTLiteStrategy._bullish_mss(
                out,
                end_idx=idx,
                sweep_idx=sweep["idx"],
                lookback=mss_lookback,
            )
            mss_level = float(mss["level"]) if mss["level"] is not None else recent_high
            mss_ok = reclaim_ok and bool(mss["ok"])
            if mss_ok:
                zones.append("MSS")
            elif reclaim_ok:
                zones.append("RECLAIM")
            elif sweep_ok:
                zones.append("SWEEP")
            else:
                zones.append("NEUTRAL")
            sweep_levels.append(sweep_level)
            mss_levels.append(mss_level)
        out["ict_zone"] = zones
        out["swept_low_level"] = sweep_levels
        out["mss_level"] = mss_levels
        return out

    def panel_items(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        items = super().panel_items(snapshot)
        try:
            rsi_min = float(self._last_config.get("rsi_min", 0.0))
            rsi_max = float(self._last_config.get("rsi_max", 100.0))
        except (TypeError, ValueError):
            rsi_min, rsi_max = 0.0, 100.0
        try:
            vol_min_ratio = float(self._last_config.get("vol_min_ratio", 0.0))
        except (TypeError, ValueError):
            vol_min_ratio = 0.0

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
