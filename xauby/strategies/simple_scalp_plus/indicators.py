"""Indicator math for the SimpleScalp+ confluence strategy.

Kept private to this plugin (mirrors ``cdc_action_zone/indicators.py``) so the
engine never needs to know about Hull MA, VWAP, Stochastic, ADX, etc. The
formulas are ported from the Binance_Cryptonice ``util/indicators.py`` Simple
Scalp concept, re-implemented with plain pandas/numpy (Wilder smoothing) so the
plugin has no hard dependency on a particular TA-library column layout.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _wilder(values: pd.Series, period: int) -> pd.Series:
    return values.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI: loss==0 -> 100, gain==0 -> 0, both flat -> 50."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = _wilder(gain, period).to_numpy(dtype=float)
    avg_loss = _wilder(loss, period).to_numpy(dtype=float)
    safe_loss = np.where(avg_loss == 0.0, 1.0, avg_loss)
    rs = np.where(
        (avg_gain == 0.0) & (avg_loss == 0.0),
        1.0,
        np.where(avg_loss == 0.0, np.inf, avg_gain / safe_loss),
    )
    out = 100.0 - (100.0 / (1.0 + rs))
    return pd.Series(out, index=close.index).clip(0.0, 100.0)


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return _wilder(_true_range(high, low, close), period)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average Directional Index (Wilder), clipped to [0, 100]."""
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    tr = _wilder(_true_range(high, low, close), period).replace(0.0, np.nan)
    plus_di = 100.0 * _wilder(plus_dm, period) / tr
    minus_di = 100.0 * _wilder(minus_dm, period) / tr
    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    return _wilder(dx, period).fillna(0.0).clip(0.0, 100.0)


def stochastic(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> tuple[pd.Series, pd.Series]:
    """Stochastic oscillator: (%K, %D=SMA3 of %K)."""
    lowest = low.rolling(window=period, min_periods=1).min()
    highest = high.rolling(window=period, min_periods=1).max()
    span = (highest - lowest).replace(0.0, np.nan)
    k = 100.0 * (close - lowest) / span
    d = k.rolling(window=3, min_periods=1).mean()
    return k.clip(0.0, 100.0), d.clip(0.0, 100.0)


def _wma(series: pd.Series, length: int) -> pd.Series:
    """Linear-weighted moving average, vectorised (weights 1..length)."""
    length = max(int(length), 1)
    arr = series.to_numpy(dtype=float)
    n = arr.shape[0]
    out = np.full(n, np.nan)
    if n >= length:
        from numpy.lib.stride_tricks import sliding_window_view

        weights = np.arange(1, length + 1, dtype=float)
        windows = sliding_window_view(arr, length)
        out[length - 1:] = (windows @ weights) / weights.sum()
    return pd.Series(out, index=series.index)


def hull_ma(series: pd.Series, period: int = 16) -> pd.Series:
    """Hull MA — minimal-lag MA: WMA(2*WMA(n/2) - WMA(n), sqrt(n))."""
    half = max(int(period / 2), 1)
    sqrt_period = max(int(np.sqrt(period)), 1)
    raw_hull = 2 * _wma(series, half) - _wma(series, period)
    return _wma(raw_hull, sqrt_period)


def hull_signal(series: pd.Series, period: int = 16) -> pd.Series:
    """+1 = Hull rising vs 2 bars ago, -1 = falling, 0 = flat/warmup."""
    h = hull_ma(series, period)
    prev = h.shift(2)
    return pd.Series(
        np.where(h > prev, 1, np.where(h < prev, -1, 0)),
        index=series.index,
        dtype=int,
    )


def vwap(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    period: int = 20,
) -> pd.Series:
    """Rolling VWAP over ``period`` bars (fresh intraday scalping reference)."""
    typical = (high + low + close) / 3.0
    tp_vol = typical * volume
    window = max(int(period), 1)
    rolling_tp_vol = tp_vol.rolling(window=window, min_periods=1).sum()
    rolling_vol = volume.rolling(window=window, min_periods=1).sum().replace(0.0, np.nan)
    return rolling_tp_vol / rolling_vol


def volume_confirmation(
    volume: pd.Series, period: int = 20, threshold: float = 1.05
) -> pd.Series:
    """True when current volume exceeds ``threshold * SMA(volume, period)``."""
    avg_vol = volume.rolling(window=period).mean()
    return volume > (avg_vol * threshold)
