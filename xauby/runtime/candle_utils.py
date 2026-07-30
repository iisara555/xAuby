"""Helpers for candle DataFrames shared by the live engine and replay paths.

The exchange returns the still-forming bar as the last kline row. Strategies
must only see closed candles (``strategy.use_closed_candles``), so both live
ticks and replay validation trim that row through the same helper to stay in
agreement.
"""
from __future__ import annotations

import time
from typing import Optional, Tuple

import pandas as pd

from xauby.strategies.timeframes import timeframe_seconds


def drop_forming_bar(
    df: Optional[pd.DataFrame],
    timeframe: str,
    *,
    now_ts: Optional[float] = None,
) -> Tuple[Optional[pd.DataFrame], bool]:
    """Drop the final row when it is a still-forming candle.

    A bar is forming when its close time (open ``timestamp`` + timeframe) is
    still in the future at ``now_ts``. The DataFrame is returned untouched when
    the last bar has already closed, so databases that only store closed
    candles never lose a real bar.

    Returns ``(df, dropped)``.
    """
    if df is None or len(df) == 0:
        return df, False
    now = time.time() if now_ts is None else float(now_ts)
    try:
        last_open = float(df["timestamp"].iloc[-1])
    except (KeyError, TypeError, ValueError):
        return df, False
    if last_open + timeframe_seconds(timeframe) > now:
        return df.iloc[:-1], True
    return df, False


def use_closed_candles(cfg: dict) -> bool:
    """Resolve ``strategy.use_closed_candles`` (default: enabled)."""
    return bool(((cfg or {}).get("strategy") or {}).get("use_closed_candles", True))


def candle_is_stale(
    last_open_ts: int,
    timeframe: str,
    *,
    now: Optional[float] = None,
    factor: float = 2.5,
) -> bool:
    """True when the newest closed candle is too old (candle sync fell behind).

    ``last_open_ts`` is the open timestamp (seconds) of the newest CLOSED candle.
    Between closed candles the age peaks at ~2x the timeframe, so ``factor``
    above 2.0 (default 2.5) flags a genuinely missed candle without false
    positives. Returns False when there is no candle yet or the timeframe is
    unknown.
    """
    if last_open_ts <= 0:
        return False
    tf = timeframe_seconds(timeframe, default=0)
    if tf <= 0:
        return False
    now_t = time.time() if now is None else float(now)
    return (now_t - float(last_open_ts)) > factor * tf
