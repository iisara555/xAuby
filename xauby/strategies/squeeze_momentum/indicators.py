"""Squeeze Momentum (LazyBear) indicator math.

Faithful port of the TradingView open-source "Squeeze Momentum Indicator
[LazyBear]" (Jul 2014). Shared by the strategy plugin and the chart indicator
plugin so both draw and trade the exact same values.

Pieces:
* Bollinger Bands (SMA basis, population stdev) and Keltner Channels
  (SMA basis, SMA of range) — the squeeze is BB nested INSIDE KC.
* Momentum ``val`` = linear-regression endpoint of
  ``close - avg(avg(highest(high,n), lowest(low,n)), sma(close,n))``.

All calculations are causal (rolling windows over closed bars only), so there
is no look-ahead when the strategy reads the last row.
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd


def _sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n or n <= 0:
        return out
    c = np.cumsum(np.insert(x, 0, 0.0))
    out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def _rolling_std_pop(x: np.ndarray, n: int) -> np.ndarray:
    """Population standard deviation (ddof=0) to match Pine's stdev()."""
    out = np.full(len(x), np.nan)
    if len(x) < n or n <= 0:
        return out
    c1 = np.cumsum(np.insert(x, 0, 0.0))
    c2 = np.cumsum(np.insert(x * x, 0, 0.0))
    s = c1[n:] - c1[:-n]
    ss = c2[n:] - c2[:-n]
    var = ss / n - (s / n) ** 2
    out[n - 1:] = np.sqrt(np.clip(var, 0.0, None))
    return out


def _rolling_max(x: np.ndarray, n: int) -> np.ndarray:
    s = pd.Series(x)
    return s.rolling(n).max().to_numpy()


def _rolling_min(x: np.ndarray, n: int) -> np.ndarray:
    s = pd.Series(x)
    return s.rolling(n).min().to_numpy()


def _linreg_endpoint(y: np.ndarray, n: int) -> np.ndarray:
    """Vectorized ta.linreg(y, n, 0): value of the least-squares line at the
    window's last point. x runs 0..n-1 within each rolling window.

    NaN-safe: leading warmup NaNs in ``y`` would otherwise poison the cumsum for
    every later bar, so they are zero-filled for the sums and any window that
    still overlaps a NaN is masked back to NaN via a rolling valid-count.
    """
    out = np.full(len(y), np.nan)
    if len(y) < n or n <= 1:
        return out
    valid = np.isfinite(y).astype("float64")
    y0 = np.where(valid > 0, y, 0.0)
    idx = np.arange(len(y), dtype="float64")
    ry = np.cumsum(np.insert(y0, 0, 0.0))
    rjy = np.cumsum(np.insert(idx * y0, 0, 0.0))
    rvalid = np.cumsum(np.insert(valid, 0, 0.0))
    sum_y = ry[n:] - ry[:-n]
    sum_jy = rjy[n:] - rjy[:-n]
    win_valid = rvalid[n:] - rvalid[:-n]
    start = np.arange(0, len(y) - n + 1, dtype="float64")
    sum_xy = sum_jy - start * sum_y          # x = j - start
    sum_x = n * (n - 1) / 2.0
    sum_x2 = (n - 1) * n * (2 * n - 1) / 6.0
    sxx = sum_x2 - sum_x * sum_x / n
    slope = (sum_xy - sum_x * sum_y / n) / sxx
    intercept = (sum_y - slope * sum_x) / n
    endpoint = intercept + slope * (n - 1)
    endpoint[win_valid < n] = np.nan        # window contained a warmup NaN
    out[n - 1:] = endpoint
    return out


def compute_squeeze_frame(df: pd.DataFrame, config: Dict[str, Any] | None = None) -> pd.DataFrame:
    """Append LazyBear squeeze/momentum columns to ``df`` (copy)."""
    cfg = dict(config or {})
    bb_len = max(2, int(cfg.get("bb_length", 20)))
    bb_mult = float(cfg.get("bb_mult", 2.0))
    kc_len = max(2, int(cfg.get("kc_length", 20)))
    kc_mult = float(cfg.get("kc_mult", 1.5))
    use_tr = bool(cfg.get("use_true_range", True))
    atr_period = max(2, int(cfg.get("atr_period", 14)))

    out = df.copy()
    if out.empty:
        for col in ("mom", "mom_prev", "sqz_on", "sqz_off", "no_sqz", "atr", "sqz_state"):
            out[col] = np.nan if col in ("mom", "mom_prev", "atr") else False
        out["sqz_state"] = "NONE"
        return out

    high = pd.to_numeric(out["high"], errors="coerce").to_numpy("float64")
    low = pd.to_numeric(out["low"], errors="coerce").to_numpy("float64")
    close = pd.to_numeric(out["close"], errors="coerce").to_numpy("float64")

    basis = _sma(close, bb_len)
    dev = bb_mult * _rolling_std_pop(close, bb_len)
    upper_bb = basis + dev
    lower_bb = basis - dev

    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum.reduce([high - low, np.abs(high - prev_close), np.abs(low - prev_close)])
    rng = tr if use_tr else (high - low)
    ma = _sma(close, kc_len)
    rangema = _sma(rng, kc_len)
    upper_kc = ma + rangema * kc_mult
    lower_kc = ma - rangema * kc_mult

    sqz_on = (lower_bb > lower_kc) & (upper_bb < upper_kc)
    sqz_off = (lower_bb < lower_kc) & (upper_bb > upper_kc)
    no_sqz = ~(sqz_on | sqz_off)

    hh = _rolling_max(high, kc_len)
    ll = _rolling_min(low, kc_len)
    avg_hl = (hh + ll) / 2.0
    sma_close = _sma(close, kc_len)
    mid = (avg_hl + sma_close) / 2.0
    de = close - mid
    mom = _linreg_endpoint(de, kc_len)

    out["atr"] = _sma(tr, atr_period)
    out["mom"] = mom
    out["mom_prev"] = np.roll(mom, 1)
    out.loc[out.index[0], "mom_prev"] = np.nan
    out["sqz_on"] = sqz_on
    out["sqz_off"] = sqz_off
    out["no_sqz"] = no_sqz
    state = np.where(sqz_on, "ON", np.where(sqz_off, "OFF", "NONE"))
    out["sqz_state"] = state
    # Green histogram = positive momentum (buy side); red = negative.
    out["mom_zone"] = np.where(mom > 0, "GREEN", np.where(mom < 0, "RED", "FLAT"))
    return out


def squeeze_snapshot(df: pd.DataFrame, config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Last-bar scalar snapshot for the strategy/TUI."""
    frame = compute_squeeze_frame(df, config)
    if frame.empty:
        return {"mom": 0.0, "mom_prev": 0.0, "atr": 0.0, "sqz_state": "NONE", "mom_zone": "FLAT"}
    row = frame.iloc[-1]

    def _f(key: str) -> float:
        v = row.get(key)
        return 0.0 if v is None or pd.isna(v) else float(v)

    return {
        "mom": _f("mom"),
        "mom_prev": _f("mom_prev"),
        "atr": _f("atr"),
        "sqz_state": str(row.get("sqz_state", "NONE")),
        "sqz_on": bool(row.get("sqz_on", False)),
        "sqz_off": bool(row.get("sqz_off", False)),
        "mom_zone": str(row.get("mom_zone", "FLAT")),
        "prev_sqz_on": bool(frame.iloc[-2]["sqz_on"]) if len(frame) >= 2 else False,
    }
