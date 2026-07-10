"""LuxAlgo-inspired Smart Money Concepts strategy plugin.

This is not a one-to-one port of the TradingView drawing script. It converts
the useful SMC primitives into deterministic trade gates that fit xAuby's
strategy contract:

* internal/swing BOS and CHoCH structure breaks
* liquidity sweep/reclaim against confirmed internal structure
* simple bullish/bearish order-block zones
* simple fair-value-gap zones
* premium/discount diagnostics from the latest swing range
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from xauby.strategies.base import Strategy
from xauby.strategies.context import MarketContext
from xauby.strategies.registry import register
from xauby.strategies.signal import Signal, buy, close_short, hold, open_short, sell


TARGET_REGIMES = {"BULL_BREAKOUT", "BULL_TREND_STRONG", "BEAR_TREND_STRONG", "VOLATILITY_EXPANSION"}


@register("smc_luxalgo")
class SMCLuxAlgoStrategy(Strategy):
    version = "0.1.0"
    author = "xAuby"
    description = "Smart Money Concepts structure strategy inspired by LuxAlgo SMC."
    tags = ["smc", "luxalgo", "structure", "bos", "choch"]
    required_timeframes = ["4h"]
    min_bars = 180

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        return {
            "internal_length": 5,
            "swing_length": 20,
            "atr_period": 14,
            "volume_ma_period": 20,
            "vol_min_ratio": 0.0,
            "entry_event_window": 3,
            "require_swing_bias": True,
            "confluence_min_score": 1.0,
            "require_liquidity_sweep": False,
            "use_fair_value_gap": True,
            "fvg_lookback": 20,
            "fvg_min_atr": 0.0,
            "use_order_block": True,
            "order_block_lookback": 12,
            "order_block_ttl_bars": 60,
            "zone_proximity_atr": 0.35,
            "use_discount_premium": True,
            "equal_levels_threshold_atr": 0.1,
            "sl_atr_mult": 2.0,
            "sl_buffer_atr": 0.2,
            "trailing_atr_mult": 1.8,
            "allow_short": False,
            "exit_on_opposite_structure": True,
            "max_calc_bars": 520,
            "fixed_tp_pct": 0.0,
            "breakeven_sl_enabled": True,
            "breakeven_activation_atr_mult": 1.4,
            "breakeven_buffer_atr_mult": 0.05,
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
        cfg = self.config
        warnings: List[str] = []
        if self._cfg_int("internal_length", 5, cfg) < 2:
            warnings.append("internal_length must be >= 2")
        if self._cfg_int("swing_length", 20, cfg) < 3:
            warnings.append("swing_length must be >= 3")
        if self._cfg_int("swing_length", 20, cfg) < self._cfg_int("internal_length", 5, cfg):
            warnings.append("swing_length should usually be >= internal_length")
        if self._cfg_float("confluence_min_score", 1.0, cfg) < 0.0:
            warnings.append("confluence_min_score must be >= 0")
        if self._cfg_float("sl_atr_mult", 2.0, cfg) <= 0.0:
            warnings.append("sl_atr_mult must be > 0")
        if self._cfg_float("trailing_atr_mult", 1.8, cfg) < 0.0:
            warnings.append("trailing_atr_mult must be >= 0")
        return warnings

    @staticmethod
    def _rolling_mean_np(values: np.ndarray, period: int) -> np.ndarray:
        out = np.full(len(values), np.nan, dtype="float64")
        if period <= 0 or len(values) < period:
            return out
        csum = np.cumsum(np.insert(values, 0, 0.0))
        out[period - 1 :] = (csum[period:] - csum[:-period]) / period
        return out

    @classmethod
    def _atr_np(cls, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]
        tr = np.maximum.reduce(
            [high - low, np.abs(high - prev_close), np.abs(low - prev_close)]
        )
        return cls._rolling_mean_np(tr, period)

    @staticmethod
    def _pivot_flags_np(high: np.ndarray, low: np.ndarray, length: int) -> Tuple[np.ndarray, np.ndarray]:
        n = len(high)
        pivot_high = np.zeros(n, dtype=bool)
        pivot_low = np.zeros(n, dtype=bool)
        if n < (length * 2) + 1:
            return pivot_high, pivot_low
        for idx in range(length, n - length):
            hi_window = high[idx - length : idx + length + 1]
            lo_window = low[idx - length : idx + length + 1]
            hi = high[idx]
            lo = low[idx]
            if np.isfinite(hi) and hi == np.nanmax(hi_window) and np.count_nonzero(hi_window == hi) == 1:
                pivot_high[idx] = True
            if np.isfinite(lo) and lo == np.nanmin(lo_window) and np.count_nonzero(lo_window == lo) == 1:
                pivot_low[idx] = True
        return pivot_high, pivot_low

    @classmethod
    def _structure_state(
        cls,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        length: int,
    ) -> Dict[str, np.ndarray]:
        n = len(close)
        pivot_high, pivot_low = cls._pivot_flags_np(high, low, length)
        high_level = np.full(n, np.nan, dtype="float64")
        low_level = np.full(n, np.nan, dtype="float64")
        trend = np.zeros(n, dtype="int64")
        event_side = np.full(n, "", dtype=object)
        event_type = np.full(n, "", dtype=object)

        last_high = np.nan
        last_low = np.nan
        high_broken = True
        low_broken = True
        state = 0
        for idx in range(n):
            confirmed_idx = idx - length
            if confirmed_idx >= 0:
                if pivot_high[confirmed_idx]:
                    last_high = float(high[confirmed_idx])
                    high_broken = False
                if pivot_low[confirmed_idx]:
                    last_low = float(low[confirmed_idx])
                    low_broken = False

            high_level[idx] = last_high
            low_level[idx] = last_low

            bull_break = np.isfinite(last_high) and not high_broken and close[idx] > last_high
            bear_break = np.isfinite(last_low) and not low_broken and close[idx] < last_low
            if bull_break and not bear_break:
                event_side[idx] = "bullish"
                event_type[idx] = "BOS" if state >= 0 else "CHOCH"
                state = 1
                high_broken = True
            elif bear_break and not bull_break:
                event_side[idx] = "bearish"
                event_type[idx] = "BOS" if state <= 0 else "CHOCH"
                state = -1
                low_broken = True
            elif bull_break and bear_break:
                if close[idx] >= (last_high + last_low) / 2.0:
                    event_side[idx] = "bullish"
                    event_type[idx] = "BOS" if state >= 0 else "CHOCH"
                    state = 1
                    high_broken = True
                else:
                    event_side[idx] = "bearish"
                    event_type[idx] = "BOS" if state <= 0 else "CHOCH"
                    state = -1
                    low_broken = True
            trend[idx] = state

        return {
            "pivot_high": pivot_high,
            "pivot_low": pivot_low,
            "high_level": high_level,
            "low_level": low_level,
            "trend": trend,
            "event_side": event_side,
            "event_type": event_type,
        }

    @staticmethod
    def _find_order_block_np(
        open_arr: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        event_idx: int,
        lookback: int,
        side: str,
    ) -> Tuple[float, float]:
        start = max(0, event_idx - lookback)
        fallback_idx: Optional[int] = None
        for idx in range(event_idx - 1, start - 1, -1):
            if side == "bullish" and close[idx] < open_arr[idx]:
                return float(low[idx]), float(high[idx])
            if side == "bearish" and close[idx] > open_arr[idx]:
                return float(low[idx]), float(high[idx])
            if fallback_idx is None:
                fallback_idx = idx
        if fallback_idx is None:
            return np.nan, np.nan
        return float(low[fallback_idx]), float(high[fallback_idx])

    @staticmethod
    def _safe_ratio(num: float, den: float) -> float:
        return float(num / den) if den and np.isfinite(den) else 0.0

    @classmethod
    def compute_smc(cls, df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
        cfg = {**cls.default_config(), **(config or {})}
        internal_len = max(2, int(cfg.get("internal_length", 5)))
        swing_len = max(3, int(cfg.get("swing_length", 20)))
        atr_n = max(1, int(cfg.get("atr_period", 14)))
        vol_n = max(1, int(cfg.get("volume_ma_period", 20)))
        vol_min_ratio = float(cfg.get("vol_min_ratio", 0.0))
        fvg_lookback = max(1, int(cfg.get("fvg_lookback", 20)))
        fvg_min_atr = max(0.0, float(cfg.get("fvg_min_atr", 0.0)))
        ob_lookback = max(1, int(cfg.get("order_block_lookback", 12)))
        ob_ttl = max(1, int(cfg.get("order_block_ttl_bars", 60)))
        sweep_window = max(1, int(cfg.get("entry_event_window", 3)))
        proximity_atr = max(0.0, float(cfg.get("zone_proximity_atr", 0.35)))
        equal_threshold = max(0.0, float(cfg.get("equal_levels_threshold_atr", 0.1)))

        out = df.copy()
        if "volume" not in out.columns:
            out["volume"] = 0.0
        for col in ("open", "high", "low", "close", "volume"):
            out[col] = pd.to_numeric(out[col], errors="coerce")

        open_arr = out["open"].to_numpy(dtype="float64", copy=False)
        high = out["high"].to_numpy(dtype="float64", copy=False)
        low = out["low"].to_numpy(dtype="float64", copy=False)
        close = out["close"].to_numpy(dtype="float64", copy=False)
        volume = out["volume"].to_numpy(dtype="float64", copy=False)
        n = len(out)

        if n == 0:
            return out

        atr = cls._atr_np(high, low, close, atr_n)
        vol_ma = cls._rolling_mean_np(volume, vol_n)
        volume_ratio = np.divide(
            volume,
            vol_ma,
            out=np.zeros(n, dtype="float64"),
            where=np.isfinite(vol_ma) & (vol_ma > 0.0),
        )

        internal = cls._structure_state(high, low, close, internal_len)
        swing = cls._structure_state(high, low, close, swing_len)

        bull_fvg_low = np.full(n, np.nan, dtype="float64")
        bull_fvg_high = np.full(n, np.nan, dtype="float64")
        bear_fvg_low = np.full(n, np.nan, dtype="float64")
        bear_fvg_high = np.full(n, np.nan, dtype="float64")
        last_bull_fvg = (np.nan, np.nan, -10_000_000)
        last_bear_fvg = (np.nan, np.nan, -10_000_000)
        for idx in range(n):
            if idx >= 2:
                min_gap = (atr[idx] * fvg_min_atr) if np.isfinite(atr[idx]) else 0.0
                if low[idx] > high[idx - 2] and (low[idx] - high[idx - 2]) >= min_gap:
                    last_bull_fvg = (float(high[idx - 2]), float(low[idx]), idx)
                if high[idx] < low[idx - 2] and (low[idx - 2] - high[idx]) >= min_gap:
                    last_bear_fvg = (float(high[idx]), float(low[idx - 2]), idx)

            b_low, b_high, b_idx = last_bull_fvg
            if b_idx >= 0 and idx > b_idx and low[idx] <= b_low:
                last_bull_fvg = (np.nan, np.nan, -10_000_000)
            elif b_idx >= 0 and idx - b_idx <= fvg_lookback:
                bull_fvg_low[idx] = b_low
                bull_fvg_high[idx] = b_high

            s_low, s_high, s_idx = last_bear_fvg
            if s_idx >= 0 and idx > s_idx and high[idx] >= s_high:
                last_bear_fvg = (np.nan, np.nan, -10_000_000)
            elif s_idx >= 0 and idx - s_idx <= fvg_lookback:
                bear_fvg_low[idx] = s_low
                bear_fvg_high[idx] = s_high

        bull_ob_low = np.full(n, np.nan, dtype="float64")
        bull_ob_high = np.full(n, np.nan, dtype="float64")
        bear_ob_low = np.full(n, np.nan, dtype="float64")
        bear_ob_high = np.full(n, np.nan, dtype="float64")
        last_bull_ob = (np.nan, np.nan, -10_000_000)
        last_bear_ob = (np.nan, np.nan, -10_000_000)
        for idx in range(n):
            bull_event = internal["event_side"][idx] == "bullish" or swing["event_side"][idx] == "bullish"
            bear_event = internal["event_side"][idx] == "bearish" or swing["event_side"][idx] == "bearish"
            if bull_event:
                last_bull_ob = (*cls._find_order_block_np(open_arr, high, low, close, idx, ob_lookback, "bullish"), idx)
            if bear_event:
                last_bear_ob = (*cls._find_order_block_np(open_arr, high, low, close, idx, ob_lookback, "bearish"), idx)

            ob_low, ob_high, ob_idx = last_bull_ob
            if ob_idx >= 0 and idx > ob_idx and close[idx] < ob_low:
                last_bull_ob = (np.nan, np.nan, -10_000_000)
            elif ob_idx >= 0 and idx - ob_idx <= ob_ttl:
                bull_ob_low[idx] = ob_low
                bull_ob_high[idx] = ob_high

            ob_low, ob_high, ob_idx = last_bear_ob
            if ob_idx >= 0 and idx > ob_idx and close[idx] > ob_high:
                last_bear_ob = (np.nan, np.nan, -10_000_000)
            elif ob_idx >= 0 and idx - ob_idx <= ob_ttl:
                bear_ob_low[idx] = ob_low
                bear_ob_high[idx] = ob_high

        bull_sweep = np.zeros(n, dtype=bool)
        bear_sweep = np.zeros(n, dtype=bool)
        sweep_level = np.full(n, np.nan, dtype="float64")
        for idx in range(n):
            start = max(0, idx - sweep_window + 1)
            for j in range(start, idx + 1):
                low_level = internal["low_level"][j - 1] if j > 0 else np.nan
                high_level = internal["high_level"][j - 1] if j > 0 else np.nan
                if np.isfinite(low_level) and low[j] < low_level and close[idx] > low_level:
                    bull_sweep[idx] = True
                    sweep_level[idx] = low_level
                if np.isfinite(high_level) and high[j] > high_level and close[idx] < high_level:
                    bear_sweep[idx] = True
                    sweep_level[idx] = high_level

        equal_high = np.zeros(n, dtype=bool)
        equal_low = np.zeros(n, dtype=bool)
        last_pivot_high = np.nan
        last_pivot_low = np.nan
        for idx in range(n):
            if swing["pivot_high"][idx]:
                threshold = atr[idx] * equal_threshold if np.isfinite(atr[idx]) else 0.0
                equal_high[idx] = np.isfinite(last_pivot_high) and abs(high[idx] - last_pivot_high) <= threshold
                last_pivot_high = high[idx]
            if swing["pivot_low"][idx]:
                threshold = atr[idx] * equal_threshold if np.isfinite(atr[idx]) else 0.0
                equal_low[idx] = np.isfinite(last_pivot_low) and abs(low[idx] - last_pivot_low) <= threshold
                last_pivot_low = low[idx]

        swing_high = swing["high_level"]
        swing_low = swing["low_level"]
        swing_range = swing_high - swing_low
        equilibrium = np.where(np.isfinite(swing_range) & (swing_range > 0.0), swing_low + (swing_range * 0.5), np.nan)
        premium_line = np.where(np.isfinite(swing_range) & (swing_range > 0.0), swing_low + (swing_range * 0.75), np.nan)
        discount_line = np.where(np.isfinite(swing_range) & (swing_range > 0.0), swing_low + (swing_range * 0.25), np.nan)
        price_zone = np.full(n, "NEUTRAL", dtype=object)
        price_zone[np.isfinite(equilibrium) & (close >= premium_line)] = "PREMIUM"
        price_zone[np.isfinite(equilibrium) & (close <= discount_line)] = "DISCOUNT"
        price_zone[np.isfinite(equilibrium) & (close > discount_line) & (close < premium_line)] = "EQUILIBRIUM"

        event_side = np.full(n, "", dtype=object)
        event_type = np.full(n, "", dtype=object)
        for idx in range(n):
            if swing["event_side"][idx]:
                event_side[idx] = swing["event_side"][idx]
                event_type[idx] = swing["event_type"][idx]
            elif internal["event_side"][idx]:
                event_side[idx] = internal["event_side"][idx]
                event_type[idx] = internal["event_type"][idx]

        last_event_side = np.full(n, "", dtype=object)
        last_event_type = np.full(n, "", dtype=object)
        last_event_bars = np.full(n, 10_000_000, dtype="int64")
        last_side = ""
        last_type = ""
        last_idx = -10_000_000
        for idx in range(n):
            if event_side[idx]:
                last_side = str(event_side[idx])
                last_type = str(event_type[idx])
                last_idx = idx
            last_event_side[idx] = last_side
            last_event_type[idx] = last_type
            last_event_bars[idx] = idx - last_idx

        bias = np.full(n, "NEUTRAL", dtype=object)
        combined_trend = np.where(swing["trend"] != 0, swing["trend"], internal["trend"])
        bias[combined_trend > 0] = "BULLISH"
        bias[combined_trend < 0] = "BEARISH"
        zone = bias.copy()
        for idx in range(n):
            if event_side[idx] == "bullish":
                zone[idx] = "BULLISH_CHOCH" if event_type[idx] == "CHOCH" else "BULLISH_BOS"
            elif event_side[idx] == "bearish":
                zone[idx] = "BEARISH_CHOCH" if event_type[idx] == "CHOCH" else "BEARISH_BOS"

        prox = np.nan_to_num(atr, nan=0.0) * proximity_atr
        bull_ob_support = np.isfinite(bull_ob_low) & (low <= bull_ob_high + prox) & (close >= bull_ob_low - prox)
        bear_ob_resistance = np.isfinite(bear_ob_high) & (high >= bear_ob_low - prox) & (close <= bear_ob_high + prox)
        bull_fvg_support = np.isfinite(bull_fvg_low) & (low <= bull_fvg_high + prox) & (close >= bull_fvg_low - prox)
        bear_fvg_resistance = np.isfinite(bear_fvg_high) & (high >= bear_fvg_low - prox) & (close <= bear_fvg_high + prox)
        discount_ok = np.isfinite(equilibrium) & (close <= equilibrium)
        premium_ok = np.isfinite(equilibrium) & (close >= equilibrium)
        volume_ok = (vol_min_ratio <= 0.0) | (volume_ratio >= vol_min_ratio)

        bull_score = bull_sweep.astype(float)
        bear_score = bear_sweep.astype(float)
        if bool(cfg.get("use_order_block", True)):
            bull_score = bull_score + bull_ob_support.astype(float)
            bear_score = bear_score + bear_ob_resistance.astype(float)
        if bool(cfg.get("use_fair_value_gap", True)):
            bull_score = bull_score + bull_fvg_support.astype(float)
            bear_score = bear_score + bear_fvg_resistance.astype(float)
        if bool(cfg.get("use_discount_premium", True)):
            bull_score = bull_score + discount_ok.astype(float)
            bear_score = bear_score + premium_ok.astype(float)
        if vol_min_ratio > 0.0:
            bull_score = bull_score + volume_ok.astype(float)
            bear_score = bear_score + volume_ok.astype(float)

        out["smc_atr"] = atr
        out["smc_volume_ratio"] = volume_ratio
        out["smc_volume_ok"] = volume_ok
        out["smc_internal_high"] = internal["high_level"]
        out["smc_internal_low"] = internal["low_level"]
        out["smc_internal_trend"] = internal["trend"]
        out["smc_swing_high"] = swing_high
        out["smc_swing_low"] = swing_low
        out["smc_swing_trend"] = swing["trend"]
        out["smc_event_side"] = event_side
        out["smc_event_type"] = event_type
        out["smc_last_event_side"] = last_event_side
        out["smc_last_event_type"] = last_event_type
        out["smc_last_event_bars"] = last_event_bars
        out["smc_bias"] = bias
        out["smc_zone"] = zone
        out["smc_bull_sweep"] = bull_sweep
        out["smc_bear_sweep"] = bear_sweep
        out["smc_sweep_level"] = sweep_level
        out["smc_equal_high"] = equal_high
        out["smc_equal_low"] = equal_low
        out["smc_bull_fvg_low"] = bull_fvg_low
        out["smc_bull_fvg_high"] = bull_fvg_high
        out["smc_bear_fvg_low"] = bear_fvg_low
        out["smc_bear_fvg_high"] = bear_fvg_high
        out["smc_bull_ob_low"] = bull_ob_low
        out["smc_bull_ob_high"] = bull_ob_high
        out["smc_bear_ob_low"] = bear_ob_low
        out["smc_bear_ob_high"] = bear_ob_high
        out["smc_equilibrium"] = equilibrium
        out["smc_premium_line"] = premium_line
        out["smc_discount_line"] = discount_line
        out["smc_price_zone"] = price_zone
        out["smc_bull_ob_support"] = bull_ob_support
        out["smc_bear_ob_resistance"] = bear_ob_resistance
        out["smc_bull_fvg_support"] = bull_fvg_support
        out["smc_bear_fvg_resistance"] = bear_fvg_resistance
        out["smc_discount_ok"] = discount_ok
        out["smc_premium_ok"] = premium_ok
        out["smc_bull_confluence_score"] = bull_score
        out["smc_bear_confluence_score"] = bear_score
        return out

    def _base_signal_kwargs(self, row: pd.Series, checklist: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
        atr = float(row.get("smc_atr", 0.0) or 0.0)
        trail_mult = self._cfg_float("trailing_atr_mult", 1.8, cfg)
        indicators = {
            "atr": atr,
            "bias": str(row.get("smc_bias", "NEUTRAL")),
            "zone": str(row.get("smc_zone", "NEUTRAL")),
            "last_event_side": str(row.get("smc_last_event_side", "")),
            "last_event_type": str(row.get("smc_last_event_type", "")),
            "last_event_bars": int(row.get("smc_last_event_bars", 0) or 0),
            "swing_high": float(row["smc_swing_high"]) if pd.notna(row.get("smc_swing_high")) else None,
            "swing_low": float(row["smc_swing_low"]) if pd.notna(row.get("smc_swing_low")) else None,
            "internal_high": float(row["smc_internal_high"]) if pd.notna(row.get("smc_internal_high")) else None,
            "internal_low": float(row["smc_internal_low"]) if pd.notna(row.get("smc_internal_low")) else None,
            "bull_confluence_score": float(row.get("smc_bull_confluence_score", 0.0) or 0.0),
            "bear_confluence_score": float(row.get("smc_bear_confluence_score", 0.0) or 0.0),
            "volume_ratio": float(row.get("smc_volume_ratio", 0.0) or 0.0),
            "price_zone": str(row.get("smc_price_zone", "NEUTRAL")),
        }
        return {
            "volatility": atr if atr > 0 else None,
            "trail_distance": atr * trail_mult if atr > 0 and trail_mult > 0 else None,
            "indicators": indicators,
            "checklist": checklist,
            "strategy_name": self.name,
        }

    def _stop_hints(self, row: pd.Series, side: str, cfg: Dict[str, Any]) -> Dict[str, float]:
        close_now = float(row["close"])
        atr = float(row.get("smc_atr", 0.0) or 0.0)
        sl_mult = self._cfg_float("sl_atr_mult", 2.0, cfg)
        buffer = atr * max(0.0, self._cfg_float("sl_buffer_atr", 0.2, cfg))
        if side == "long":
            candidates = []
            if pd.notna(row.get("smc_internal_low")):
                candidates.append(float(row["smc_internal_low"]) - buffer)
            if pd.notna(row.get("smc_swing_low")):
                candidates.append(float(row["smc_swing_low"]) - buffer)
            if pd.notna(row.get("smc_bull_ob_low")):
                candidates.append(float(row["smc_bull_ob_low"]) - buffer)
            stop = min(candidates) if candidates else close_now - (atr * sl_mult)
            if not np.isfinite(stop) or stop >= close_now:
                stop = close_now - (atr * sl_mult)
            return {"stop_loss_price": stop, "stop_loss_distance": max(0.0, close_now - stop)}
        candidates = []
        if pd.notna(row.get("smc_internal_high")):
            candidates.append(float(row["smc_internal_high"]) + buffer)
        if pd.notna(row.get("smc_swing_high")):
            candidates.append(float(row["smc_swing_high"]) + buffer)
        if pd.notna(row.get("smc_bear_ob_high")):
            candidates.append(float(row["smc_bear_ob_high"]) + buffer)
        stop = max(candidates) if candidates else close_now + (atr * sl_mult)
        if not np.isfinite(stop) or stop <= close_now:
            stop = close_now + (atr * sl_mult)
        return {"stop_loss_price": stop, "stop_loss_distance": max(0.0, stop - close_now)}

    def analyze(self, ctx: MarketContext) -> Signal:
        cfg = {**self.config, **(ctx.config or {})}
        max_calc_bars = max(self.min_bars, self._cfg_int("max_calc_bars", 520, cfg))
        source = ctx.df_primary
        df = source.tail(max_calc_bars).copy() if len(source) > max_calc_bars else source.copy()
        if df.empty or len(df) < self.min_bars:
            return hold(
                f"Need at least {self.min_bars} SMC candles",
                strategy_name=self.name,
                timeframe=ctx.timeframe_primary,
            )
        missing = [col for col in ("open", "high", "low", "close") if col not in df.columns]
        if missing:
            return hold(
                "SMC missing OHLC columns",
                strategy_name=self.name,
                timeframe=ctx.timeframe_primary,
            )

        df = self.compute_smc(df, cfg).dropna(subset=["open", "high", "low", "close"])
        if df.empty or len(df) < self.min_bars:
            return hold(
                "Not enough clean SMC candles",
                strategy_name=self.name,
                timeframe=ctx.timeframe_primary,
            )

        row = df.iloc[-1]
        close_now = float(row["close"])
        atr = float(row.get("smc_atr", 0.0) or 0.0)
        event_window = self._cfg_int("entry_event_window", 3, cfg)
        min_score = self._cfg_float("confluence_min_score", 1.0, cfg)
        require_swing_bias = self._cfg_bool("require_swing_bias", True, cfg)
        require_sweep = self._cfg_bool("require_liquidity_sweep", False, cfg)
        allow_short = self._cfg_bool("allow_short", False, cfg)
        exit_on_opposite = self._cfg_bool("exit_on_opposite_structure", True, cfg)
        last_side = str(row.get("smc_last_event_side", ""))
        last_type = str(row.get("smc_last_event_type", ""))
        raw_last_bars = row.get("smc_last_event_bars", 10_000_000)
        last_bars = 10_000_000 if pd.isna(raw_last_bars) else int(raw_last_bars)
        swing_trend = int(row.get("smc_swing_trend", 0) or 0)
        volume_ok = bool(row.get("smc_volume_ok", True))
        bull_score = float(row.get("smc_bull_confluence_score", 0.0) or 0.0)
        bear_score = float(row.get("smc_bear_confluence_score", 0.0) or 0.0)

        fresh_bull = last_side == "bullish" and last_bars <= event_window
        fresh_bear = last_side == "bearish" and last_bars <= event_window
        long_bias_ok = (not require_swing_bias) or swing_trend >= 0
        short_bias_ok = (not require_swing_bias) or swing_trend <= 0
        long_sweep_ok = (not require_sweep) or bool(row.get("smc_bull_sweep", False))
        short_sweep_ok = (not require_sweep) or bool(row.get("smc_bear_sweep", False))
        long_score_ok = bull_score >= min_score
        short_score_ok = bear_score >= min_score

        long_ok = fresh_bull and long_bias_ok and long_score_ok and long_sweep_ok and volume_ok and atr > 0.0
        short_ok = fresh_bear and short_bias_ok and short_score_ok and short_sweep_ok and volume_ok and atr > 0.0

        checklist = [
            {"label": "Structure", "value": f"{last_side or 'none'} {last_type or ''} {last_bars}b", "ok": fresh_bull or fresh_bear},
            {"label": "Swing Bias", "value": str(row.get("smc_bias", "NEUTRAL")), "ok": long_bias_ok if fresh_bull else short_bias_ok if fresh_bear else swing_trend != 0},
            {"label": "Bull Score", "value": f"{bull_score:.1f}", "ok": long_score_ok, "hint": f">={min_score:.1f}"},
            {"label": "Bear Score", "value": f"{bear_score:.1f}", "ok": short_score_ok, "hint": f">={min_score:.1f}"},
            {"label": "Sweep", "value": "bull" if row.get("smc_bull_sweep", False) else "bear" if row.get("smc_bear_sweep", False) else "no", "ok": (long_sweep_ok if fresh_bull else short_sweep_ok)},
            {"label": "Volume", "value": f"{float(row.get('smc_volume_ratio', 0.0) or 0.0):.2f}x", "ok": volume_ok},
            {"label": "Price Zone", "value": str(row.get("smc_price_zone", "NEUTRAL")), "ok": True},
        ]
        base_kwargs = self._base_signal_kwargs(row, checklist, cfg)
        base_kwargs["timeframe"] = ctx.timeframe_primary

        position_side = str(ctx.position_side or "LONG").upper()
        if ctx.has_position:
            if ctx.sl_confirmed:
                if position_side == "SHORT":
                    return close_short(
                        "SMC short SL confirmed",
                        confidence=0.9,
                        **base_kwargs,
                    )
                return sell(
                    "SMC SL confirmed",
                    confidence=0.9,
                    **base_kwargs,
                )
            if exit_on_opposite and position_side == "SHORT" and fresh_bull:
                return close_short(
                    f"SMC bullish {last_type} against short",
                    confidence=0.68,
                    **base_kwargs,
                )
            if exit_on_opposite and position_side != "SHORT" and fresh_bear:
                return sell(
                    f"SMC bearish {last_type} against long",
                    confidence=0.68,
                    **base_kwargs,
                )
            return hold(
                "SMC position riding",
                confidence=0.48,
                status_summary=f"SMC hold {row.get('smc_bias', 'NEUTRAL')}",
                **base_kwargs,
            )

        if long_ok:
            confidence = min(0.82, 0.56 + (bull_score * 0.05) + (0.04 if last_type == "CHOCH" else 0.0))
            return buy(
                f"SMC bullish {last_type} with confluence",
                confidence=confidence,
                status_summary=f"SMC BUY {last_type} score {bull_score:.1f}",
                **self._stop_hints(row, "long", cfg),
                **base_kwargs,
            )

        if allow_short and short_ok:
            confidence = min(0.82, 0.56 + (bear_score * 0.05) + (0.04 if last_type == "CHOCH" else 0.0))
            return open_short(
                f"SMC bearish {last_type} with confluence",
                confidence=confidence,
                status_summary=f"SMC SHORT {last_type} score {bear_score:.1f}",
                **self._stop_hints(row, "short", cfg),
                **base_kwargs,
            )

        reason = "Waiting for SMC structure"
        if fresh_bear and not allow_short:
            reason = "Bearish SMC setup ignored while shorts disabled"
        elif fresh_bull and not long_score_ok:
            reason = "SMC bullish structure lacks confluence"
        elif fresh_bear and not short_score_ok:
            reason = "SMC bearish structure lacks confluence"
        return hold(
            reason,
            confidence=0.34,
            status_summary=f"SMC wait {row.get('smc_bias', 'NEUTRAL')}",
            **base_kwargs,
        )
