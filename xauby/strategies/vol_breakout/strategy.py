"""Volatility-expansion breakout strategy plugin.

Long-only spot: enter when price clears the recent range high while ATR is
expanding above its own average (volatility breakout) with volume
confirmation; exit on loss of EMA20. Targets VOLATILITY_EXPANSION via the
RegimeRouter.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from xauby.strategies.base import Strategy
from xauby.strategies.context import MarketContext
from xauby.strategies.registry import register
from xauby.strategies.signal import Signal, buy, hold, sell

# Small tuning grid evaluated on the train split only (regime_strategy_eval.py).
PARAM_GRID: List[Dict[str, Any]] = [
    {"lookback": lb, "atr_expansion_mult": x, "vol_min_ratio": v}
    for lb in (12, 20, 30)
    for x in (1.1, 1.3)
    for v in (1.0, 1.5)
]
TARGET_REGIMES = {"VOLATILITY_EXPANSION"}


@register("vol_breakout")
class VolatilityBreakoutStrategy(Strategy):
    version = "0.1.0"
    author = "xAuby"
    description = "ATR-expansion range breakout with volume confirmation."
    tags = ["btc", "breakout", "volatility", "1h"]
    required_timeframes = ["1h"]
    min_bars = 240

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        return {
            "lookback": 20,
            "atr_period": 14,
            "atr_ma_period": 50,
            "atr_expansion_mult": 1.2,
            "vol_ma_period": 20,
            "vol_min_ratio": 1.2,
            "exit_ema_period": 20,
            "sl_atr_mult": 2.0,
            "trailing_atr_mult": 2.0,
            "breakeven_sl_enabled": True,
            "breakeven_activation_atr_mult": 1.2,
            "breakeven_buffer_atr_mult": 0.05,
            "max_calc_bars": 420,
        }

    def _cfg_int(self, key: str, default: int, cfg: Optional[Dict[str, Any]] = None) -> int:
        try:
            src = cfg if cfg is not None else self.config
            return max(1, int(src.get(key, default)))
        except (TypeError, ValueError):
            return default

    def _cfg_float(self, key: str, default: float, cfg: Optional[Dict[str, Any]] = None) -> float:
        try:
            src = cfg if cfg is not None else self.config
            return float(src.get(key, default))
        except (TypeError, ValueError):
            return default

    def validate_config(self) -> List[str]:
        warnings: List[str] = []
        if self._cfg_float("atr_expansion_mult", 1.2) < 1.0:
            warnings.append("atr_expansion_mult < 1.0 disables the expansion gate")
        return warnings

    @staticmethod
    def _ema_np(values: np.ndarray, period: int) -> np.ndarray:
        ema = np.empty(len(values), dtype="float64")
        alpha = 2.0 / (period + 1.0)
        ema[0] = values[0]
        for i in range(1, len(values)):
            ema[i] = (values[i] * alpha) + (ema[i - 1] * (1.0 - alpha))
        return ema

    @staticmethod
    def _rolling_mean_np(values: np.ndarray, period: int) -> np.ndarray:
        out = np.full(len(values), np.nan, dtype="float64")
        if len(values) < period:
            return out
        csum = np.cumsum(np.insert(values, 0, 0.0))
        out[period - 1:] = (csum[period:] - csum[:-period]) / period
        return out

    def analyze(self, ctx: MarketContext) -> Signal:
        cfg = {**self.config, **(ctx.config or {})}
        lookback = self._cfg_int("lookback", 20, cfg)
        atr_n = self._cfg_int("atr_period", 14, cfg)
        atr_ma_n = self._cfg_int("atr_ma_period", 50, cfg)
        expansion_mult = self._cfg_float("atr_expansion_mult", 1.2, cfg)
        vol_n = self._cfg_int("vol_ma_period", 20, cfg)
        vol_min_ratio = self._cfg_float("vol_min_ratio", 1.2, cfg)
        exit_ema_n = self._cfg_int("exit_ema_period", 20, cfg)
        sl_mult = self._cfg_float("sl_atr_mult", 2.0, cfg)
        trail_mult = self._cfg_float("trailing_atr_mult", 2.0, cfg)
        min_required = max(self.min_bars, atr_n + atr_ma_n + 5, lookback + 5)
        max_calc_bars = max(min_required, self._cfg_int("max_calc_bars", 420, cfg))

        df_source = ctx.df_primary
        df = df_source.tail(max_calc_bars) if len(df_source) > max_calc_bars else df_source
        if df.empty or len(df) < min_required:
            return hold("Need more breakout candles", strategy_name=self.name, timeframe=ctx.timeframe_primary)

        high_arr = df["high"].to_numpy(dtype="float64", copy=False)
        low_arr = df["low"].to_numpy(dtype="float64", copy=False)
        close_arr = df["close"].to_numpy(dtype="float64", copy=False)
        volume_arr = df["volume"].to_numpy(dtype="float64", copy=False)

        prev_close = np.roll(close_arr, 1)
        prev_close[0] = close_arr[0]
        tr = np.maximum.reduce(
            [high_arr - low_arr, np.abs(high_arr - prev_close), np.abs(low_arr - prev_close)]
        )
        atr = self._rolling_mean_np(tr, atr_n)
        atr_ma = self._rolling_mean_np(np.nan_to_num(atr, nan=0.0), atr_ma_n)
        exit_ema = self._ema_np(close_arr, exit_ema_n)
        vol_ma = self._rolling_mean_np(volume_arr, vol_n)

        i = len(close_arr) - 1
        current_close = float(close_arr[i])
        current_atr = float(atr[i]) if np.isfinite(atr[i]) else 0.0
        atr_avg = float(atr_ma[i]) if np.isfinite(atr_ma[i]) else 0.0
        exit_ema_now = float(exit_ema[i])

        # Range high excludes the current bar.
        range_high = float(high_arr[i - lookback : i].max())
        breakout = current_close > range_high
        expansion = atr_avg > 0 and current_atr >= atr_avg * expansion_mult
        vol_avg = float(vol_ma[i]) if np.isfinite(vol_ma[i]) else 0.0
        vol_ratio = float(volume_arr[i]) / vol_avg if vol_avg > 0 else 0.0
        vol_ok = vol_min_ratio <= 0.0 or vol_ratio >= vol_min_ratio

        indicators = {
            "atr": current_atr,
            "atr_avg": atr_avg,
            "range_high": range_high,
            "exit_ema": exit_ema_now,
            "vol_ratio": vol_ratio,
        }
        checklist = [
            {"label": "Breakout", "value": f"{current_close:.2f}>{range_high:.2f}", "ok": breakout},
            {"label": "ATR expand", "value": f"{current_atr:.2f}/{atr_avg:.2f}", "ok": expansion, "hint": f">={expansion_mult:.2f}x"},
            {"label": "Volume", "value": f"{vol_ratio:.2f}x", "ok": vol_ok, "hint": f">={vol_min_ratio:.2f}x"},
        ]

        if ctx.has_position:
            if ctx.sl_confirmed:
                return sell("Vol breakout SL confirmed", confidence=0.9, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            if current_close < exit_ema_now:
                return sell("Close lost exit EMA", confidence=0.72, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            return hold("Vol breakout position riding", confidence=0.5, volatility=current_atr, trail_distance=current_atr * trail_mult if current_atr > 0 else None, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)

        if breakout and expansion and vol_ok and current_atr > 0:
            return buy(
                "Range breakout with ATR expansion",
                confidence=0.64,
                stop_loss_distance=current_atr * sl_mult,
                trail_distance=current_atr * trail_mult,
                volatility=current_atr,
                indicators=indicators,
                checklist=checklist,
                status_summary=f"VolBO BUY ATR {current_atr:.2f}",
                strategy_name=self.name,
                timeframe=ctx.timeframe_primary,
            )

        return hold("Waiting for volatility breakout", confidence=0.35, volatility=current_atr if current_atr > 0 else None, indicators=indicators, checklist=checklist, status_summary="Waiting VolBO", strategy_name=self.name, timeframe=ctx.timeframe_primary)
