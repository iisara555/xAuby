"""Indicator calculations for the BB + RSI mean reversion strategy."""
from __future__ import annotations

import logging
from typing import Any, Dict

import pandas as pd
import pandas_ta as ta

logger = logging.getLogger("xauby.strategies.bbrsi_mean_reversion")


def classify_reversion_zone(
    close: float,
    bb_lower: float,
    bb_mid: float,
    bb_upper: float,
    rsi: float,
    *,
    rsi_oversold: float,
    rsi_overbought: float,
) -> str:
    """Classify mean-reversion state for chart/TUI zones."""
    if rsi <= rsi_oversold or (bb_lower > 0 and close <= bb_lower):
        return "OVERSOLD"
    if rsi >= rsi_overbought or (bb_upper > 0 and close >= bb_upper):
        return "OVERBOUGHT"
    if bb_mid > 0 and abs(close - bb_mid) / bb_mid <= 0.005:
        return "NEUTRAL"
    return "NEUTRAL"


def compute_volume_ratio(volumes: pd.Series, sma_window: int = 20) -> float:
    if volumes is None or len(volumes) < sma_window + 1:
        return 0.0
    completed = volumes.astype(float)
    last_vol = float(completed.iloc[-1])
    last_sma = float(completed.iloc[-(sma_window + 1):-1].mean())
    if last_sma <= 0 or pd.isna(last_sma):
        return 0.0
    return round(last_vol / last_sma, 4)


def compute_indicators(df: pd.DataFrame, config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Compute Bollinger Bands + RSI indicators for mean-reversion decisions."""
    cfg = dict(config or {})
    bb_length = max(5, int(cfg.get("bb_length", 20)))
    bb_std = float(cfg.get("bb_std", 2.0))
    rsi_period = max(2, int(cfg.get("rsi_period", 14)))
    atr_period = max(2, int(cfg.get("atr_period", 14)))
    vol_ma_period = max(2, int(cfg.get("volume_ma_period", 20)))
    rsi_oversold = float(cfg.get("rsi_oversold", 30.0))
    rsi_overbought = float(cfg.get("rsi_overbought", 70.0))

    res: Dict[str, Any] = {
        "close": 0.0,
        "bb_upper": 0.0,
        "bb_mid": 0.0,
        "bb_lower": 0.0,
        "bb_pct": 0.5,
        "rsi": 50.0,
        "atr": 0.0,
        "volume_ratio": 0.0,
        "zone": "NEUTRAL",
        "below_lower_band": False,
        "above_mid_band": False,
        "above_upper_band": False,
    }

    if df is None or df.empty or len(df) < bb_length + 5:
        return res

    try:
        out = df.copy()
        for col in ("open", "high", "low", "close", "volume"):
            out[col] = pd.to_numeric(out[col], errors="coerce")
        out = out.dropna(subset=["open", "high", "low", "close"])
        if len(out) < bb_length + 5:
            return res

        high = out["high"].astype(float)
        low = out["low"].astype(float)
        close = out["close"].astype(float)
        volume = out["volume"].astype(float)

        bb = ta.bbands(close, length=bb_length, std=bb_std)
        rsi_series = ta.rsi(close, length=rsi_period)
        atr_series = ta.atr(high, low, close, length=atr_period)

        if bb is not None and not bb.empty:
            upper_col = [c for c in bb.columns if c.startswith("BBU")]
            mid_col = [c for c in bb.columns if c.startswith("BBM")]
            lower_col = [c for c in bb.columns if c.startswith("BBL")]
            pct_col = [c for c in bb.columns if c.startswith("BBP")]
            if upper_col:
                res["bb_upper"] = float(bb[upper_col[0]].iloc[-1])
            if mid_col:
                res["bb_mid"] = float(bb[mid_col[0]].iloc[-1])
            if lower_col:
                res["bb_lower"] = float(bb[lower_col[0]].iloc[-1])
            if pct_col:
                res["bb_pct"] = float(bb[pct_col[0]].iloc[-1])

        if rsi_series is not None and len(rsi_series) > 0:
            res["rsi"] = float(rsi_series.iloc[-1])
        if atr_series is not None and len(atr_series) > 0:
            res["atr"] = float(atr_series.iloc[-1])

        res["close"] = float(close.iloc[-1])
        res["volume_ratio"] = compute_volume_ratio(volume, vol_ma_period)
        res["below_lower_band"] = res["bb_lower"] > 0 and res["close"] <= res["bb_lower"]
        res["above_mid_band"] = res["bb_mid"] > 0 and res["close"] >= res["bb_mid"]
        res["above_upper_band"] = res["bb_upper"] > 0 and res["close"] >= res["bb_upper"]
        res["zone"] = classify_reversion_zone(
            res["close"],
            res["bb_lower"],
            res["bb_mid"],
            res["bb_upper"],
            res["rsi"],
            rsi_oversold=rsi_oversold,
            rsi_overbought=rsi_overbought,
        )
    except Exception as e:
        logger.error("Error computing BBRSI mean reversion indicators: %s", e)

    return res
