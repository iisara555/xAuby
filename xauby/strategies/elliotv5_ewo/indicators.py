"""ElliotV5 / SMAOffset (EWO) indicator math.

Port of the freqtrade community strategy family variously published as
``ElliotV5`` / ``SMAOffset`` / ``SMAOffsetProtectOptV1``. The signal is a
mean-reversion entry against a moving average, gated by the Elliott Wave
Oscillator:

    EWO     = (EMA(fast_ewo) - EMA(slow_ewo)) / close * 100
    ma_buy  = SMA(base_nb_candles_buy)
    ma_sell = SMA(base_nb_candles_sell)

Two entry branches, exactly as in the original:
  * a *dip in an uptrend* — price below the MA offset while EWO is strongly
    positive and RSI has not yet run hot; and
  * a *capitulation* — price below the MA offset while EWO is deeply negative.

Exit is price back above ``ma_sell * high_offset``. Everything else the original
strategy relies on (the ROI ladder, the stop, trailing) is engine-managed here
via strategy config, so it is not reimplemented.

All values are causal: bar ``i`` uses only bars ``<= i``.

Reuses the Wilder RSI and EMA already in the repo rather than adding a second
implementation.
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from xauby.strategies.simple_scalp_plus.indicators import atr, ema, rsi


def elliott_wave_oscillator(close: pd.Series, fast: int, slow: int) -> pd.Series:
    """EWO as freqtrade defines it: EMA spread as a percentage of price."""
    fast_ema = ema(close, fast)
    slow_ema = ema(close, slow)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = (fast_ema - slow_ema) / close.replace(0.0, np.nan) * 100.0
    return out


def compute_elliotv5_frame(
    df: pd.DataFrame, config: Dict[str, Any] | None = None
) -> pd.DataFrame:
    """Append ElliotV5/EWO columns to ``df`` (copy)."""
    cfg = dict(config or {})
    nb_buy = max(1, int(cfg.get("base_nb_candles_buy", 14)))
    nb_sell = max(1, int(cfg.get("base_nb_candles_sell", 24)))
    low_offset = float(cfg.get("low_offset", 0.975))
    high_offset = float(cfg.get("high_offset", 1.010))
    fast_ewo = max(1, int(cfg.get("fast_ewo", 50)))
    slow_ewo = max(2, int(cfg.get("slow_ewo", 200)))
    rsi_period = max(2, int(cfg.get("rsi_period", 14)))
    atr_period = max(2, int(cfg.get("atr_period", 14)))

    out = df.copy()
    if out.empty:
        for col in ("ewo", "ma_buy", "ma_sell", "buy_line", "sell_line",
                    "rsi", "atr"):
            out[col] = np.nan
        out["ewo_zone"] = "NONE"
        return out

    close = pd.to_numeric(out["close"], errors="coerce")
    high = pd.to_numeric(out["high"], errors="coerce")
    low = pd.to_numeric(out["low"], errors="coerce")

    ma_buy = close.rolling(nb_buy).mean()
    ma_sell = close.rolling(nb_sell).mean()

    out["ewo"] = elliott_wave_oscillator(close, fast_ewo, slow_ewo)
    out["ma_buy"] = ma_buy
    out["ma_sell"] = ma_sell
    out["buy_line"] = ma_buy * low_offset
    out["sell_line"] = ma_sell * high_offset
    out["rsi"] = rsi(close, rsi_period)
    out["atr"] = atr(high, low, close, atr_period)

    ewo_hi = float(cfg.get("ewo_high", 2.327))
    ewo_lo = float(cfg.get("ewo_low", -19.988))
    ewo_np = out["ewo"].to_numpy("float64")
    out["ewo_zone"] = np.where(
        ewo_np > ewo_hi, "IMPULSE",
        np.where(ewo_np < ewo_lo, "CAPITULATION", "NEUTRAL"),
    )
    return out


def elliotv5_snapshot(
    df: pd.DataFrame, config: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    """Last-bar scalar snapshot for the strategy/TUI."""
    cfg = dict(config or {})
    max_calc = int(cfg.get("max_calc_bars", 0) or 0)
    frame_src = df.tail(max_calc) if max_calc > 0 and len(df) > max_calc else df
    frame = compute_elliotv5_frame(frame_src, cfg)
    if frame.empty:
        return {"ewo": 0.0, "buy_line": 0.0, "sell_line": 0.0, "rsi": 50.0,
                "atr": 0.0, "close": 0.0, "zone": "NONE"}
    row = frame.iloc[-1]

    def _f(key: str, default: float = 0.0) -> float:
        v = row.get(key)
        return default if v is None or pd.isna(v) else float(v)

    return {
        "ewo": _f("ewo"),
        "ma_buy": _f("ma_buy"),
        "ma_sell": _f("ma_sell"),
        "buy_line": _f("buy_line"),
        "sell_line": _f("sell_line"),
        "rsi": _f("rsi", 50.0),
        "atr": _f("atr"),
        "close": _f("close"),
        "zone": str(row.get("ewo_zone", "NONE")),
    }
