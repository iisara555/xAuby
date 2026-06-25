"""RSI(2) mean-reversion strategy plugin (Connors-style dip buy).

Long-only spot: buy short-term oversold dips (RSI(2) deeply low, price below
SMA5) only while the larger trend is up (close > EMA200), exit on reversion
to the short mean (close > SMA5) or RSI(2) overbought. Tight ATR stop.
Targets LOW_VOL_* and SIDEWAYS_CHOP regimes via the RegimeRouter.
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
    {"rsi_buy": r, "sl_atr_mult": s}
    for r in (5.0, 10.0, 15.0)
    for s in (1.2, 1.5, 2.0)
]
TARGET_REGIMES = {"LOW_VOL_ACCUMULATION", "LOW_VOL_RANGE", "SIDEWAYS_CHOP", "BEAR_TREND_WEAK"}


@register("rsi2_meanrev")
class RSI2MeanReversionStrategy(Strategy):
    version = "0.1.0"
    author = "xAuby"
    description = "Connors RSI(2) dip-buy above EMA200 with SMA5 reversion exit."
    tags = ["btc", "rsi2", "mean-reversion", "1h"]
    required_timeframes = ["1h"]
    min_bars = 240

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        return {
            "rsi_period": 2,
            "rsi_buy": 10.0,
            "rsi_exit": 65.0,
            "sma_fast_period": 5,
            "ema_period": 200,
            "atr_period": 14,
            "sl_atr_mult": 1.5,
            "fixed_tp_pct": 0.8,
            # Loose trail: the reversion exit should fire long before the
            # engine trailing stop binds.
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
        warnings: List[str] = []
        if self._cfg_float("rsi_buy", 10.0) >= self._cfg_float("rsi_exit", 65.0):
            warnings.append("rsi_buy should be well below rsi_exit")
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

    @staticmethod
    def _rsi_wilder_np(close: np.ndarray, period: int) -> np.ndarray:
        """Wilder RSI (ewm alpha=1/period) — matches TradingView/Connors usage."""
        n = len(close)
        out = np.full(n, 50.0, dtype="float64")
        if n <= period:
            return out
        delta = np.diff(close)
        gain = np.maximum(delta, 0.0)
        loss = np.maximum(-delta, 0.0)
        avg_gain = np.empty(len(delta), dtype="float64")
        avg_loss = np.empty(len(delta), dtype="float64")
        avg_gain[period - 1] = gain[:period].mean()
        avg_loss[period - 1] = loss[:period].mean()
        avg_gain[: period - 1] = np.nan
        avg_loss[: period - 1] = np.nan
        for i in range(period, len(delta)):
            avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i]) / period
            avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i]) / period
        for i in range(period - 1, len(delta)):
            if avg_loss[i] == 0.0:
                out[i + 1] = 100.0 if avg_gain[i] > 0 else 50.0
            else:
                rs = avg_gain[i] / avg_loss[i]
                out[i + 1] = 100.0 - (100.0 / (1.0 + rs))
        return out

    def analyze(self, ctx: MarketContext) -> Signal:
        cfg = {**self.config, **(ctx.config or {})}
        rsi_n = self._cfg_int("rsi_period", 2, cfg)
        rsi_buy_th = self._cfg_float("rsi_buy", 10.0, cfg)
        rsi_exit_th = self._cfg_float("rsi_exit", 65.0, cfg)
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
            return hold("Need more RSI2 candles", strategy_name=self.name, timeframe=ctx.timeframe_primary)

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
        sma_fast = self._rolling_mean_np(close_arr, sma_n)
        rsi = self._rsi_wilder_np(close_arr, rsi_n)

        i = len(close_arr) - 1
        current_close = float(close_arr[i])
        current_atr = float(atr[i]) if np.isfinite(atr[i]) else 0.0
        ema_now = float(ema[i])
        sma_now = float(sma_fast[i]) if np.isfinite(sma_fast[i]) else current_close
        rsi_now = float(rsi[i])

        oversold = rsi_now < rsi_buy_th
        trend_ok = current_close > ema_now
        below_mean = current_close < sma_now

        indicators = {
            "atr": current_atr,
            "ema": ema_now,
            "sma_fast": sma_now,
            "rsi2": rsi_now,
        }
        checklist = [
            {"label": "RSI2", "value": f"{rsi_now:.1f}", "ok": oversold, "hint": f"<{rsi_buy_th:.0f}"},
            {"label": "EMA200", "value": f"{ema_now:.2f}", "ok": trend_ok},
            {"label": "SMA5", "value": f"{sma_now:.2f}", "ok": below_mean, "hint": "close below"},
        ]

        if ctx.has_position:
            if ctx.sl_confirmed:
                return sell("RSI2 SL confirmed", confidence=0.9, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            if current_close > sma_now:
                return sell("Reverted above SMA5", confidence=0.75, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            if rsi_now > rsi_exit_th:
                return sell("RSI2 overbought exit", confidence=0.7, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            return hold("RSI2 dip held, waiting for reversion", confidence=0.5, volatility=current_atr, trail_distance=current_atr * trail_mult if current_atr > 0 else None, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)

        if oversold and trend_ok and below_mean and current_atr > 0:
            return buy(
                "RSI2 oversold dip above EMA200",
                confidence=0.62,
                stop_loss_distance=current_atr * sl_mult,
                trail_distance=current_atr * trail_mult,
                volatility=current_atr,
                indicators=indicators,
                checklist=checklist,
                status_summary=f"RSI2 BUY rsi {rsi_now:.1f}",
                strategy_name=self.name,
                timeframe=ctx.timeframe_primary,
            )

        return hold("Waiting for RSI2 oversold dip", confidence=0.35, volatility=current_atr if current_atr > 0 else None, indicators=indicators, checklist=checklist, status_summary="Waiting RSI2 dip", strategy_name=self.name, timeframe=ctx.timeframe_primary)
