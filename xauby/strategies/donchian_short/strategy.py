"""Donchian SHORT breakdown strategy — RESEARCH ONLY.

WARNING: signal convention is inverted: BUY = open a SHORT position,
SELL = cover. The production spot engine treats BUY as a real spot buy,
so this plugin must NEVER be mapped in the regime router or whitelist.
It exists for scripts/regime_strategy_eval.py short-edge research
(ShortPositionSimulator, Binance futures fee model).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from xauby.strategies.base import Strategy
from xauby.strategies.context import MarketContext
from xauby.strategies.registry import register
from xauby.strategies.signal import Signal, close_short, hold, open_short

PARAM_GRID: List[Dict[str, Any]] = [
    {"entry_len": e, "sl_atr_mult": s}
    for e in (24, 48, 72)
    for s in (2.0, 2.5)
]
TARGET_REGIMES = {"BEAR_BREAKDOWN", "BEAR_TREND_STRONG", "PANIC_SELL"}


@register("donchian_short")
class DonchianShortStrategy(Strategy):
    version = "0.1.0"
    author = "xAuby"
    description = "RESEARCH ONLY short: Donchian breakdown below EMA200 (BUY = open short)."
    tags = ["research", "short", "donchian", "1h"]
    # Research short mirror: BUY opens a SHORT.
    maturity = "research"
    required_timeframes = ["1h"]
    min_bars = 240

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        return {
            "entry_len": 48,
            "exit_len": 24,
            "ema_period": 200,
            "atr_period": 14,
            "sl_atr_mult": 2.0,
            "trailing_atr_mult": 2.5,
            "breakeven_sl_enabled": False,
            "breakeven_activation_atr_mult": 1.5,
            "breakeven_buffer_atr_mult": 0.1,
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
        return [
            "RESEARCH-ONLY SHORT plugin: BUY opens a SHORT. "
            "Never map this strategy in the production router/whitelist."
        ]

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
        entry_len = self._cfg_int("entry_len", 48, cfg)
        exit_len = self._cfg_int("exit_len", 24, cfg)
        ema_n = self._cfg_int("ema_period", 200, cfg)
        atr_n = self._cfg_int("atr_period", 14, cfg)
        sl_mult = self._cfg_float("sl_atr_mult", 2.0, cfg)
        trail_mult = self._cfg_float("trailing_atr_mult", 2.5, cfg)
        min_required = max(self.min_bars, ema_n + atr_n + 5, entry_len + 5)
        max_calc_bars = max(min_required, self._cfg_int("max_calc_bars", 420, cfg))

        df_source = ctx.df_primary
        df = df_source.tail(max_calc_bars) if len(df_source) > max_calc_bars else df_source
        if df.empty or len(df) < min_required:
            return hold("Need more Donchian-short candles", strategy_name=self.name, timeframe=ctx.timeframe_primary)

        high_arr = df["high"].to_numpy(dtype="float64", copy=False)
        low_arr = df["low"].to_numpy(dtype="float64", copy=False)
        close_arr = df["close"].to_numpy(dtype="float64", copy=False)

        prev_close = np.roll(close_arr, 1)
        prev_close[0] = close_arr[0]
        tr = np.maximum.reduce(
            [high_arr - low_arr, np.abs(high_arr - prev_close), np.abs(low_arr - prev_close)]
        )
        atr = self._rolling_mean_np(tr, atr_n)
        ema = self._ema_np(close_arr, ema_n)

        i = len(close_arr) - 1
        current_close = float(close_arr[i])
        current_atr = float(atr[i]) if np.isfinite(atr[i]) else 0.0
        ema_now = float(ema[i])

        entry_low = float(low_arr[i - entry_len : i].min())
        exit_low = float(low_arr[i - exit_len : i].min())
        exit_high = float(high_arr[i - exit_len : i].max())
        exit_mid = (exit_high + exit_low) / 2.0

        breakdown = current_close < entry_low
        bear_ok = current_close < ema_now

        indicators = {
            "atr": current_atr,
            "ema": ema_now,
            "donchian_entry_low": entry_low,
            "donchian_exit_mid": exit_mid,
        }
        checklist = [
            {"label": "Breakdown", "value": f"{current_close:.2f}<{entry_low:.2f}", "ok": breakdown},
            {"label": "EMA200", "value": f"{ema_now:.2f}", "ok": bear_ok, "hint": "close below"},
        ]

        if ctx.has_position:
            if ctx.sl_confirmed:
                return close_short("Donchian-short SL confirmed (cover)", confidence=0.9, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            if current_close > exit_mid:
                return close_short("Reclaimed Donchian exit midline (cover)", confidence=0.72, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            if current_close > ema_now:
                return close_short("Reclaimed EMA200 (cover)", confidence=0.68, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            return hold("Donchian short riding", confidence=0.5, volatility=current_atr, trail_distance=current_atr * trail_mult if current_atr > 0 else None, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)

        if breakdown and bear_ok and current_atr > 0:
            return open_short(
                "Donchian breakdown below EMA200 (open short)",
                confidence=0.66,
                stop_loss_distance=current_atr * sl_mult,
                trail_distance=current_atr * trail_mult,
                volatility=current_atr,
                indicators=indicators,
                checklist=checklist,
                status_summary=f"Donchian SHORT ATR {current_atr:.2f}",
                strategy_name=self.name,
                timeframe=ctx.timeframe_primary,
            )

        return hold("Waiting for Donchian breakdown", confidence=0.35, volatility=current_atr if current_atr > 0 else None, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
