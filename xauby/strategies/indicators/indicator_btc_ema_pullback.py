"""BTC EMA pullback chart indicator plugin."""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from xauby.strategies.indicators.base import Indicator
from xauby.strategies.indicators.registry import register


@register("btc_ema_pullback")
class BTCEMAPullbackIndicator(Indicator):
    display_config: Dict[str, Any] = {
        "chart_type": "ema_pullback",
        "label": "BTC EMA Pullback",
        "unit": "price",
        "zone_column": "ema_pullback_zone",
        "zone_label": "EMA Pullback",
        "buy_zones": ["RECLAIM"],
        "zones": [
            {"key": "TREND", "label": "Trend", "color": (34, 197, 94), "bg_color": (20, 83, 45)},
            {"key": "PULLBACK", "label": "Pullback", "color": (251, 191, 36), "bg_color": (113, 63, 18)},
            {"key": "RECLAIM", "label": "Reclaim", "color": (52, 211, 153), "bg_color": (6, 78, 59)},
            {"key": "NEUTRAL", "label": "Neutral", "color": (148, 163, 184), "bg_color": (51, 65, 85)},
        ],
        "lines": [
            {"key": "ema_fast", "label": "EMA Fast", "color": (34, 211, 238), "glyph": "●"},
            {"key": "ema_slow", "label": "EMA Slow", "color": (96, 165, 250), "glyph": "◆"},
            {"key": "ema_trend", "label": "EMA Trend", "color": (251, 191, 36), "glyph": "■"},
        ],
        "metrics": [
            {"key": "ema_pullback_zone", "label": "EMA Zone"},
            {"key": "rsi", "label": "RSI"},
            {"key": "volume_ratio", "label": "Vol Ratio"},
        ],
    }

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0.0).rolling(period).mean()
        loss = (-delta.clip(upper=0.0)).rolling(period).mean()
        rs = gain / loss.replace(0.0, pd.NA)
        return (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

    def compute(self, df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
        merged = dict(self.config)
        merged.update(config or {})
        ema_fast_n = max(1, int(merged.get("ema_fast", 20)))
        ema_slow_n = max(1, int(merged.get("ema_slow", 55)))
        ema_trend_n = max(1, int(merged.get("ema_trend", 200)))
        atr_n = max(1, int(merged.get("atr_period", 14)))
        rsi_n = max(2, int(merged.get("rsi_period", 14)))
        vol_n = max(2, int(merged.get("volume_ma_period", 20)))
        pullback_lookback = max(1, int(merged.get("pullback_lookback", 5)))
        pullback_atr_mult = float(merged.get("pullback_atr_mult", 0.7))
        breakout_lookback = max(1, int(merged.get("breakout_lookback", 3)))

        out = df.copy()
        for col in ("open", "high", "low", "close", "volume"):
            out[col] = pd.to_numeric(out[col], errors="coerce")
        close = out["close"].astype(float)
        high = out["high"].astype(float)
        low = out["low"].astype(float)
        volume = out["volume"].astype(float)
        out["ema_fast"] = close.ewm(span=ema_fast_n, adjust=False).mean()
        out["ema_slow"] = close.ewm(span=ema_slow_n, adjust=False).mean()
        out["ema_trend"] = close.ewm(span=ema_trend_n, adjust=False).mean()
        prev_close = close.shift(1)
        tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        out["atr"] = tr.rolling(atr_n).mean()
        out["rsi"] = self._rsi(close, rsi_n)
        vol_ma = volume.rolling(vol_n).mean()
        out["volume_ratio"] = volume / vol_ma.replace(0.0, pd.NA)

        zones = []
        for i, row in out.iterrows():
            idx = out.index.get_loc(i)
            c = float(row["close"]) if not pd.isna(row["close"]) else 0.0
            fast = float(row.get("ema_fast", 0.0)) if not pd.isna(row.get("ema_fast")) else 0.0
            slow = float(row.get("ema_slow", 0.0)) if not pd.isna(row.get("ema_slow")) else 0.0
            trend = float(row.get("ema_trend", 0.0)) if not pd.isna(row.get("ema_trend")) else 0.0
            atr = float(row.get("atr", 0.0)) if not pd.isna(row.get("atr")) else 0.0
            trend_ok = c > trend and fast > slow and slow > trend
            start = max(0, idx - pullback_lookback + 1)
            pull_slice = out.iloc[start: idx + 1]
            pullback_ok = bool((pull_slice["low"] <= (pull_slice["ema_fast"] + atr * pullback_atr_mult)).any()) if atr > 0 else False
            pivot = out.iloc[max(0, idx - breakout_lookback): idx]
            pivot_high = float(pivot["high"].max()) if not pivot.empty else c
            reclaim_ok = c > fast and c > pivot_high
            if trend_ok and pullback_ok and reclaim_ok:
                zones.append("RECLAIM")
            elif trend_ok and pullback_ok:
                zones.append("PULLBACK")
            elif trend_ok:
                zones.append("TREND")
            else:
                zones.append("NEUTRAL")
        out["ema_pullback_zone"] = zones
        return out
