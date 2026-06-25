"""RSI(2) SHORT fade strategy — RESEARCH ONLY.

WARNING: signal convention is inverted: BUY = open a SHORT position,
SELL = cover. Never map this plugin in the production router/whitelist.
Fades short-lived rallies inside a downtrend (Connors RSI(2) mirrored):
short when RSI(2) is extremely overbought while price is below EMA200.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from xauby.strategies.base import Strategy
from xauby.strategies.context import MarketContext
from xauby.strategies.registry import register
from xauby.strategies.signal import Signal, close_short, hold, open_short
from xauby.strategies.rsi2_meanrev.strategy import RSI2MeanReversionStrategy

PARAM_GRID: List[Dict[str, Any]] = [
    {"rsi_sell": r, "sl_atr_mult": s}
    for r in (85.0, 90.0, 95.0)
    for s in (1.2, 1.5)
]
TARGET_REGIMES = {"BEAR_TREND_WEAK"}


@register("rsi2_short")
class RSI2ShortStrategy(Strategy):
    version = "0.1.0"
    author = "xAuby"
    description = "RESEARCH ONLY short: RSI(2) overbought fade below EMA200 (BUY = open short)."
    tags = ["research", "short", "rsi2", "1h"]
    required_timeframes = ["1h"]
    min_bars = 240

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        return {
            "rsi_period": 2,
            "rsi_sell": 90.0,
            "rsi_cover": 35.0,
            "sma_fast_period": 5,
            "ema_period": 200,
            "atr_period": 14,
            "sl_atr_mult": 1.5,
            "trailing_atr_mult": 3.0,
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

    def analyze(self, ctx: MarketContext) -> Signal:
        cfg = {**self.config, **(ctx.config or {})}
        rsi_n = self._cfg_int("rsi_period", 2, cfg)
        rsi_sell_th = self._cfg_float("rsi_sell", 90.0, cfg)
        rsi_cover_th = self._cfg_float("rsi_cover", 35.0, cfg)
        sma_n = self._cfg_int("sma_fast_period", 5, cfg)
        ema_n = self._cfg_int("ema_period", 200, cfg)
        atr_n = self._cfg_int("atr_period", 14, cfg)
        sl_mult = self._cfg_float("sl_atr_mult", 1.5, cfg)
        trail_mult = self._cfg_float("trailing_atr_mult", 3.0, cfg)
        min_required = max(self.min_bars, ema_n + atr_n + 5)
        max_calc_bars = max(min_required, self._cfg_int("max_calc_bars", 420, cfg))

        df_source = ctx.df_primary
        df = df_source.tail(max_calc_bars) if len(df_source) > max_calc_bars else df_source
        if df.empty or len(df) < min_required:
            return hold("Need more RSI2-short candles", strategy_name=self.name, timeframe=ctx.timeframe_primary)

        high_arr = df["high"].to_numpy(dtype="float64", copy=False)
        low_arr = df["low"].to_numpy(dtype="float64", copy=False)
        close_arr = df["close"].to_numpy(dtype="float64", copy=False)

        prev_close = np.roll(close_arr, 1)
        prev_close[0] = close_arr[0]
        tr = np.maximum.reduce(
            [high_arr - low_arr, np.abs(high_arr - prev_close), np.abs(low_arr - prev_close)]
        )
        atr = RSI2MeanReversionStrategy._rolling_mean_np(tr, atr_n)
        ema = RSI2MeanReversionStrategy._ema_np(close_arr, ema_n)
        sma_fast = RSI2MeanReversionStrategy._rolling_mean_np(close_arr, sma_n)
        rsi = RSI2MeanReversionStrategy._rsi_wilder_np(close_arr, rsi_n)

        i = len(close_arr) - 1
        current_close = float(close_arr[i])
        current_atr = float(atr[i]) if np.isfinite(atr[i]) else 0.0
        ema_now = float(ema[i])
        sma_now = float(sma_fast[i]) if np.isfinite(sma_fast[i]) else current_close
        rsi_now = float(rsi[i])

        overbought = rsi_now > rsi_sell_th
        bear_ok = current_close < ema_now
        above_mean = current_close > sma_now

        indicators = {
            "atr": current_atr,
            "ema": ema_now,
            "sma_fast": sma_now,
            "rsi2": rsi_now,
        }
        checklist = [
            {"label": "RSI2", "value": f"{rsi_now:.1f}", "ok": overbought, "hint": f">{rsi_sell_th:.0f}"},
            {"label": "EMA200", "value": f"{ema_now:.2f}", "ok": bear_ok, "hint": "close below"},
            {"label": "SMA5", "value": f"{sma_now:.2f}", "ok": above_mean, "hint": "close above"},
        ]

        if ctx.has_position:
            if ctx.sl_confirmed:
                return close_short("RSI2-short SL confirmed (cover)", confidence=0.9, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            if current_close < sma_now:
                return close_short("Reverted below SMA5 (cover)", confidence=0.75, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            if rsi_now < rsi_cover_th:
                return close_short("RSI2 oversold (cover)", confidence=0.7, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            return hold("RSI2 short held, waiting for reversion", confidence=0.5, volatility=current_atr, trail_distance=current_atr * trail_mult if current_atr > 0 else None, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)

        if overbought and bear_ok and above_mean and current_atr > 0:
            return open_short(
                "RSI2 overbought rally below EMA200 (open short)",
                confidence=0.62,
                stop_loss_distance=current_atr * sl_mult,
                trail_distance=current_atr * trail_mult,
                volatility=current_atr,
                indicators=indicators,
                checklist=checklist,
                status_summary=f"RSI2 SHORT rsi {rsi_now:.1f}",
                strategy_name=self.name,
                timeframe=ctx.timeframe_primary,
            )

        return hold("Waiting for RSI2 overbought fade", confidence=0.35, volatility=current_atr if current_atr > 0 else None, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
