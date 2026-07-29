"""SuperTrend + EMA200 strategy plugin.

BTC 4H SuperTrend + EMA200 trend strategy.  It trades above/below EMA200 on a
fresh SuperTrend flip and uses ATR-based strategy exits plus engine trailing
stop.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from xauby.strategies.base import Strategy
from xauby.strategies.context import MarketContext
from xauby.strategies.registry import register
from xauby.strategies.signal import Signal, buy, close_short, hold, open_short, sell


@register("supertrend_ema200")
class SuperTrendEMA200Strategy(Strategy):
    version = "0.1.0"
    author = "xAuby"
    description = "BTC 4H SuperTrend + EMA200 trend strategy."
    tags = ["btc", "supertrend", "ema200", "4h", "trend"]
    # Certified: clears backtest.acceptance on native BTC-USDT-SWAP
    # (xauby/saas/certificates/okx-btc-supertrend-v1.json) and out-of-sample
    # over 66 fixed-config months.
    maturity = "production"
    required_timeframes = ["4h"]
    min_bars = 240

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        return {
            "ema_period": 200,
            "atr_period": 10,
            "supertrend_mult": 4.0,
            "confirm_bars": 1,
            "entry_on_flip_only": True,
            "rsi_period": 14,
            "rsi_min": 0.0,
            "rsi_max": 100.0,
            "volume_ma_period": 20,
            "vol_min_ratio": 0.0,
            "sl_atr_mult": 3.0,
            "trailing_atr_mult": 2.0,
            "breakeven_sl_enabled": True,
            "breakeven_activation_atr_mult": 1.2,
            "breakeven_buffer_atr_mult": 0.05,
            "exit_on_supertrend_flip": True,
            "exit_on_ema_loss": True,
            "max_calc_bars": 420,
            # Mirrored short side (SuperTrend bear below EMA200).
            # Default off: live long-only behavior is unchanged.
            "enable_short": False,
            # Optional higher-timeframe confirmation.  This is config-gated so
            # the certified/live BTC profile (D1 off) is byte-for-byte
            # unchanged.  When enabled, entries require the last CLOSED D1 bar
            # to have the same SuperTrend+EMA200 alignment as the 4H signal.
            "use_d1_regime_filter": False,
            "use_d1_regime_filter_long": None,
            "use_d1_regime_filter_short": None,
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

    def _cfg_optional_bool(
        self,
        key: str,
        fallback: bool,
        cfg: Optional[Dict[str, Any]] = None,
    ) -> bool:
        src = cfg if cfg is not None else self.config
        value = src.get(key)
        if value is None:
            return fallback
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def _d1_alignment(
        self,
        ctx: MarketContext,
        *,
        ema_n: int,
        atr_n: int,
        st_mult: float,
    ) -> Dict[str, Any]:
        """Alignment from the last D1 candle known closed at the 4H decision.

        Historical OKX frames contain candles that are closed *now*.  During a
        replay, however, the newest D1 row whose open is before the 4H cutoff
        may still have been forming then.  Requiring ``D1 open + 1d <= 4H
        close`` prevents that future daily close from leaking into the signal.
        """
        result: Dict[str, Any] = {
            "ready": False,
            "long_ok": False,
            "short_ok": False,
            "close": 0.0,
            "ema": 0.0,
            "supertrend_bull": None,
            "bars": 0,
        }
        regime = ctx.df_regime
        if regime is None or regime.empty:
            return result

        frame = regime
        if "timestamp" in frame.columns and "timestamp" in ctx.df_primary.columns:
            from xauby.runtime.candle_utils import timeframe_seconds

            primary_open = int(ctx.df_primary["timestamp"].iloc[-1])
            decision_ts = primary_open + timeframe_seconds(ctx.timeframe_primary)
            d1_span = timeframe_seconds(ctx.timeframe_regime or "1d")
            frame = frame[
                frame["timestamp"].astype("int64") + d1_span <= decision_ts
            ]
        if frame.empty:
            return result

        minimum = max(self.min_bars, ema_n + atr_n + 5)
        if len(frame) < minimum:
            result["bars"] = len(frame)
            return result
        max_calc = max(minimum, self._cfg_int("max_calc_bars", 420))
        frame = frame.tail(max_calc)
        try:
            high = frame["high"].to_numpy(dtype="float64", copy=False)
            low = frame["low"].to_numpy(dtype="float64", copy=False)
            close = frame["close"].to_numpy(dtype="float64", copy=False)
        except (TypeError, ValueError):
            numeric = frame[["high", "low", "close"]].apply(
                pd.to_numeric, errors="coerce"
            )
            high = numeric["high"].to_numpy(dtype="float64", copy=False)
            low = numeric["low"].to_numpy(dtype="float64", copy=False)
            close = numeric["close"].to_numpy(dtype="float64", copy=False)
        finite = np.isfinite(high) & np.isfinite(low) & np.isfinite(close)
        high, low, close = high[finite], low[finite], close[finite]
        result["bars"] = len(close)
        if len(close) < minimum:
            return result

        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]
        tr = np.maximum.reduce(
            [high - low, np.abs(high - prev_close), np.abs(low - prev_close)]
        )
        atr = self._rolling_mean_np(tr, atr_n)
        ema = self._ema_np(close, ema_n)
        trend, _line = self._supertrend_np(high, low, close, atr, st_mult)
        last_close = float(close[-1])
        last_ema = float(ema[-1])
        bull = bool(trend[-1])
        result.update(
            ready=True,
            long_ok=bull and last_close > last_ema,
            short_ok=(not bull) and last_close < last_ema,
            close=last_close,
            ema=last_ema,
            supertrend_bull=bull,
        )
        return result

    def validate_config(self) -> List[str]:
        warnings: List[str] = []
        if self._cfg_int("ema_period", 200) < 50:
            warnings.append("ema_period should usually be >= 50")
        if self._cfg_float("supertrend_mult", 3.0) <= 0:
            warnings.append("supertrend_mult must be > 0")
        return warnings

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0.0).rolling(period).mean()
        loss = (-delta.clip(upper=0.0)).rolling(period).mean()
        rs = gain / loss.replace(0.0, pd.NA)
        return (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

    @staticmethod
    def _supertrend(high: pd.Series, low: pd.Series, close: pd.Series, atr: pd.Series, mult: float) -> tuple[pd.Series, pd.Series]:
        hl2 = (high + low) / 2.0
        upper_basic = hl2 + mult * atr
        lower_basic = hl2 - mult * atr
        upper = upper_basic.copy()
        lower = lower_basic.copy()
        trend = pd.Series(False, index=close.index)

        for i in range(1, len(close)):
            prev_i = i - 1
            if pd.isna(atr.iloc[i]):
                trend.iloc[i] = trend.iloc[prev_i]
                continue

            if upper_basic.iloc[i] < upper.iloc[prev_i] or close.iloc[prev_i] > upper.iloc[prev_i]:
                upper.iloc[i] = upper_basic.iloc[i]
            else:
                upper.iloc[i] = upper.iloc[prev_i]

            if lower_basic.iloc[i] > lower.iloc[prev_i] or close.iloc[prev_i] < lower.iloc[prev_i]:
                lower.iloc[i] = lower_basic.iloc[i]
            else:
                lower.iloc[i] = lower.iloc[prev_i]

            if trend.iloc[prev_i]:
                trend.iloc[i] = close.iloc[i] >= lower.iloc[i]
            else:
                trend.iloc[i] = close.iloc[i] > upper.iloc[i]

        st_line = pd.Series(index=close.index, dtype="float64")
        st_line[trend] = lower[trend]
        st_line[~trend] = upper[~trend]
        return trend, st_line

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
    def _rsi_np(close: np.ndarray, period: int) -> np.ndarray:
        out = np.full(len(close), 50.0, dtype="float64")
        if len(close) <= period:
            return out
        delta = np.diff(close, prepend=close[0])
        gain = np.maximum(delta, 0.0)
        loss = np.maximum(-delta, 0.0)
        avg_gain = SuperTrendEMA200Strategy._rolling_mean_np(gain, period)
        avg_loss = SuperTrendEMA200Strategy._rolling_mean_np(loss, period)
        valid = np.isfinite(avg_gain) & np.isfinite(avg_loss) & (avg_loss > 0.0)
        rs = np.zeros(len(close), dtype="float64")
        rs[valid] = avg_gain[valid] / avg_loss[valid]
        out[valid] = 100.0 - (100.0 / (1.0 + rs[valid]))
        out[np.isfinite(avg_gain) & (avg_loss == 0.0) & (avg_gain > 0.0)] = 100.0
        return out

    @staticmethod
    def _supertrend_np(high: np.ndarray, low: np.ndarray, close: np.ndarray, atr: np.ndarray, mult: float) -> tuple[np.ndarray, np.ndarray]:
        hl2 = (high + low) / 2.0
        upper_basic = hl2 + (mult * atr)
        lower_basic = hl2 - (mult * atr)
        upper = upper_basic.copy()
        lower = lower_basic.copy()
        trend = np.zeros(len(close), dtype=bool)

        for i in range(1, len(close)):
            prev = i - 1
            if not np.isfinite(atr[i]):
                trend[i] = trend[prev]
                upper[i] = upper[prev] if np.isfinite(upper[prev]) else upper_basic[i]
                lower[i] = lower[prev] if np.isfinite(lower[prev]) else lower_basic[i]
                continue

            prev_upper = upper[prev] if np.isfinite(upper[prev]) else upper_basic[i]
            prev_lower = lower[prev] if np.isfinite(lower[prev]) else lower_basic[i]
            upper[i] = upper_basic[i] if upper_basic[i] < prev_upper or close[prev] > prev_upper else prev_upper
            lower[i] = lower_basic[i] if lower_basic[i] > prev_lower or close[prev] < prev_lower else prev_lower
            trend[i] = close[i] >= lower[i] if trend[prev] else close[i] > upper[i]

        st_line = np.where(trend, lower, upper)
        return trend, st_line

    def analyze(self, ctx: MarketContext) -> Signal:
        runtime_cfg = {**self.config, **(ctx.config or {})}
        ema_n = self._cfg_int("ema_period", 200, runtime_cfg)
        atr_n = self._cfg_int("atr_period", 10, runtime_cfg)
        st_mult = self._cfg_float("supertrend_mult", 3.0, runtime_cfg)
        confirm_bars = self._cfg_int("confirm_bars", 1, runtime_cfg)
        entry_on_flip_only = self._cfg_bool("entry_on_flip_only", False, runtime_cfg)
        rsi_n = self._cfg_int("rsi_period", 14, runtime_cfg)
        rsi_min = self._cfg_float("rsi_min", 0.0, runtime_cfg)
        rsi_max = self._cfg_float("rsi_max", 100.0, runtime_cfg)
        vol_n = self._cfg_int("volume_ma_period", 20, runtime_cfg)
        vol_min_ratio = self._cfg_float("vol_min_ratio", 0.0, runtime_cfg)
        sl_mult = self._cfg_float("sl_atr_mult", 2.0, runtime_cfg)
        trail_mult = self._cfg_float("trailing_atr_mult", 1.5, runtime_cfg)
        exit_on_st_flip = self._cfg_bool("exit_on_supertrend_flip", True, runtime_cfg)
        exit_on_ema_loss = self._cfg_bool("exit_on_ema_loss", True, runtime_cfg)
        use_d1 = self._cfg_bool("use_d1_regime_filter", False, runtime_cfg)
        use_d1_long = self._cfg_optional_bool(
            "use_d1_regime_filter_long", use_d1, runtime_cfg
        )
        use_d1_short = self._cfg_optional_bool(
            "use_d1_regime_filter_short", use_d1, runtime_cfg
        )
        min_required = max(self.min_bars, ema_n + atr_n + 5, vol_n + rsi_n + 5, confirm_bars + 2)
        max_calc_bars = max(min_required, self._cfg_int("max_calc_bars", 420, runtime_cfg))

        df_source = ctx.df_primary
        df = df_source.tail(max_calc_bars) if len(df_source) > max_calc_bars else df_source
        if df.empty or len(df) < self.min_bars:
            return hold("Need more SuperTrend candles", strategy_name=self.name, timeframe=ctx.timeframe_primary)

        if len(df) < min_required:
            return hold("Not enough clean SuperTrend candles", strategy_name=self.name, timeframe=ctx.timeframe_primary)

        try:
            open_arr = df["open"].to_numpy(dtype="float64", copy=False)
            high_arr = df["high"].to_numpy(dtype="float64", copy=False)
            low_arr = df["low"].to_numpy(dtype="float64", copy=False)
            close_arr = df["close"].to_numpy(dtype="float64", copy=False)
            volume_arr = df["volume"].to_numpy(dtype="float64", copy=False)
        except (TypeError, ValueError):
            numeric_df = df[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
            open_arr = numeric_df["open"].to_numpy(dtype="float64", copy=False)
            high_arr = numeric_df["high"].to_numpy(dtype="float64", copy=False)
            low_arr = numeric_df["low"].to_numpy(dtype="float64", copy=False)
            close_arr = numeric_df["close"].to_numpy(dtype="float64", copy=False)
            volume_arr = numeric_df["volume"].to_numpy(dtype="float64", copy=False)

        finite = np.isfinite(open_arr) & np.isfinite(high_arr) & np.isfinite(low_arr) & np.isfinite(close_arr) & np.isfinite(volume_arr)
        if not finite.all():
            open_arr = open_arr[finite]
            high_arr = high_arr[finite]
            low_arr = low_arr[finite]
            close_arr = close_arr[finite]
            volume_arr = volume_arr[finite]
        if len(close_arr) < min_required:
            return hold("Not enough clean SuperTrend candles", strategy_name=self.name, timeframe=ctx.timeframe_primary)

        prev_close = np.roll(close_arr, 1)
        prev_close[0] = close_arr[0]
        tr = np.maximum.reduce([high_arr - low_arr, np.abs(high_arr - prev_close), np.abs(low_arr - prev_close)])
        atr = self._rolling_mean_np(tr, atr_n)
        ema = self._ema_np(close_arr, ema_n)
        st_bull, st_line = self._supertrend_np(high_arr, low_arr, close_arr, atr, st_mult)
        rsi = self._rsi_np(close_arr, rsi_n)
        vol_ma = self._rolling_mean_np(volume_arr, vol_n)

        i = len(close_arr) - 1
        current_close = float(close_arr[i])
        current_open = float(open_arr[i])
        current_atr = float(atr[i]) if np.isfinite(atr[i]) else 0.0
        ema_now = float(ema[i])
        st_now = bool(st_bull[i])
        st_prev = bool(st_bull[i - 1]) if i > 0 else False
        st_line_now = float(st_line[i]) if np.isfinite(st_line[i]) else 0.0
        bull_confirmed = bool(st_bull[max(0, i - confirm_bars + 1): i + 1].all())
        flip_bull = st_now and not st_prev
        ema_ok = current_close > ema_now
        rsi_now = float(rsi[i])
        rsi_ok = rsi_min <= rsi_now <= rsi_max
        vol_avg = float(vol_ma[i]) if np.isfinite(vol_ma[i]) else 0.0
        vol_ratio = float(volume_arr[i]) / vol_avg if vol_avg > 0 else 0.0
        vol_ok = vol_min_ratio <= 0.0 or vol_ratio >= vol_min_ratio
        body_atr = abs(current_close - current_open) / current_atr if current_atr > 0 else 0.0

        enable_short = self._cfg_bool("enable_short", False, runtime_cfg)
        bear_confirmed = bool((~st_bull[max(0, i - confirm_bars + 1): i + 1]).all())
        flip_bear = (not st_now) and st_prev
        ema_short_ok = current_close < ema_now
        short_timing_ok = flip_bear if entry_on_flip_only else bear_confirmed

        entry_timing_ok = flip_bull if entry_on_flip_only else bull_confirmed
        d1 = (
            self._d1_alignment(
                ctx,
                ema_n=ema_n,
                atr_n=atr_n,
                st_mult=st_mult,
            )
            if use_d1_long or use_d1_short
            else {
                "ready": True,
                "long_ok": True,
                "short_ok": True,
                "close": 0.0,
                "ema": 0.0,
                "supertrend_bull": None,
                "bars": 0,
            }
        )
        indicators = {
            "atr": current_atr,
            "ema": ema_now,
            "supertrend_line": st_line_now,
            "supertrend_bull": st_now,
            "supertrend_flip_bull": flip_bull,
            "rsi": rsi_now,
            "vol_ratio": vol_ratio,
            "body_atr": body_atr,
            "d1_ready": bool(d1["ready"]),
            "d1_close": float(d1["close"]),
            "d1_ema": float(d1["ema"]),
            "d1_supertrend_bull": d1["supertrend_bull"],
        }
        candidate_short = enable_short and not st_now
        ema_check = ema_short_ok if candidate_short else ema_ok
        timing_check = short_timing_ok if candidate_short else entry_timing_ok
        comparison = "<" if candidate_short else ">"
        expected_flip = "bear" if candidate_short else "bull"
        checklist = [
            {"label": "EMA200", "value": f"{current_close:.2f}{comparison}{ema_now:.2f}", "ok": ema_check},
            {
                "label": "SuperTrend",
                "value": "Bear" if candidate_short else "Bull",
                "ok": timing_check,
                "hint": f"Fresh {expected_flip} flip required" if entry_on_flip_only else "Trend confirmed",
            },
            {"label": "RSI", "value": f"{rsi_now:.1f}", "ok": rsi_ok, "hint": f"{rsi_min:.0f}-{rsi_max:.0f}"},
            {"label": "Volume", "value": f"{vol_ratio:.2f}x", "ok": vol_ok, "hint": f">={vol_min_ratio:.2f}x"},
        ]
        candidate_d1_enabled = use_d1_short if candidate_short else use_d1_long
        candidate_d1_ok = bool(d1["short_ok"] if candidate_short else d1["long_ok"])
        if candidate_d1_enabled:
            d1_side = "Bear" if candidate_short else "Bull"
            d1_value = (
                d1_side
                if d1["ready"]
                else f"Not ready ({int(d1['bars'])} bars)"
            )
            checklist.append(
                {
                    "label": "D1 ST+EMA",
                    "value": d1_value,
                    "ok": candidate_d1_ok,
                    "hint": "Last closed D1 candle only",
                }
            )

        if ctx.has_position and str(ctx.position_side or "").upper() == "SHORT":
            if ctx.sl_confirmed:
                return close_short("SuperTrend short Stop loss confirmed", confidence=0.9, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            if exit_on_st_flip and st_now:
                return close_short("SuperTrend flipped bullish", confidence=0.72, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            if exit_on_ema_loss and current_close > ema_now:
                return close_short("Close reclaimed EMA200", confidence=0.68, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            return hold("SuperTrend EMA200 short managed by ATR trailing stop", confidence=0.5, volatility=current_atr, trail_distance=current_atr * trail_mult if current_atr > 0 else None, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)

        if ctx.has_position:
            if ctx.sl_confirmed:
                return sell("SuperTrend EMA200 SL confirmed", confidence=0.9, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            if exit_on_st_flip and not st_now:
                return sell("SuperTrend flipped bearish", confidence=0.72, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            if exit_on_ema_loss and current_close < ema_now:
                return sell("Close lost EMA200", confidence=0.68, volatility=current_atr, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)
            return hold("SuperTrend EMA200 position managed by ATR trailing stop", confidence=0.5, volatility=current_atr, trail_distance=current_atr * trail_mult if current_atr > 0 else None, indicators=indicators, checklist=checklist, strategy_name=self.name, timeframe=ctx.timeframe_primary)

        if (
            ema_ok
            and entry_timing_ok
            and rsi_ok
            and vol_ok
            and current_atr > 0
            and (not use_d1_long or bool(d1["long_ok"]))
        ):
            return buy(
                "SuperTrend bull above EMA200",
                confidence=0.64,
                stop_loss_distance=current_atr * sl_mult,
                trail_distance=current_atr * trail_mult,
                volatility=current_atr,
                indicators=indicators,
                checklist=checklist,
                status_summary=f"ST+EMA BUY ATR {current_atr:.2f}",
                strategy_name=self.name,
                timeframe=ctx.timeframe_primary,
            )

        if (
            enable_short
            and ema_short_ok
            and short_timing_ok
            and rsi_ok
            and vol_ok
            and current_atr > 0
            and (not use_d1_short or bool(d1["short_ok"]))
        ):
            return open_short(
                "SuperTrend bear below EMA200",
                confidence=0.64,
                stop_loss_distance=current_atr * sl_mult,
                trail_distance=current_atr * trail_mult,
                volatility=current_atr,
                indicators=indicators,
                checklist=checklist,
                status_summary=f"ST+EMA SHORT ATR {current_atr:.2f}",
                strategy_name=self.name,
                timeframe=ctx.timeframe_primary,
            )

        if entry_on_flip_only and ema_check:
            hold_reason = f"SuperTrend {'bearish below' if candidate_short else 'bullish above'} EMA200; waiting for a fresh {expected_flip} flip"
            status_summary = "Waiting for fresh ST flip"
        else:
            hold_reason = "Waiting for SuperTrend and EMA200 alignment"
            status_summary = "Waiting ST+EMA200"
        return hold(hold_reason, confidence=0.35, volatility=current_atr if current_atr > 0 else None, indicators=indicators, checklist=checklist, status_summary=status_summary, strategy_name=self.name, timeframe=ctx.timeframe_primary)
