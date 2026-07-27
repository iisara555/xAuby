"""SuperTrend SHORT strategy — RESEARCH ONLY.

WARNING: signal convention is inverted: BUY = open a SHORT position,
SELL = cover. Never map this plugin in the production router/whitelist —
the spot engine would execute a real BUY. Used by
scripts/regime_strategy_eval.py short-edge research only.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np

from xauby.strategies.base import Strategy
from xauby.strategies.context import MarketContext
from xauby.strategies.registry import register
from xauby.strategies.signal import Signal, close_short, hold, open_short
from xauby.strategies.supertrend_ema200.strategy import SuperTrendEMA200Strategy

PARAM_GRID: List[Dict[str, Any]] = [
    {"supertrend_mult": m, "sl_atr_mult": s}
    for m in (3.0, 3.5)
    for s in (2.0, 2.5)
]
TARGET_REGIMES = {"BEAR_BREAKDOWN", "BEAR_TREND_STRONG", "PANIC_SELL"}


@register("supertrend_short")
class SuperTrendShortStrategy(Strategy):
    version = "0.1.0"
    author = "xAuby"
    description = "RESEARCH ONLY short: SuperTrend bear below EMA200 (BUY = open short)."
    tags = ["research", "short", "supertrend", "1h"]
    # Research short mirror: BUY opens a SHORT.
    maturity = "research"
    required_timeframes = ["1h"]
    min_bars = 240

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        return {
            "ema_period": 200,
            "atr_period": 10,
            "supertrend_mult": 3.5,
            "entry_on_flip_only": True,
            "sl_atr_mult": 2.5,
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

    def _cfg_bool(self, key: str, default: bool, cfg: Optional[Dict[str, Any]] = None) -> bool:
        src = cfg if cfg is not None else self.config
        value = src.get(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def validate_config(self) -> List[str]:
        return [
            "RESEARCH-ONLY SHORT plugin: BUY opens a SHORT. "
            "Never map this strategy in the production router/whitelist."
        ]

    def analyze(self, ctx: MarketContext) -> Signal:
        cfg = {**self.config, **(ctx.config or {})}
        ema_n = self._cfg_int("ema_period", 200, cfg)
        atr_n = self._cfg_int("atr_period", 10, cfg)
        st_mult = self._cfg_float("supertrend_mult", 3.5, cfg)
        entry_on_flip_only = self._cfg_bool("entry_on_flip_only", True, cfg)
        sl_mult = self._cfg_float("sl_atr_mult", 2.5, cfg)
        trail_mult = self._cfg_float("trailing_atr_mult", 2.0, cfg)
        min_required = max(self.min_bars, ema_n + atr_n + 5)
        max_calc_bars = max(min_required, self._cfg_int("max_calc_bars", 420, cfg))

        df_source = ctx.df_primary
        df = df_source.tail(max_calc_bars) if len(df_source) > max_calc_bars else df_source
        if df.empty or len(df) < min_required:
            return hold("Need more ST-short candles", strategy_name=self.name, timeframe=ctx.timeframe_primary)

        high_arr = df["high"].to_numpy(dtype="float64", copy=False)
        low_arr = df["low"].to_numpy(dtype="float64", copy=False)
        close_arr = df["close"].to_numpy(dtype="float64", copy=False)

        prev_close = np.roll(close_arr, 1)
        prev_close[0] = close_arr[0]
        tr = np.maximum.reduce(
            [high_arr - low_arr, np.abs(high_arr - prev_close), np.abs(low_arr - prev_close)]
        )
        atr = SuperTrendEMA200Strategy._rolling_mean_np(tr, atr_n)
        ema = SuperTrendEMA200Strategy._ema_np(close_arr, ema_n)
        st_bull, _st_line = SuperTrendEMA200Strategy._supertrend_np(
            high_arr, low_arr, close_arr, atr, st_mult
        )

        i = len(close_arr) - 1
        current_close = float(close_arr[i])
        current_atr = float(atr[i]) if np.isfinite(atr[i]) else 0.0
        ema_now = float(ema[i])
        st_bear_now = not bool(st_bull[i])
        st_bear_prev = not bool(st_bull[i - 1]) if i > 0 else False
        flip_bear = st_bear_now and not st_bear_prev
        bear_ok = current_close < ema_now
        entry_timing_ok = flip_bear if entry_on_flip_only else st_bear_now

        indicators = {
            "atr": current_atr,
            "ema": ema_now,
            "supertrend_bear": st_bear_now,
            "supertrend_flip_bear": flip_bear,
        }
        checklist = [
            {"label": "EMA200", "value": f"{current_close:.2f}<{ema_now:.2f}", "ok": bear_ok},
            {"label": "SuperTrend", "value": "Bear" if st_bear_now else "Bull", "ok": entry_timing_ok},
        ]

        if ctx.has_position:
            if ctx.sl_confirmed:
                return close_short("ST-short SL confirmed (cover)", confidence=0.9, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            if not st_bear_now:
                return close_short("SuperTrend flipped bullish (cover)", confidence=0.72, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            return hold("ST short riding", confidence=0.5, volatility=current_atr, trail_distance=current_atr * trail_mult if current_atr > 0 else None, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)

        if bear_ok and entry_timing_ok and current_atr > 0:
            return open_short(
                "SuperTrend bear below EMA200 (open short)",
                confidence=0.64,
                stop_loss_distance=current_atr * sl_mult,
                trail_distance=current_atr * trail_mult,
                volatility=current_atr,
                indicators=indicators,
                checklist=checklist,
                status_summary=f"ST SHORT ATR {current_atr:.2f}",
                strategy_name=self.name,
                timeframe=ctx.timeframe_primary,
            )

        return hold("Waiting for SuperTrend bear below EMA200", confidence=0.35, volatility=current_atr if current_atr > 0 else None, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
