"""BTC EMA pullback strategy plugin.

Designed for paper-testing BTC on lower timeframes: frequent enough to exercise
the engine, but still gated by trend, pullback, momentum, RSI, volume and ATR.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from xauby.strategies.base import Strategy
from xauby.strategies.context import MarketContext
from xauby.strategies.registry import register
from xauby.strategies.signal import Signal, buy, hold, sell


@register("btc_ema_pullback")
class BTCEMAPullbackStrategy(Strategy):
    version = "0.1.0"
    author = "xAuby"
    description = "BTC 1H EMA trend pullback/reclaim strategy with ATR exits."
    tags = ["btc", "1h", "ema", "pullback", "paper-test"]
    required_timeframes = ["1h"]
    min_bars = 120

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        return {
            "ema_fast": 20,
            "ema_slow": 55,
            "ema_trend": 200,
            "atr_period": 14,
            "rsi_period": 14,
            "volume_ma_period": 20,
            "pullback_lookback": 5,
            "pullback_atr_mult": 0.7,
            "breakout_lookback": 3,
            "min_body_atr": 0.05,
            "rsi_min": 45.0,
            "rsi_max": 75.0,
            "vol_min_ratio": 0.6,
            "sl_atr_mult": 1.8,
            "trailing_atr_mult": 1.2,
            "fixed_tp_pct": 1.5,
            "breakeven_sl_enabled": True,
            "breakeven_activation_atr_mult": 1.0,
            "breakeven_buffer_atr_mult": 0.05,
            "exit_on_trend_loss": True,
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
        return str(value).lower() in ("true", "1", "yes", "on")

    def validate_config(self) -> List[str]:
        warnings: List[str] = []
        if self._cfg_int("ema_fast", 20) >= self._cfg_int("ema_slow", 55):
            warnings.append("ema_fast should be less than ema_slow")
        if self._cfg_int("ema_slow", 55) >= self._cfg_int("ema_trend", 200):
            warnings.append("ema_slow should be less than ema_trend")
        return warnings

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0.0).rolling(period).mean()
        loss = (-delta.clip(upper=0.0)).rolling(period).mean()
        rs = gain / loss.replace(0.0, pd.NA)
        return (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

    def analyze(self, ctx: MarketContext) -> Signal:
        df = ctx.df_primary.copy()
        runtime_cfg = {**self.config, **(ctx.config or {})}
        if df.empty or len(df) < self.min_bars:
            return hold("Need more BTC EMA pullback candles", strategy_name=self.name, timeframe=ctx.timeframe_primary)

        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close", "volume"])

        ema_fast_n = self._cfg_int("ema_fast", 20, runtime_cfg)
        ema_slow_n = self._cfg_int("ema_slow", 55, runtime_cfg)
        ema_trend_n = self._cfg_int("ema_trend", 200, runtime_cfg)
        atr_n = self._cfg_int("atr_period", 14, runtime_cfg)
        rsi_n = self._cfg_int("rsi_period", 14, runtime_cfg)
        vol_n = self._cfg_int("volume_ma_period", 20, runtime_cfg)
        pullback_lookback = self._cfg_int("pullback_lookback", 5, runtime_cfg)
        breakout_lookback = self._cfg_int("breakout_lookback", 3, runtime_cfg)
        pullback_atr_mult = self._cfg_float("pullback_atr_mult", 0.7, runtime_cfg)
        min_body_atr = self._cfg_float("min_body_atr", 0.05, runtime_cfg)
        rsi_min = self._cfg_float("rsi_min", 45.0, runtime_cfg)
        rsi_max = self._cfg_float("rsi_max", 75.0, runtime_cfg)
        vol_min_ratio = self._cfg_float("vol_min_ratio", 0.6, runtime_cfg)
        sl_mult = self._cfg_float("sl_atr_mult", 1.8, runtime_cfg)
        trail_mult = self._cfg_float("trailing_atr_mult", 1.2, runtime_cfg)
        exit_on_trend_loss = self._cfg_bool("exit_on_trend_loss", True, runtime_cfg)

        if len(df) < max(self.min_bars, ema_trend_n + 5):
            return hold("Not enough clean BTC EMA candles", strategy_name=self.name, timeframe=ctx.timeframe_primary)

        close = df["close"]
        high = df["high"]
        low = df["low"]
        open_ = df["open"]
        volume = df["volume"]
        ema_fast = close.ewm(span=ema_fast_n, adjust=False).mean()
        ema_slow = close.ewm(span=ema_slow_n, adjust=False).mean()
        ema_trend = close.ewm(span=ema_trend_n, adjust=False).mean()
        prev_close = close.shift(1)
        tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(atr_n).mean()
        rsi = self._rsi(close, rsi_n)
        vol_ma = volume.rolling(vol_n).mean()

        i = len(df) - 1
        current_close = float(close.iloc[i])
        current_open = float(open_.iloc[i])
        current_high = float(high.iloc[i])
        current_low = float(low.iloc[i])
        current_atr = float(atr.iloc[i] or 0.0)
        fast = float(ema_fast.iloc[i])
        slow = float(ema_slow.iloc[i])
        trend = float(ema_trend.iloc[i])

        trend_ok = current_close > trend and fast > slow and slow > trend
        pull_slice = df.iloc[max(0, i - pullback_lookback + 1): i + 1]
        pullback_ok = bool((pull_slice["low"] <= (ema_fast.iloc[pull_slice.index] + current_atr * pullback_atr_mult)).any())
        pivot = df.iloc[max(0, i - breakout_lookback): i]
        pivot_high = float(pivot["high"].max()) if not pivot.empty else current_high
        reclaim_ok = current_close > fast and current_close > pivot_high
        body_atr = abs(current_close - current_open) / current_atr if current_atr > 0 else 0.0
        body_ok = body_atr >= min_body_atr
        rsi_now = float(rsi.iloc[i])
        rsi_ok = rsi_min <= rsi_now <= rsi_max
        vol_avg = float(vol_ma.iloc[i]) if pd.notna(vol_ma.iloc[i]) else 0.0
        vol_ratio = float(volume.iloc[i]) / vol_avg if vol_avg > 0 else 0.0
        vol_ok = vol_min_ratio <= 0.0 or vol_ratio >= vol_min_ratio

        indicators = {
            "ema_fast": fast,
            "ema_slow": slow,
            "ema_trend": trend,
            "atr": current_atr,
            "pivot_high": pivot_high,
            "body_atr": body_atr,
            "rsi": rsi_now,
            "vol_ratio": vol_ratio,
        }
        checklist = [
            {"label": "Trend", "value": f"{current_close:.2f}>{trend:.2f}", "ok": trend_ok},
            {"label": "Pullback", "value": f"low near EMA{ema_fast_n}", "ok": pullback_ok},
            {"label": "Reclaim", "value": f"{current_close:.2f}>{pivot_high:.2f}", "ok": reclaim_ok},
            {"label": "Body", "value": f"{body_atr:.2f} ATR", "ok": body_ok, "hint": f">={min_body_atr:.2f}"},
            {"label": "RSI", "value": f"{rsi_now:.1f}", "ok": rsi_ok, "hint": f"{rsi_min:.0f}-{rsi_max:.0f}"},
            {"label": "Volume", "value": f"{vol_ratio:.2f}x", "ok": vol_ok, "hint": f">={vol_min_ratio:.2f}x"},
        ]

        if ctx.has_position:
            if ctx.sl_confirmed:
                return sell("BTC EMA pullback SL confirmed", confidence=0.9, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            if exit_on_trend_loss and (current_close < slow or fast < slow):
                return sell("BTC EMA trend lost", confidence=0.65, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            return hold("BTC EMA position managed by ATR trailing stop", confidence=0.5, volatility=current_atr, trail_distance=current_atr * trail_mult if current_atr > 0 else None, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)

        if trend_ok and pullback_ok and reclaim_ok and body_ok and rsi_ok and vol_ok and current_atr > 0:
            return buy(
                "BTC EMA pullback reclaim",
                confidence=0.62,
                stop_loss_distance=current_atr * sl_mult,
                trail_distance=current_atr * trail_mult,
                volatility=current_atr,
                indicators=indicators,
                checklist=checklist,
                status_summary=f"BTC EMA BUY ATR {current_atr:.2f}",
                strategy_name=self.name,
                timeframe=ctx.timeframe_primary,
            )

        return hold("Waiting for BTC EMA pullback reclaim", confidence=0.35, volatility=current_atr if current_atr > 0 else None, indicators=indicators, checklist=checklist, status_summary="Waiting for BTC EMA pullback", strategy_name=self.name, timeframe=ctx.timeframe_primary)
