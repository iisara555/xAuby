"""UT Bot Alerts indicator math.

Port of the widely-circulated TradingView "UT Bot Alerts" study (Yo_adriiiiaan
/ QuantNomad lineage), which is an ATR trailing stop that ratchets in the
direction of the trade and flips side when price closes through it.

    nLoss = key_value * ATR(atr_period)
    stop  = ratchet(prev_stop, src, prev_src, nLoss)
    pos   = +1 when src closes above the stop, -1 when it closes below

The ratchet is genuinely sequential — each stop depends on the previous one —
so it is written as an explicit loop over numpy arrays rather than forced into
a vectorised form that would not be faithful.

All values are causal: bar ``i`` uses only bars ``<= i``, so reading the last
row of the frame introduces no look-ahead.

Memory note: the stop is recursive, so in principle it depends on all history.
In practice the ratchet is reset by every flip (each flip re-seeds the stop
from the current bar), which bounds its memory to the time since the last
flip. That is what makes it safe to evaluate over a bounded context window.
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd


def _wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    """Wilder's ATR — the smoothing Pine's ta.atr() uses."""
    size = len(close)
    out = np.full(size, np.nan)
    if size == 0 or n <= 0:
        return out
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum.reduce(
        [high - low, np.abs(high - prev_close), np.abs(low - prev_close)]
    )
    if size < n:
        return out
    seed = float(np.mean(tr[:n]))
    out[n - 1] = seed
    acc = seed
    for i in range(n, size):
        acc = (acc * (n - 1) + tr[i]) / n
        out[i] = acc
    return out


def _heikin_ashi_close(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> np.ndarray:
    """HA close only — that is all UT Bot's ``h`` option consumes as ``src``."""
    return (open_ + high + low + close) / 4.0


def _ut_bot_stop(src: np.ndarray, nloss: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The UT Bot trailing-stop recursion and its resulting position series."""
    size = len(src)
    stop = np.full(size, np.nan)
    pos = np.zeros(size, dtype="int8")
    if size == 0:
        return stop, pos

    start = 0
    while start < size and not (np.isfinite(nloss[start]) and np.isfinite(src[start])):
        start += 1
    if start >= size:
        return stop, pos

    stop[start] = src[start] - nloss[start]
    pos[start] = 1
    for i in range(start + 1, size):
        prev_stop = stop[i - 1]
        loss = nloss[i]
        if not np.isfinite(loss):
            stop[i] = prev_stop
            pos[i] = pos[i - 1]
            continue
        cur, prev = src[i], src[i - 1]
        if cur > prev_stop and prev > prev_stop:
            stop[i] = max(prev_stop, cur - loss)
        elif cur < prev_stop and prev < prev_stop:
            stop[i] = min(prev_stop, cur + loss)
        elif cur > prev_stop:
            stop[i] = cur - loss
        else:
            stop[i] = cur + loss

        if prev <= prev_stop and cur > stop[i]:
            pos[i] = 1
        elif prev >= prev_stop and cur < stop[i]:
            pos[i] = -1
        else:
            pos[i] = pos[i - 1]
    return stop, pos


def compute_ut_bot_frame(
    df: pd.DataFrame, config: Dict[str, Any] | None = None
) -> pd.DataFrame:
    """Append UT Bot columns to ``df`` (copy)."""
    cfg = dict(config or {})
    key_value = float(cfg.get("key_value", 1.0))
    atr_period = max(1, int(cfg.get("atr_period", 10)))
    atr_risk_period = max(1, int(cfg.get("atr_period_risk", 14)))
    use_ha = bool(cfg.get("use_heikin_ashi", False))

    out = df.copy()
    if out.empty:
        for col in ("ut_atr", "ut_nloss", "ut_stop", "ut_atr_risk"):
            out[col] = np.nan
        out["ut_pos"] = 0
        out["ut_flip_up"] = False
        out["ut_flip_down"] = False
        out["ut_zone"] = "NONE"
        return out

    open_ = pd.to_numeric(out["open"], errors="coerce").to_numpy("float64")
    high = pd.to_numeric(out["high"], errors="coerce").to_numpy("float64")
    low = pd.to_numeric(out["low"], errors="coerce").to_numpy("float64")
    close = pd.to_numeric(out["close"], errors="coerce").to_numpy("float64")

    src = _heikin_ashi_close(open_, high, low, close) if use_ha else close
    atr = _wilder_atr(high, low, close, atr_period)
    nloss = key_value * atr
    stop, pos = _ut_bot_stop(src, nloss)

    prev_pos = np.roll(pos, 1)
    prev_pos[0] = 0

    out["ut_atr"] = atr
    out["ut_atr_risk"] = _wilder_atr(high, low, close, atr_risk_period)
    out["ut_nloss"] = nloss
    out["ut_stop"] = stop
    out["ut_pos"] = pos
    out["ut_flip_up"] = (pos == 1) & (prev_pos != 1)
    out["ut_flip_down"] = (pos == -1) & (prev_pos != -1)
    out["ut_zone"] = np.where(pos > 0, "LONG", np.where(pos < 0, "SHORT", "NONE"))
    return out


def ut_bot_snapshot(
    df: pd.DataFrame, config: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    """Last-bar scalar snapshot for the strategy/TUI."""
    cfg = dict(config or {})
    max_calc = int(cfg.get("max_calc_bars", 0) or 0)
    frame_src = df.tail(max_calc) if max_calc > 0 and len(df) > max_calc else df
    frame = compute_ut_bot_frame(frame_src, cfg)
    if frame.empty:
        return {
            "atr": 0.0, "atr_risk": 0.0, "stop": 0.0, "pos": 0, "zone": "NONE",
            "flip_up": False, "flip_down": False, "close": 0.0, "bars_in_pos": 0,
        }
    row = frame.iloc[-1]

    def _f(key: str) -> float:
        v = row.get(key)
        return 0.0 if v is None or pd.isna(v) else float(v)

    pos = int(row.get("ut_pos", 0) or 0)
    # How long the current side has held — lets the strategy require a flip to
    # persist before acting on it.
    bars_in_pos = 0
    pos_series = frame["ut_pos"].to_numpy()
    for value in pos_series[::-1]:
        if int(value) != pos:
            break
        bars_in_pos += 1

    return {
        "atr": _f("ut_atr"),
        "atr_risk": _f("ut_atr_risk") or _f("ut_atr"),
        "stop": _f("ut_stop"),
        "pos": pos,
        "zone": str(row.get("ut_zone", "NONE")),
        "flip_up": bool(row.get("ut_flip_up", False)),
        "flip_down": bool(row.get("ut_flip_down", False)),
        "close": _f("close"),
        "bars_in_pos": bars_in_pos,
    }
