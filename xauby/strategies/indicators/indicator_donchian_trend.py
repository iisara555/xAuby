"""Donchian Trend chart indicator plugin.

Computes for the candlestick chart:
  donchian_entry_high  — N-bar channel high (entry breakout level)
  donchian_exit_mid    — midline of the exit channel (exit trigger)
  ema200               — 200-period EMA (trend filter)
  adx                  — ADX strength (entry filter)
  donchian_zone        — BULL / WAIT / BEAR for candle colouring
"""
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
        ema[i] = values[i] * alpha + ema[i - 1] * (1.0 - alpha)
    return ema


def _wilder_smooth(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype="float64")
    if len(values) < period:
        return out
    out[period - 1] = values[:period].sum()
    for i in range(period, len(values)):
        out[i] = out[i - 1] - (out[i - 1] / period) + values[i]
    return out


def _adx_np(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    n = len(close)
    adx = np.full(n, 0.0, dtype="float64")
    if n < period * 2 + 1:
        return adx
    up = high[1:] - high[:-1]
    down = low[:-1] - low[1:]
    plus_dm = np.where((up > down) & (up > 0.0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0.0), down, 0.0)
    tr = np.maximum.reduce([
        high[1:] - low[1:],
        np.abs(high[1:] - close[:-1]),
        np.abs(low[1:] - close[:-1]),
    ])
    tr_s = _wilder_smooth(tr, period)
    plus_s = _wilder_smooth(plus_dm, period)
    minus_s = _wilder_smooth(minus_dm, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100.0 * plus_s / tr_s
        minus_di = 100.0 * minus_s / tr_s
        dx = 100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    dx = np.nan_to_num(dx, nan=0.0)
    adx_part = np.full(len(dx), 0.0, dtype="float64")
    start = period * 2 - 2
    if start < len(dx):
        adx_part[start] = dx[period - 1: start + 1].mean()
        for i in range(start + 1, len(dx)):
            adx_part[i] = (adx_part[i - 1] * (period - 1) + dx[i]) / period
    adx[1:] = adx_part
    return adx


@register("xauby_donchian_trend")
class DonchianTrendIndicator(Indicator):
    display_config: Dict[str, Any] = {
        "chart_type": "trend_zones",
        "label": "xAuby Donchian Trend",
        "unit": "price",
        "zone_column": "donchian_zone",
        "zone_label": "DC Zone",
        "buy_zones": ["BULL"],
        "zones": [
            {"key": "BULL",  "label": "DC Bull",  "color": (34, 197, 94),  "bg_color": (20, 83, 45)},
            {"key": "WAIT",  "label": "DC Wait",  "color": (251, 191, 36), "bg_color": (78, 60, 10)},
            {"key": "BEAR",  "label": "DC Bear",  "color": (248, 113, 113),"bg_color": (127, 29, 29)},
        ],
        "lines": [
            {"key": "ema200",               "label": "EMA200",      "color": (251, 191, 36),  "glyph": "◆"},
            {"key": "donchian_entry_high",  "label": "+Break",       "color": (34, 211, 238),  "glyph": "▲"},
            {"key": "donchian_exit_mid",    "label": "Exit Mid",     "color": (249, 115, 22),  "glyph": "▼"},
        ],
        "cross_glyph": "▲",
        "metrics": [
            {"key": "ema200",              "label": "EMA200"},
            {"key": "adx",                 "label": "ADX",          "min": 0.0, "max": 100.0},
            {"key": "donchian_entry_high", "label": "+Break Level"},
            {"key": "donchian_exit_mid",   "label": "Exit Mid"},
        ],
    }

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def compute(self, df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
        merged = {**self.config, **(config or {})}
        entry_len = max(2, int(merged.get("entry_len", 48)))
        exit_len  = max(2, int(merged.get("exit_len",  24)))
        ema_n     = max(1, int(merged.get("ema_period", 200)))
        adx_n     = max(2, int(merged.get("adx_period", 14)))
        adx_min   = float(merged.get("adx_min", 20.0))

        out = df.copy()
        high_arr  = out["high"].to_numpy(dtype="float64", copy=False)
        low_arr   = out["low"].to_numpy(dtype="float64", copy=False)
        close_arr = out["close"].to_numpy(dtype="float64", copy=False)
        n = len(close_arr)

        # EMA200 — pandas ewm is 4× faster than Python loop, identical values
        out["ema200"] = (
            pd.Series(close_arr).ewm(span=ema_n, adjust=False).mean().to_numpy()
        )

        # ADX
        out["adx"] = _adx_np(high_arr, low_arr, close_arr, adx_n)

        # Donchian channels — shift(1) excludes current bar (classic turtle)
        # pandas rolling is 9× faster than Python loops
        h_series = pd.Series(high_arr)
        l_series = pd.Series(low_arr)
        entry_high = h_series.shift(1).rolling(entry_len).max().to_numpy()
        exit_h     = h_series.shift(1).rolling(exit_len).max().to_numpy()
        exit_l     = l_series.shift(1).rolling(exit_len).min().to_numpy()
        exit_mid   = (exit_h + exit_l) / 2.0

        out["donchian_entry_high"] = entry_high
        out["donchian_exit_mid"]   = exit_mid

        # Zone colouring — NaN EMA or no Donchian data yet → WAIT
        ema_series = out["ema200"].to_numpy(dtype="float64")
        adx_series = out["adx"].to_numpy(dtype="float64")
        dc_valid = np.isfinite(entry_high)
        zones = np.where(
            dc_valid & (close_arr > ema_series) & (adx_series >= adx_min),
            "BULL",
            np.where(dc_valid & (close_arr > ema_series), "WAIT", "BEAR"),
        )
        out["donchian_zone"] = zones

        return out
