"""ICT Lite strategy plugin.

This is a conservative, engine-agnostic ICT-inspired setup:
trend filter + recent liquidity sweep/reclaim + ATR-based exits.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from xauby.strategies.base import Strategy
from xauby.strategies.context import MarketContext
from xauby.strategies.registry import register
from xauby.strategies.signal import Signal, buy, hold, sell


@register("ict_lite_strategy")
class ICTLiteStrategy(Strategy):
    version = "0.1.0"
    author = "xAuby"
    description = "Conservative ICT-lite sweep/reclaim setup with ATR exits."
    tags = ["ict", "btc", "sweep", "reclaim"]
    required_timeframes = ["4h"]
    min_bars = 120

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        return {
            "ema_fast": 20,
            "ema_slow": 50,
            "atr_period": 14,
            "rsi_period": 14,
            "volume_ma_period": 20,
            "swing_lookback": 20,
            "reclaim_window": 3,
            "mss_lookback": 5,
            "require_mss": True,
            "min_body_atr": 0.15,
            "sl_atr_mult": 2.0,
            "trailing_atr_mult": 1.4,
            "fixed_tp_pct": 2.0,
            "breakeven_sl_enabled": False,
            "breakeven_activation_atr_mult": 1.5,
            "breakeven_buffer_atr_mult": 0.1,
            "rsi_min": 0.0,
            "rsi_max": 100.0,
            "vol_min_ratio": 0.0,
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
        if self._cfg_int("ema_fast", 20) >= self._cfg_int("ema_slow", 50):
            warnings.append("ema_fast should be less than ema_slow")
        if self._cfg_int("swing_lookback", 20) < 5:
            warnings.append("swing_lookback should be >= 5")
        if self._cfg_int("mss_lookback", 5) < 2:
            warnings.append("mss_lookback should be >= 2")
        return warnings

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        # Wilder smoothing (ewm alpha=1/period) to match TradingView RSI;
        # rolling-mean (Cutler) RSI diverges noticeably from TV values.
        delta = close.diff()
        gain = delta.clip(lower=0.0).ewm(alpha=1.0 / period, adjust=False).mean()
        loss = (-delta.clip(upper=0.0)).ewm(alpha=1.0 / period, adjust=False).mean()
        rs = gain / loss.replace(0.0, pd.NA)
        return (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

    @staticmethod
    def _recent_sweep_low(
        df: pd.DataFrame,
        *,
        end_idx: int,
        lookback: int,
        window: int,
    ) -> Dict[str, Any]:
        """Find a recent low sweep against the structure known before each bar.

        The previous implementation compared the whole reclaim window with a
        single current lookback. That folded older sweep lows into the level and
        made reclaim_window behave like "current bar only" in many cases.
        """
        start = max(0, end_idx - window + 1)
        best: Dict[str, Any] = {
            "ok": False,
            "idx": None,
            "level": None,
            "sweep_low": None,
        }
        for j in range(start, end_idx + 1):
            prior = df.iloc[max(0, j - lookback) : j]
            if prior.empty:
                continue
            level = float(prior["low"].min())
            bar_low = float(df["low"].iloc[j])
            if bar_low < level:
                best = {
                    "ok": True,
                    "idx": j,
                    "level": level,
                    "sweep_low": bar_low,
                }
        return best

    MIN_MSS_PIVOT_BARS = 3

    @staticmethod
    def _bullish_mss(
        df: pd.DataFrame,
        *,
        end_idx: int,
        sweep_idx: Optional[int],
        lookback: int,
    ) -> Dict[str, Any]:
        """Bullish market-structure shift: close breaks a post-sweep pivot high.

        The pivot window between the sweep bar and now must hold at least
        MIN_MSS_PIVOT_BARS bars — a 1-2 bar "pivot" confirms almost any close
        above it and makes the MSS check meaningless.
        """
        if sweep_idx is None:
            return {"ok": False, "level": None}
        start = max(0, int(sweep_idx) + 1)
        pivot_start = max(0, end_idx - lookback)
        pivot_start = max(pivot_start, start)
        pivot = df.iloc[pivot_start:end_idx]
        if pivot.empty:
            pivot = df.iloc[max(0, end_idx - lookback) : end_idx]
        if pivot.empty:
            return {"ok": False, "level": None}
        level = float(pivot["high"].max())
        if len(pivot) < ICTLiteStrategy.MIN_MSS_PIVOT_BARS:
            return {"ok": False, "level": level}
        close_now = float(df["close"].iloc[end_idx])
        return {"ok": close_now > level, "level": level}

    def analyze(self, ctx: MarketContext) -> Signal:
        df = ctx.df_primary.copy()
        runtime_cfg = {**self.config, **(getattr(ctx, "config", None) or {})}
        if df.empty or len(df) < self.min_bars:
            return hold(
                f"Need at least {self.min_bars} candles",
                strategy_name=self.name,
                timeframe=ctx.timeframe_primary,
            )

        fast_n = self._cfg_int("ema_fast", 20, runtime_cfg)
        slow_n = self._cfg_int("ema_slow", 50, runtime_cfg)
        atr_n = self._cfg_int("atr_period", 14, runtime_cfg)
        rsi_n = self._cfg_int("rsi_period", 14, runtime_cfg)
        vol_n = self._cfg_int("volume_ma_period", 20, runtime_cfg)
        lookback = self._cfg_int("swing_lookback", 20, runtime_cfg)
        reclaim_window = self._cfg_int("reclaim_window", 3, runtime_cfg)
        mss_lookback = self._cfg_int("mss_lookback", 5, runtime_cfg)
        require_mss = bool(runtime_cfg.get("require_mss", True))
        min_body_atr = self._cfg_float("min_body_atr", 0.15, runtime_cfg)
        sl_mult = self._cfg_float("sl_atr_mult", 2.0, runtime_cfg)
        trail_mult = self._cfg_float("trailing_atr_mult", 1.4, runtime_cfg)
        rsi_min = self._cfg_float("rsi_min", 0.0, runtime_cfg)
        rsi_max = self._cfg_float("rsi_max", 100.0, runtime_cfg)
        vol_min_ratio = self._cfg_float("vol_min_ratio", 0.0, runtime_cfg)

        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close"])
        if len(df) < max(self.min_bars, slow_n + lookback + 5):
            return hold(
                "Not enough clean candles",
                strategy_name=self.name,
                timeframe=ctx.timeframe_primary,
            )

        close = df["close"]
        high = df["high"]
        low = df["low"]
        open_ = df["open"]
        ema_fast = close.ewm(span=fast_n, adjust=False).mean()
        ema_slow = close.ewm(span=slow_n, adjust=False).mean()
        prev_close = close.shift(1)
        tr = pd.concat(
            [
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(atr_n).mean()
        rsi = self._rsi(close, rsi_n)
        volume_ma = df["volume"].rolling(vol_n).mean()

        i = len(df) - 1
        current_close = float(close.iloc[i])
        current_open = float(open_.iloc[i])
        current_atr = float(atr.iloc[i] or 0.0)
        bullish_trend = float(ema_fast.iloc[i]) > float(ema_slow.iloc[i])
        bearish_trend = float(ema_fast.iloc[i]) < float(ema_slow.iloc[i])

        prior = df.iloc[max(0, i - lookback) : i]
        recent_high = float(prior["high"].max())
        recent_low = float(prior["low"].min())

        sweep = self._recent_sweep_low(
            df,
            end_idx=i,
            lookback=lookback,
            window=reclaim_window,
        )
        swept_low = bool(sweep["ok"])
        swept_level = float(sweep["level"]) if sweep["level"] is not None else recent_low
        reclaim_low = swept_low and current_close > swept_level
        mss = self._bullish_mss(
            df,
            end_idx=i,
            sweep_idx=sweep["idx"],
            lookback=mss_lookback,
        )
        bullish_mss = bool(mss["ok"])

        recent_window = df.iloc[max(0, i - reclaim_window + 1) : i + 1]
        swept_high = bool((recent_window["high"] > recent_high).any())
        reject_high = current_close < recent_high
        body_atr = abs(current_close - current_open) / current_atr if current_atr > 0 else 0.0
        strong_body = body_atr >= min_body_atr
        rsi_now = float(rsi.iloc[i])
        rsi_ok = rsi_min <= rsi_now <= rsi_max
        vol_now = float(df["volume"].iloc[i])
        vol_avg = float(volume_ma.iloc[i]) if pd.notna(volume_ma.iloc[i]) else 0.0
        vol_ratio = (vol_now / vol_avg) if vol_avg > 0 else 0.0
        vol_ok = vol_min_ratio <= 0.0 or vol_ratio >= vol_min_ratio

        indicators = {
            "ema_fast": float(ema_fast.iloc[i]),
            "ema_slow": float(ema_slow.iloc[i]),
            "atr": current_atr,
            "recent_high": recent_high,
            "recent_low": recent_low,
            "swept_low_level": swept_level,
            "sweep_low": sweep["sweep_low"],
            "mss_level": mss["level"],
            "swept_low": swept_low,
            "swept_high": swept_high,
            "body_atr": body_atr,
            "rsi": rsi_now,
            "vol_ratio": vol_ratio,
        }
        checklist = [
            {"label": "Trend", "value": "Bull" if bullish_trend else "Bear", "ok": bullish_trend},
            {"label": "Sweep Low", "value": "yes" if swept_low else "no", "ok": swept_low},
            {"label": "Reclaim", "value": f"{current_close:.2f}>{swept_level:.2f}", "ok": reclaim_low},
            {
                "label": "MSS",
                "value": (
                    f"{current_close:.2f}>{float(mss['level']):.2f}"
                    if mss["level"] is not None
                    else "no pivot"
                ),
                "ok": bullish_mss if require_mss else True,
                "hint": "required" if require_mss else "optional",
            },
            {"label": "Body", "value": f"{body_atr:.2f} ATR", "ok": strong_body, "hint": f">={min_body_atr:.2f}"},
            {"label": "RSI", "value": f"{rsi_now:.1f}", "ok": rsi_ok, "hint": f"{rsi_min:.0f}-{rsi_max:.0f}"},
            {"label": "Volume", "value": f"{vol_ratio:.2f}x", "ok": vol_ok, "hint": f">={vol_min_ratio:.2f}x"},
        ]

        if ctx.has_position:
            if ctx.sl_confirmed:
                return sell(
                    "ICT Lite SL confirmed",
                    confidence=0.9,
                    volatility=current_atr,
                    indicators=indicators,
                    checklist=checklist,
                    strategy_name=self.name,
                    timeframe=ctx.timeframe_primary,
                )
            if bearish_trend and swept_high and reject_high:
                return sell(
                    "Bearish sweep/rejection exit",
                    confidence=0.65,
                    volatility=current_atr,
                    indicators=indicators,
                    checklist=checklist,
                    strategy_name=self.name,
                    timeframe=ctx.timeframe_primary,
                )
            return hold(
                "Position managed by ATR trailing stop",
                confidence=0.5,
                volatility=current_atr,
                trail_distance=current_atr * trail_mult if current_atr > 0 else None,
                indicators=indicators,
                checklist=checklist,
                strategy_name=self.name,
                timeframe=ctx.timeframe_primary,
            )

        mss_ok = bullish_mss or not require_mss
        if bullish_trend and swept_low and reclaim_low and mss_ok and strong_body and rsi_ok and vol_ok and current_atr > 0:
            return buy(
                "Bullish liquidity sweep reclaim with MSS",
                confidence=0.68,
                stop_loss_distance=current_atr * sl_mult,
                trail_distance=current_atr * trail_mult,
                volatility=current_atr,
                indicators=indicators,
                checklist=checklist,
                status_summary=f"ICT BUY sweep/reclaim ATR {current_atr:.2f}",
                strategy_name=self.name,
                timeframe=ctx.timeframe_primary,
            )

        reason = "Waiting for bullish sweep/reclaim"
        return hold(
            reason,
            confidence=0.35,
            volatility=current_atr if current_atr > 0 else None,
            indicators=indicators,
            checklist=checklist,
            status_summary=reason,
            strategy_name=self.name,
            timeframe=ctx.timeframe_primary,
        )
