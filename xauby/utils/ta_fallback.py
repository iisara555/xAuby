"""Pure-pandas fallback for the pandas_ta functions the CDC plugin uses.

pandas_ta is a git-sourced dependency that some environments (CI sandboxes,
minimal containers) cannot install. This module replicates the exact math of
``pandas_ta.ema`` / ``pandas_ta.rsi`` / ``pandas_ta.atr`` (0.3.x / MerlinR
fork) so the CDC Action Zone plugin — and its backtests — produce identical
values with or without the real library:

- ``ema``: SMA-seeded EMA (first ``length`` values seed an SMA, then a
  recursive ``ewm(span=length, adjust=False)`` continues from it).
- ``rsi``: Wilder's RSI via RMA smoothing (``ewm(alpha=1/length)``, pandas
  default ``adjust=True`` — matching pandas_ta's ``rma``).
- ``atr``: true range smoothed with the same RMA.

``tests/test_ta_fallback.py`` asserts parity against the real pandas_ta when
it is importable, so any drift between the two implementations fails CI on
machines that have the dependency.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def ema(close: pd.Series, length: int = 10) -> Optional[pd.Series]:
    """SMA-seeded EMA, matching ``pandas_ta.ema(..., sma=True, adjust=False)``."""
    if close is None or length <= 0:
        return None
    close = pd.Series(close, dtype="float64")
    if len(close) < length:
        return pd.Series(np.nan, index=close.index, name=f"EMA_{length}")
    seeded = close.copy()
    sma_seed = seeded.iloc[:length].mean()
    seeded.iloc[: length - 1] = np.nan
    seeded.iloc[length - 1] = sma_seed
    out = seeded.ewm(span=length, min_periods=length, adjust=False).mean()
    out.name = f"EMA_{length}"
    return out


def _rma(series: pd.Series, length: int) -> pd.Series:
    """Wilder smoothing as pandas_ta implements it (``adjust`` left at default True)."""
    return series.ewm(alpha=1.0 / length, min_periods=length).mean()


def rsi(close: pd.Series, length: int = 14) -> Optional[pd.Series]:
    """Wilder RSI, matching ``pandas_ta.rsi`` (RMA-smoothed gains/losses)."""
    if close is None or length <= 0:
        return None
    close = pd.Series(close, dtype="float64")
    diff = close.diff()
    positive = diff.clip(lower=0.0)
    negative = -diff.clip(upper=0.0)
    pos_rma = _rma(positive, length)
    neg_rma = _rma(negative, length)
    denom = pos_rma + neg_rma
    out = 100.0 * pos_rma / denom.where(denom != 0, np.nan)
    out.name = f"RSI_{length}"
    return out


def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    length: int = 14,
) -> Optional[pd.Series]:
    """RMA-smoothed Average True Range, matching ``pandas_ta.atr`` (mamode='rma')."""
    if high is None or low is None or close is None or length <= 0:
        return None
    high = pd.Series(high, dtype="float64")
    low = pd.Series(low, dtype="float64")
    close = pd.Series(close, dtype="float64")
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    # pandas_ta's true_range is NaN on the first bar (no previous close).
    true_range.iloc[0] = np.nan
    out = _rma(true_range, length)
    out.name = f"ATRr_{length}"
    return out
