"""Donchian channel trend-following strategy plugin (turtle-style).

Long-only spot: enter on a breakout of the N-bar Donchian high while above
EMA200 (classic time-series momentum), optional ADX strength filter; exit on
loss of the exit-channel midline or EMA200. Targets BULL_* regimes via the
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
    {"entry_len": e, "adx_min": a, "sl_atr_mult": s}
    for e in (48, 96, 120)
    for a in (20.0, 25.0)
    for s in (2.0, 2.5)
]
TARGET_REGIMES = {"BULL_BREAKOUT", "BULL_TREND_STRONG", "BULL_TREND_WEAK"}


@register("donchian_trend")
class DonchianTrendStrategy(Strategy):
    version = "0.1.0"
    author = "xAuby"
    description = "Donchian breakout above EMA200 with ADX filter (turtle-style trend)."
    tags = ["btc", "donchian", "trend", "1h"]
    required_timeframes = ["1h"]
    min_bars = 240

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        return {
            "entry_len": 48,
            "exit_len": 24,
            "ema_period": 200,
            "atr_period": 14,
            "adx_period": 14,
            "adx_min": 20.0,
            "vol_ma_period": 20,
            "vol_min_ratio": 0.0,
            "sl_atr_mult": 2.0,
            "trailing_atr_mult": 6.0,
            "breakeven_sl_enabled": False,
            "breakeven_activation_atr_mult": 1.5,
            "breakeven_buffer_atr_mult": 0.1,
            "exit_on_ema_loss": True,
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
        warnings: List[str] = []
        if self._cfg_int("entry_len", 48) < self._cfg_int("exit_len", 24):
            warnings.append("entry_len should usually be >= exit_len")
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
    def _wilder_smooth(values: np.ndarray, period: int) -> np.ndarray:
        out = np.full(len(values), np.nan, dtype="float64")
        if len(values) < period:
            return out
        out[period - 1] = values[:period].sum()
        for i in range(period, len(values)):
            out[i] = out[i - 1] - (out[i - 1] / period) + values[i]
        return out

    @classmethod
    def _adx_np(cls, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
        n = len(close)
        adx = np.full(n, 0.0, dtype="float64")
        if n < period * 2 + 1:
            return adx
        up = high[1:] - high[:-1]
        down = low[:-1] - low[1:]
        plus_dm = np.where((up > down) & (up > 0.0), up, 0.0)
        minus_dm = np.where((down > up) & (down > 0.0), down, 0.0)
        prev_close = close[:-1]
        tr = np.maximum.reduce(
            [high[1:] - low[1:], np.abs(high[1:] - prev_close), np.abs(low[1:] - prev_close)]
        )
        tr_s = cls._wilder_smooth(tr, period)
        plus_s = cls._wilder_smooth(plus_dm, period)
        minus_s = cls._wilder_smooth(minus_dm, period)
        with np.errstate(divide="ignore", invalid="ignore"):
            plus_di = 100.0 * plus_s / tr_s
            minus_di = 100.0 * minus_s / tr_s
            dx = 100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
        dx = np.nan_to_num(dx, nan=0.0)
        adx_part = np.full(len(dx), 0.0, dtype="float64")
        start = period * 2 - 2
        if start < len(dx):
            adx_part[start] = dx[period - 1 : start + 1].mean()
            for i in range(start + 1, len(dx)):
                adx_part[i] = (adx_part[i - 1] * (period - 1) + dx[i]) / period
        adx[1:] = adx_part
        return adx

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
        adx_n = self._cfg_int("adx_period", 14, cfg)
        adx_min = self._cfg_float("adx_min", 20.0, cfg)
        vol_n = self._cfg_int("vol_ma_period", 20, cfg)
        vol_min_ratio = self._cfg_float("vol_min_ratio", 0.0, cfg)
        sl_mult = self._cfg_float("sl_atr_mult", 2.0, cfg)
        trail_mult = self._cfg_float("trailing_atr_mult", 2.5, cfg)
        exit_on_ema_loss = self._cfg_bool("exit_on_ema_loss", True, cfg)
        min_required = max(self.min_bars, ema_n + atr_n + 5, entry_len + 5, adx_n * 2 + 5)
        max_calc_bars = max(min_required, self._cfg_int("max_calc_bars", 420, cfg))

        df_source = ctx.df_primary
        df = df_source.tail(max_calc_bars) if len(df_source) > max_calc_bars else df_source
        if df.empty or len(df) < min_required:
            return hold("Need more Donchian candles", strategy_name=self.name, timeframe=ctx.timeframe_primary)

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
        ema = self._ema_np(close_arr, ema_n)
        adx = self._adx_np(high_arr, low_arr, close_arr, adx_n)
        vol_ma = self._rolling_mean_np(volume_arr, vol_n)

        i = len(close_arr) - 1
        current_close = float(close_arr[i])
        current_atr = float(atr[i]) if np.isfinite(atr[i]) else 0.0
        ema_now = float(ema[i])
        adx_now = float(adx[i])

        # Channels exclude the current bar (prior N bars) — classic Donchian.
        entry_high = float(high_arr[i - entry_len : i].max())
        exit_low = float(low_arr[i - exit_len : i].min())
        exit_high = float(high_arr[i - exit_len : i].max())
        exit_mid = (exit_high + exit_low) / 2.0

        breakout = current_close > entry_high
        ema_ok = current_close > ema_now
        adx_ok = adx_min <= 0.0 or adx_now >= adx_min
        vol_avg = float(vol_ma[i]) if np.isfinite(vol_ma[i]) else 0.0
        vol_ratio = float(volume_arr[i]) / vol_avg if vol_avg > 0 else 0.0
        vol_ok = vol_min_ratio <= 0.0 or vol_ratio >= vol_min_ratio

        indicators = {
            "atr": current_atr,
            "ema": ema_now,
            "adx": adx_now,
            "donchian_entry_high": entry_high,
            "donchian_exit_mid": exit_mid,
            "vol_ratio": vol_ratio,
        }
        checklist = [
            {"label": "Breakout", "value": f"{current_close:.2f}>{entry_high:.2f}", "ok": breakout},
            {"label": "EMA200", "value": f"{ema_now:.2f}", "ok": ema_ok},
            {"label": "ADX", "value": f"{adx_now:.1f}", "ok": adx_ok, "hint": f">={adx_min:.0f}"},
            {"label": "Volume", "value": f"{vol_ratio:.2f}x", "ok": vol_ok, "hint": f">={vol_min_ratio:.2f}x"},
        ]

        if ctx.has_position:
            if ctx.sl_confirmed:
                return sell("Donchian SL confirmed", confidence=0.9, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            if current_close < exit_mid:
                return sell("Close lost Donchian exit midline", confidence=0.72, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            if exit_on_ema_loss and current_close < ema_now:
                return sell("Close lost EMA200", confidence=0.68, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            return hold("Donchian trend position riding", confidence=0.5, volatility=current_atr, trail_distance=current_atr * trail_mult if current_atr > 0 else None, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)

        if breakout and ema_ok and adx_ok and vol_ok and current_atr > 0:
            return buy(
                "Donchian breakout above EMA200",
                confidence=0.66,
                stop_loss_distance=current_atr * sl_mult,
                trail_distance=current_atr * trail_mult,
                volatility=current_atr,
                indicators=indicators,
                checklist=checklist,
                status_summary=f"Donchian BUY ATR {current_atr:.2f}",
                strategy_name=self.name,
                timeframe=ctx.timeframe_primary,
            )

        return hold("Waiting for Donchian breakout", confidence=0.35, volatility=current_atr if current_atr > 0 else None, indicators=indicators, checklist=checklist, status_summary="Waiting Donchian", strategy_name=self.name, timeframe=ctx.timeframe_primary)
