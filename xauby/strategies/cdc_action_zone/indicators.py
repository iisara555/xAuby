"""Indicator calculations specific to the CDC Action Zone strategy.

Kept private to this plugin so the engine never needs to know about EMAs,
RSI, ATR, or zone classification.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

try:
    import pandas_ta as ta
except ImportError:  # pragma: no cover - exercised in envs without pandas_ta
    # Same math, pure pandas — parity is asserted by tests/test_ta_fallback.py
    # wherever the real dependency is installed.
    from xauby.utils import ta_fallback as ta

logger = logging.getLogger("xauby.strategies.cdc")


def classify_zone(price: float, fast: float, slow: float) -> str:
    """Classify the CDC Action Zone colour from price vs. EMA fast/slow."""
    bull = fast > slow
    bear = fast < slow

    if bull and price > fast:
        return "GREEN"
    if bear and price > fast and price > slow:
        return "BLUE"
    if bear and price > fast and price < slow:
        return "LBLUE"
    if bear and price < fast:
        return "RED"
    if bull and price < fast and price < slow:
        return "ORANGE"
    if bull and price < fast and price > slow:
        return "YELLOW"

    if bull:
        return "GREEN" if price >= fast else "ORANGE"
    return "RED" if price <= fast else "BLUE"


def classify_zone_vectorized(
    price: "np.ndarray", fast: "np.ndarray", slow: "np.ndarray"
) -> "np.ndarray":
    """Array form of :func:`classify_zone` for whole-history chart rendering.

    Priority order mirrors the if/elif chain above exactly (first matching
    condition wins); the tie cases (price == fast/slow, or fast == slow) fall
    through to the same fallback formula as the scalar version.
    """
    price = np.asarray(price, dtype="float64")
    fast = np.asarray(fast, dtype="float64")
    slow = np.asarray(slow, dtype="float64")
    bull = fast > slow
    bear = fast < slow

    conditions = [
        bull & (price > fast),
        bear & (price > fast) & (price > slow),
        bear & (price > fast) & (price < slow),
        bear & (price < fast),
        bull & (price < fast) & (price < slow),
        bull & (price < fast) & (price > slow),
    ]
    choices = ["GREEN", "BLUE", "LBLUE", "RED", "ORANGE", "YELLOW"]
    fallback = np.where(
        bull,
        np.where(price >= fast, "GREEN", "ORANGE"),
        np.where(price <= fast, "RED", "BLUE"),
    )
    return np.select(conditions, choices, default=fallback)


def _action_price(close: pd.Series, ap_smoothing: int) -> pd.Series:
    """CDC Action Zone V3 source: EMA-smoothed price (``AP = ema(close, n)``).

    ``ap_smoothing <= 1`` disables smoothing and uses raw close (legacy
    behaviour); ``ap_smoothing = 2`` matches the original Piriya V3 indicator.
    """
    if ap_smoothing and ap_smoothing >= 2:
        ap = ta.ema(close, length=ap_smoothing)
        if ap is not None and len(ap) > 0:
            return ap  # keep NaN in warm-up period; do not substitute close prices
    return close


def compute_volume_ratio_4h(
    volumes: pd.Series,
    sma_window: int = 20,
    last_bar_is_forming: bool = True,
) -> float:
    """Last completed 4H volume vs SMA20 of completed candles only.

    When ``last_bar_is_forming`` the final row is the still-forming candle and
    is excluded from both the ratio numerator and the SMA window. Pass False
    when the engine already trimmed the forming bar (strategy.use_closed_candles)
    so a real closed bar is not dropped twice.
    """
    if volumes is None or len(volumes) < sma_window + 1:
        return 0.0
    completed = (volumes.iloc[:-1] if last_bar_is_forming else volumes).astype(float)
    if len(completed) < sma_window:
        return 0.0
    vol_sma = completed.rolling(window=sma_window).mean()
    last_completed_vol = float(completed.iloc[-1])
    last_completed_sma = float(vol_sma.iloc[-1])
    if last_completed_sma <= 0 or pd.isna(last_completed_sma):
        return 0.0
    return round(last_completed_vol / last_completed_sma, 4)


def compute_indicators(
    df_primary: pd.DataFrame,
    df_regime: Optional[pd.DataFrame],
    ap_smoothing: int = 2,
    use_d1_regime: bool = False,
    last_bar_is_forming: bool = True,
    slope_bars: int = 3,
) -> Dict[str, Any]:
    """Compute every indicator the CDC strategy needs in one pass.

    ``last_bar_is_forming`` mirrors ctx.extras: False means the engine already
    trimmed the forming bar, so the final row is a closed candle.
    """
    res: Dict[str, Any] = {
        "cdc_zone_d1": "OFF",
        "cdc_zone_4h": "UNKNOWN",
        "cdc_zone_4h_prev": "UNKNOWN",
        "cdc_zone_4h_green_streak": 0,
        "cdc_zone_4h_red_streak": 0,
        "ema_fast_4h": 0.0,
        "ema_slow_4h": 0.0,
        "ema_fast_4h_prev": 0.0,
        "ema_slow_4h_prev": 0.0,
        "ema_fast_d1": 0.0,
        "ema_slow_d1": 0.0,
        "ema_fast_d1_prev": 0.0,
        "ema_slow_d1_prev": 0.0,
        "ap_smoothing": int(ap_smoothing),
        # Slow-EMA slope over ``slope_bars`` bars; None = not enough data, so
        # the strategy's slope filter must treat None as "no opinion" (pass).
        "ema_slow_4h_slope": None,
        # Close position of the last CLOSED bar within its high-low range
        # (0 = closed at the low, 1 = at the high); None when range is zero.
        "bar_close_pos_4h": None,
        "rsi_4h": 0.0,
        "volume_ratio_4h": 0.0,
        "atr_4h": 0.0,
        "close_4h": 0.0,
        "close_d1": 0.0,
    }

    if use_d1_regime:
        if df_regime is not None and not df_regime.empty and len(df_regime) >= 26:
            try:
                close_d1 = df_regime["close"].astype(float)
                ap_d1 = _action_price(close_d1, ap_smoothing)
                ema_fast_d1 = ta.ema(ap_d1, length=12)
                ema_slow_d1 = ta.ema(ap_d1, length=26)
                # D1 shares the same forming-bar contract as the primary
                # timeframe (engine trims both under strategy.use_closed_candles);
                # index off the same closed-bar rule instead of assuming -1.
                closed_idx_d1 = -2 if last_bar_is_forming and len(close_d1) >= 2 else -1
                if ema_fast_d1 is not None and ema_slow_d1 is not None and len(ema_fast_d1) > 0:
                    p = float(ap_d1.iloc[closed_idx_d1])
                    f = float(ema_fast_d1.iloc[closed_idx_d1])
                    s = float(ema_slow_d1.iloc[closed_idx_d1])
                    res["cdc_zone_d1"] = classify_zone(p, f, s)
                    res["close_d1"] = float(close_d1.iloc[closed_idx_d1])
                    res["ema_fast_d1"] = f
                    res["ema_slow_d1"] = s
                    if len(ap_d1) + closed_idx_d1 >= 1:
                        prev_idx_d1 = closed_idx_d1 - 1
                        res["ema_fast_d1_prev"] = float(ema_fast_d1.iloc[prev_idx_d1])
                        res["ema_slow_d1_prev"] = float(ema_slow_d1.iloc[prev_idx_d1])
                else:
                    res["cdc_zone_d1"] = "N/A"
            except Exception as e:
                logger.error(f"Error calculating D1 indicators: {e}")
                res["cdc_zone_d1"] = "N/A"
        else:
            res["cdc_zone_d1"] = "N/A"

    if df_primary is not None and not df_primary.empty and len(df_primary) >= 100:
        try:
            close_4h = df_primary["close"].astype(float)
            high_4h = df_primary["high"].astype(float)
            low_4h = df_primary["low"].astype(float)
            volume_4h = df_primary["volume"].astype(float)

            # Closed-bar index: every field below reads this bar, never the
            # still-forming one, so zone/EMA/RSI/ATR stay in lockstep with
            # bar_close_pos/volume/recent_closes instead of silently mixing
            # a forming-bar price into a closed-bar signal.
            closed_idx = -2 if last_bar_is_forming and len(close_4h) >= 2 else -1

            ap_4h = _action_price(close_4h, ap_smoothing)
            ema_fast_4h = ta.ema(ap_4h, length=12)
            ema_slow_4h = ta.ema(ap_4h, length=26)
            if ema_fast_4h is not None and ema_slow_4h is not None and len(ema_fast_4h) > 0:
                p = float(ap_4h.iloc[closed_idx])
                f = float(ema_fast_4h.iloc[closed_idx])
                s = float(ema_slow_4h.iloc[closed_idx])
                res["cdc_zone_4h"] = classify_zone(p, f, s)
                res["close_4h"] = float(close_4h.iloc[closed_idx])
                res["ema_fast_4h"] = f
                res["ema_slow_4h"] = s
                if len(ap_4h) + closed_idx >= 1:
                    prev_idx = closed_idx - 1
                    p_prev = float(ap_4h.iloc[prev_idx])
                    f_prev = float(ema_fast_4h.iloc[prev_idx])
                    s_prev = float(ema_slow_4h.iloc[prev_idx])
                    res["cdc_zone_4h_prev"] = classify_zone(p_prev, f_prev, s_prev)
                    res["ema_fast_4h_prev"] = f_prev
                    res["ema_slow_4h_prev"] = s_prev

                n = max(1, int(slope_bars))
                slope_idx = closed_idx - n
                if len(ema_slow_4h) + slope_idx >= 0 and not pd.isna(ema_slow_4h.iloc[slope_idx]):
                    res["ema_slow_4h_slope"] = float(
                        ema_slow_4h.iloc[closed_idx] - ema_slow_4h.iloc[slope_idx]
                    )

                # Count consecutive GREEN bars ending at the closed bar so the
                # strategy can treat a transition as "fresh" within a window of
                # bars (volume often confirms a bar or two after the crossover).
                if res["cdc_zone_4h"] in ("GREEN", "RED"):
                    target_zone = res["cdc_zone_4h"]
                    streak = 0
                    usable_len = len(ema_fast_4h) + closed_idx + 1
                    lookback = min(usable_len, 50)
                    for i in range(1, lookback + 1):
                        idx = closed_idx - (i - 1)
                        zone_i = classify_zone(
                            float(ap_4h.iloc[idx]),
                            float(ema_fast_4h.iloc[idx]),
                            float(ema_slow_4h.iloc[idx]),
                        )
                        if zone_i == target_zone:
                            streak += 1
                        else:
                            break
                    # Fresh-transition streaks are symmetric: GREEN drives long
                    # entries, RED drives short entries (CDC stop-and-reverse).
                    if target_zone == "GREEN":
                        res["cdc_zone_4h_green_streak"] = streak
                    else:
                        res["cdc_zone_4h_red_streak"] = streak

            bar_high = float(high_4h.iloc[closed_idx])
            bar_low = float(low_4h.iloc[closed_idx])
            bar_range = bar_high - bar_low
            if bar_range > 0:
                res["bar_close_pos_4h"] = round(
                    (float(close_4h.iloc[closed_idx]) - bar_low) / bar_range, 4
                )

            rsi_series = ta.rsi(close_4h, length=14)
            if rsi_series is not None and len(rsi_series) > 0:
                res["rsi_4h"] = float(rsi_series.iloc[closed_idx])

            res["volume_ratio_4h"] = compute_volume_ratio_4h(
                volume_4h, last_bar_is_forming=last_bar_is_forming
            )

            atr_series = ta.atr(high_4h, low_4h, close_4h, length=14)
            if atr_series is not None and len(atr_series) > 0:
                res["atr_4h"] = float(atr_series.iloc[closed_idx])

            # Last 8 completed 4H closes for TUI sparkline (exclude forming bar).
            if len(close_4h) >= 3:
                completed = close_4h.iloc[:-1] if last_bar_is_forming else close_4h
                res["recent_closes_4h"] = [
                    float(v) for v in completed.tail(8).tolist()
                ]
        except Exception as e:
            logger.error(f"Error calculating 4H indicators: {e}")

    return res
