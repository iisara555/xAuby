"""Dual Thrust range-breakout indicator math.

Classic intraday breakout system. Over the last N *completed* sessions:

    HH = max(session high)    HC = max(session close)
    LC = min(session close)   LL = min(session low)
    Range    = max(HH - LC, HC - LL)
    BuyLine  = session_open + k_upper * Range
    SellLine = session_open - k_lower * Range

Crypto trades continuously, so a "session" is the UTC day (96 bars at 15m).

Look-ahead is the primary hazard here, and it is guarded structurally: the
per-session aggregates are shifted by one session *before* the rolling window
is taken, so a session's own high/low/close can never enter its own breakout
lines. Only ``session_open`` comes from the current session, which is
legitimately known at the session's first bar.
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd


def _wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    size = len(close)
    out = np.full(size, np.nan)
    if size == 0 or n <= 0 or size < n:
        return out
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum.reduce(
        [high - low, np.abs(high - prev_close), np.abs(low - prev_close)]
    )
    seed = float(np.mean(tr[:n]))
    out[n - 1] = seed
    acc = seed
    for i in range(n, size):
        acc = (acc * (n - 1) + tr[i]) / n
        out[i] = acc
    return out


def _session_key(out: pd.DataFrame) -> pd.Series:
    """UTC calendar day per bar. Mirrors xauby_vwap_pullback's resolution."""
    if "open_time" in out.columns:
        raw = out["open_time"]
        numeric = pd.to_numeric(raw, errors="coerce")
        if numeric.notna().any():
            unit = "ms" if float(numeric.abs().max() or 0.0) > 10_000_000_000 else "s"
            return pd.to_datetime(numeric, unit=unit, utc=True, errors="coerce").dt.date
        return pd.to_datetime(raw, utc=True, errors="coerce").dt.date
    if "timestamp" in out.columns:
        numeric = pd.to_numeric(out["timestamp"], errors="coerce")
        unit = "ms" if float(numeric.abs().max() or 0.0) > 10_000_000_000 else "s"
        return pd.to_datetime(numeric, unit=unit, utc=True, errors="coerce").dt.date
    return pd.Series(0, index=out.index)


def compute_dual_thrust_frame(
    df: pd.DataFrame, config: Dict[str, Any] | None = None
) -> pd.DataFrame:
    """Append Dual Thrust columns to ``df`` (copy)."""
    cfg = dict(config or {})
    lookback = max(1, int(cfg.get("lookback_days", 4)))
    k_upper = float(cfg.get("k_upper", 0.5))
    k_lower = float(cfg.get("k_lower", 0.5))
    atr_period = max(2, int(cfg.get("atr_period", 14)))

    out = df.copy()
    if out.empty:
        for col in ("dt_session_open", "dt_range", "dt_buy_line", "dt_sell_line", "atr"):
            out[col] = np.nan
        out["dt_zone"] = "INSIDE"
        out["dt_bar_in_session"] = 0
        return out

    high = pd.to_numeric(out["high"], errors="coerce")
    low = pd.to_numeric(out["low"], errors="coerce")
    close = pd.to_numeric(out["close"], errors="coerce")
    open_ = pd.to_numeric(out["open"], errors="coerce")

    session = _session_key(out)
    out["_session"] = session

    grouped = out.groupby("_session", sort=True)
    agg = pd.DataFrame(
        {
            "hh": high.groupby(session).max(),
            "ll": low.groupby(session).min(),
            "hc": close.groupby(session).max(),
            "lc": close.groupby(session).min(),
        }
    ).sort_index()

    # Shift by one session FIRST: a session must never see itself.
    prior = agg.shift(1)
    roll_hh = prior["hh"].rolling(lookback, min_periods=lookback).max()
    roll_ll = prior["ll"].rolling(lookback, min_periods=lookback).min()
    roll_hc = prior["hc"].rolling(lookback, min_periods=lookback).max()
    roll_lc = prior["lc"].rolling(lookback, min_periods=lookback).min()
    rng = np.maximum(roll_hh - roll_lc, roll_hc - roll_ll)

    session_open = grouped["open"].transform("first")
    bar_in_session = grouped.cumcount()

    rng_per_bar = out["_session"].map(rng)
    out["dt_range"] = pd.to_numeric(rng_per_bar, errors="coerce").to_numpy("float64")
    out["dt_session_open"] = pd.to_numeric(session_open, errors="coerce").to_numpy("float64")
    out["dt_bar_in_session"] = bar_in_session.to_numpy("int64")
    out["dt_buy_line"] = out["dt_session_open"] + k_upper * out["dt_range"]
    out["dt_sell_line"] = out["dt_session_open"] - k_lower * out["dt_range"]

    out["atr"] = _wilder_atr(
        high.to_numpy("float64"), low.to_numpy("float64"),
        close.to_numpy("float64"), atr_period,
    )

    close_np = close.to_numpy("float64")
    buy_np = out["dt_buy_line"].to_numpy("float64")
    sell_np = out["dt_sell_line"].to_numpy("float64")
    out["dt_zone"] = np.where(
        close_np > buy_np, "LONG_BREAK",
        np.where(close_np < sell_np, "SHORT_BREAK", "INSIDE"),
    )
    out.drop(columns=["_session"], inplace=True)
    return out


def dual_thrust_snapshot(
    df: pd.DataFrame, config: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    """Last-bar scalar snapshot for the strategy/TUI."""
    cfg = dict(config or {})
    max_calc = int(cfg.get("max_calc_bars", 0) or 0)
    frame_src = df.tail(max_calc) if max_calc > 0 and len(df) > max_calc else df
    frame = compute_dual_thrust_frame(frame_src, cfg)
    if frame.empty:
        return {
            "session_open": 0.0, "range": 0.0, "buy_line": 0.0, "sell_line": 0.0,
            "zone": "INSIDE", "atr": 0.0, "close": 0.0, "bar_in_session": 0,
            "entered_long_this_session": False, "entered_short_this_session": False,
            "is_session_last_bar": False,
        }
    row = frame.iloc[-1]

    def _f(key: str) -> float:
        v = row.get(key)
        return 0.0 if v is None or pd.isna(v) else float(v)

    # "Already broke out this session" is derived from the frame rather than
    # instance state — a Strategy is a pure analyst and may be reconstructed.
    bar_in_session = int(row.get("dt_bar_in_session", 0) or 0)
    session_slice = frame.iloc[len(frame) - bar_in_session - 1 :] if bar_in_session >= 0 else frame
    zones = session_slice["dt_zone"].astype(str)
    bars_per_session = max(1, int(cfg.get("bars_per_session", 96)))

    return {
        "session_open": _f("dt_session_open"),
        "range": _f("dt_range"),
        "buy_line": _f("dt_buy_line"),
        "sell_line": _f("dt_sell_line"),
        "zone": str(row.get("dt_zone", "INSIDE")),
        "atr": _f("atr"),
        "close": _f("close"),
        "bar_in_session": bar_in_session,
        "entered_long_this_session": bool((zones.iloc[:-1] == "LONG_BREAK").any())
        if len(zones) > 1 else False,
        "entered_short_this_session": bool((zones.iloc[:-1] == "SHORT_BREAK").any())
        if len(zones) > 1 else False,
        "is_session_last_bar": bar_in_session >= bars_per_session - 1,
    }
