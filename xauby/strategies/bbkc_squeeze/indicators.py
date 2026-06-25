"""Indicator calculations for the BBKC squeeze strategy."""
from __future__ import annotations

import logging
from typing import Any, Dict

import pandas as pd
import pandas_ta as ta

logger = logging.getLogger("xauby.strategies.bbkc_squeeze")


def classify_squeeze_zone(squeeze_on: bool, squeeze_fired: bool) -> str:
    """Classify squeeze state for chart/TUI zones."""
    if squeeze_on:
        return "SQUEEZE"
    if squeeze_fired:
        return "FIRED"
    return "NEUTRAL"


def compute_volume_ratio(volumes: pd.Series, sma_window: int = 20) -> float:
    """Last completed candle volume vs SMA of prior completed candles."""
    if volumes is None or len(volumes) < sma_window + 1:
        return 0.0
    completed = volumes.astype(float)
    last_vol = float(completed.iloc[-1])
    last_sma = float(completed.iloc[-(sma_window + 1):-1].mean())
    if last_sma <= 0 or pd.isna(last_sma):
        return 0.0
    return round(last_vol / last_sma, 4)


def compute_indicators(df: pd.DataFrame, config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Compute Bollinger + Keltner squeeze indicators for strategy decisions."""
    cfg = dict(config or {})
    bb_length = max(5, int(cfg.get("bb_length", 20)))
    bb_std = float(cfg.get("bb_std", 2.0))
    kc_length = max(5, int(cfg.get("kc_length", 20)))
    kc_mult = float(cfg.get("kc_mult", 1.5))
    squeeze_length = max(5, int(cfg.get("squeeze_length", bb_length)))
    rsi_period = max(2, int(cfg.get("rsi_period", 14)))
    atr_period = max(2, int(cfg.get("atr_period", 14)))
    vol_ma_period = max(2, int(cfg.get("volume_ma_period", 20)))
    release_window = max(1, int(cfg.get("release_window", 3)))

    res: Dict[str, Any] = {
        "close": 0.0,
        "bb_upper": 0.0,
        "bb_mid": 0.0,
        "bb_lower": 0.0,
        "kc_upper": 0.0,
        "kc_lower": 0.0,
        "squeeze_on": False,
        "squeeze_fired": False,
        "squeeze_on_prev": False,
        "squeeze_release": False,
        "squeeze_release_recent": False,
        "bars_since_release": None,
        "momentum": 0.0,
        "momentum_prev": 0.0,
        "rsi": 50.0,
        "atr": 0.0,
        "volume_ratio": 0.0,
        "zone": "NEUTRAL",
    }

    if df is None or df.empty or len(df) < squeeze_length + 5:
        return res

    try:
        out = df.copy()
        for col in ("open", "high", "low", "close", "volume"):
            out[col] = pd.to_numeric(out[col], errors="coerce")
        out = out.dropna(subset=["open", "high", "low", "close"])
        if len(out) < squeeze_length + 5:
            return res

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
        atr_series = ta.atr(high, low, close, length=atr_period)

        if bb is not None and not bb.empty:
            bb_upper_col = [c for c in bb.columns if c.startswith("BBU")]
            bb_mid_col = [c for c in bb.columns if c.startswith("BBM")]
            bb_lower_col = [c for c in bb.columns if c.startswith("BBL")]
            if bb_upper_col:
                res["bb_upper"] = float(bb[bb_upper_col[0]].iloc[-1])
            if bb_mid_col:
                res["bb_mid"] = float(bb[bb_mid_col[0]].iloc[-1])
            if bb_lower_col:
                res["bb_lower"] = float(bb[bb_lower_col[0]].iloc[-1])

        if kc is not None and not kc.empty:
            kc_upper_col = [c for c in kc.columns if c.startswith("KCU")]
            kc_lower_col = [c for c in kc.columns if c.startswith("KCL")]
            if kc_upper_col:
                res["kc_upper"] = float(kc[kc_upper_col[0]].iloc[-1])
            if kc_lower_col:
                res["kc_lower"] = float(kc[kc_lower_col[0]].iloc[-1])

        if squeeze is not None and not squeeze.empty:
            mom_col = [c for c in squeeze.columns if c.startswith("SQZ_") and "ON" not in c and "OFF" not in c and "NO" not in c]
            if mom_col:
                res["momentum"] = float(squeeze[mom_col[0]].iloc[-1])
                if len(squeeze) >= 2:
                    res["momentum_prev"] = float(squeeze[mom_col[0]].iloc[-2])
            if "SQZ_ON" in squeeze.columns:
                res["squeeze_on"] = bool(int(squeeze["SQZ_ON"].iloc[-1]) == 1)
                if len(squeeze) >= 2:
                    res["squeeze_on_prev"] = bool(int(squeeze["SQZ_ON"].iloc[-2]) == 1)
            if "SQZ_OFF" in squeeze.columns:
                res["squeeze_fired"] = bool(int(squeeze["SQZ_OFF"].iloc[-1]) == 1)

            if "SQZ_ON" in squeeze.columns and "SQZ_OFF" in squeeze.columns:
                squeeze_on_series = squeeze["SQZ_ON"].fillna(0).astype(int).eq(1)
                squeeze_off_series = squeeze["SQZ_OFF"].fillna(0).astype(int).eq(1)
                releases = squeeze_on_series.shift(1, fill_value=False) & squeeze_off_series
                recent_releases = releases.iloc[-release_window:]
                if bool(recent_releases.any()):
                    release_positions = [
                        pos for pos, released in enumerate(recent_releases.tolist()) if released
                    ]
                    res["squeeze_release_recent"] = True
                    res["bars_since_release"] = len(recent_releases) - 1 - release_positions[-1]

        res["squeeze_release"] = res["squeeze_on_prev"] and res["squeeze_fired"]
        res["close"] = float(close.iloc[-1])

        if rsi_series is not None and len(rsi_series) > 0:
            res["rsi"] = float(rsi_series.iloc[-1])
        if atr_series is not None and len(atr_series) > 0:
            res["atr"] = float(atr_series.iloc[-1])
        res["volume_ratio"] = compute_volume_ratio(volume, vol_ma_period)
        res["zone"] = classify_squeeze_zone(res["squeeze_on"], res["squeeze_fired"])
    except Exception as e:
        logger.error("Error computing BBKC squeeze indicators: %s", e)

    return res
