"""BBKC squeeze breakout strategy plugin.

Long-only spot strategy for low-volatility accumulation / range regimes:
enter on Bollinger-inside-Keltner squeeze release with bullish momentum.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from xauby.strategies.base import Strategy
from xauby.strategies.bbkc_squeeze.indicators import compute_indicators
from xauby.strategies.context import MarketContext
from xauby.strategies.registry import register
from xauby.strategies.signal import Signal, buy, hold, sell


@register("bbkc_squeeze")
class BBKCSqueezeStrategy(Strategy):
    version = "0.1.0"
    author = "xAuby"
    description = "Bollinger + Keltner squeeze breakout strategy (long-only spot)."
    tags = ["squeeze", "breakout", "bb", "keltner", "1h", "low-vol"]
    required_timeframes = ["1h"]
    min_bars = 60

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        return {
            "bb_length": 20,
            "bb_std": 2.0,
            "kc_length": 20,
            "kc_mult": 1.5,
            "squeeze_length": 20,
            "rsi_period": 14,
            "rsi_min": 40.0,
            "rsi_max": 75.0,
            "atr_period": 14,
            "volume_ma_period": 20,
            "vol_min_ratio": 0.8,
            "momentum_min": 0.0,
            "entry_on_release_only": True,
            "release_window": 3,
            "sl_atr_mult": 2.0,
            "trailing_atr_mult": 1.5,
            "breakeven_sl_enabled": True,
            "breakeven_activation_atr_mult": 1.0,
            "breakeven_buffer_atr_mult": 0.05,
            "exit_on_momentum_loss": True,
            "exit_on_squeeze_reentry": True,
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
        if self._cfg_float("rsi_min", 40.0) >= self._cfg_float("rsi_max", 75.0):
            warnings.append("rsi_min must be less than rsi_max")
        if self._cfg_float("bb_std", 2.0) <= 0:
            warnings.append("bb_std must be > 0")
        if self._cfg_float("kc_mult", 1.5) <= 0:
            warnings.append("kc_mult must be > 0")
        return warnings

    def _build_checklist(
        self,
        indicators: Dict[str, Any],
        entry_ok: bool,
        release_ok: bool,
        vol_ok: bool,
        *,
        rsi_min: float,
        rsi_max: float,
    ) -> List[Dict[str, Any]]:
        rsi = float(indicators.get("rsi", 0.0))
        return [
            {
                "label": "Squeeze",
                "value": indicators.get("zone", "NEUTRAL"),
                "ok": release_ok,
                "hint": "Need squeeze release / fired",
            },
            {
                "label": "Momentum",
                "value": f"{float(indicators.get('momentum', 0.0)):.2f}",
                "ok": float(indicators.get("momentum", 0.0)) > 0,
                "hint": ">= 0 bullish",
            },
            {
                "label": "RSI",
                "value": f"{rsi:.1f}",
                "ok": rsi_min <= rsi <= rsi_max,
                "hint": f"{rsi_min:.0f}-{rsi_max:.0f}",
            },
            {
                "label": "Volume",
                "value": f"{float(indicators.get('volume_ratio', 0.0)):.2f}x",
                "ok": vol_ok,
            },
            {
                "label": "Entry Gate",
                "value": "Ready" if entry_ok else "Wait",
                "ok": entry_ok,
            },
        ]

    def analyze(self, ctx: MarketContext) -> Signal:
        runtime_cfg = {**self.config, **(ctx.config or {})}
        rsi_min = self._cfg_float("rsi_min", 40.0, runtime_cfg)
        rsi_max = self._cfg_float("rsi_max", 75.0, runtime_cfg)
        vol_min_ratio = self._cfg_float("vol_min_ratio", 0.8, runtime_cfg)
        momentum_min = self._cfg_float("momentum_min", 0.0, runtime_cfg)
        entry_on_release_only = self._cfg_bool("entry_on_release_only", True, runtime_cfg)
        release_window = self._cfg_int("release_window", 3, runtime_cfg)
        runtime_cfg["release_window"] = release_window
        sl_mult = self._cfg_float("sl_atr_mult", 2.0, runtime_cfg)
        trail_mult = self._cfg_float("trailing_atr_mult", 1.5, runtime_cfg)
        exit_on_momentum_loss = self._cfg_bool("exit_on_momentum_loss", True, runtime_cfg)
        exit_on_squeeze_reentry = self._cfg_bool("exit_on_squeeze_reentry", True, runtime_cfg)

        df = ctx.df_primary
        if df.empty or len(df) < self.min_bars:
            return hold(
                "Need more BBKC squeeze candles",
                strategy_name=self.name,
                timeframe=ctx.timeframe_primary,
            )

        indicators = compute_indicators(df, runtime_cfg)
        atr = float(indicators.get("atr", 0.0))
        momentum = float(indicators.get("momentum", 0.0))
        rsi = float(indicators.get("rsi", 50.0))
        vol_ratio = float(indicators.get("volume_ratio", 0.0))
        close = float(indicators.get("close", 0.0))
        bb_mid = float(indicators.get("bb_mid", 0.0))

        rsi_ok = rsi_min <= rsi <= rsi_max
        vol_ok = vol_min_ratio <= 0.0 or vol_ratio >= vol_min_ratio
        momentum_ok = momentum > momentum_min
        trend_ok = close > bb_mid if bb_mid > 0 else True

        if entry_on_release_only:
            release_ok = bool(indicators.get("squeeze_release_recent"))
        else:
            release_ok = bool(indicators.get("squeeze_fired"))

        entry_ok = release_ok and momentum_ok and trend_ok and rsi_ok and vol_ok and atr > 0
        checklist = self._build_checklist(
            indicators,
            entry_ok,
            release_ok,
            vol_ok,
            rsi_min=rsi_min,
            rsi_max=rsi_max,
        )
        common: Dict[str, Any] = {
            "indicators": indicators,
            "volatility": atr,
            "checklist": checklist,
            "status_summary": (
                f"Squeeze {indicators.get('zone', 'NEUTRAL')} | "
                f"Mom {momentum:.2f} | RSI {rsi:.1f} | Vol {vol_ratio:.2f}x"
            ),
            "strategy_name": self.name,
            "timeframe": ctx.timeframe_primary,
        }

        if ctx.has_position:
            if ctx.sl_confirmed:
                return sell("BBKC squeeze SL confirmed", confidence=0.9, **common)
            if exit_on_momentum_loss and momentum <= 0:
                return sell("BBKC squeeze momentum turned bearish", confidence=0.72, **common)
            if exit_on_squeeze_reentry and indicators.get("squeeze_on"):
                return sell("BBKC squeeze re-compressed", confidence=0.68, **common)
            return hold(
                "BBKC squeeze position managed by ATR trailing stop",
                confidence=0.5,
                trail_distance=atr * trail_mult if atr > 0 else None,
                **common,
            )

        if entry_ok:
            return buy(
                "BBKC squeeze bullish breakout",
                confidence=0.66,
                stop_loss_distance=atr * sl_mult,
                trail_distance=atr * trail_mult,
                **common,
            )

        if not indicators.get("squeeze_fired") and not indicators.get("squeeze_on"):
            reason = "Waiting for BBKC squeeze setup"
        elif indicators.get("squeeze_on"):
            reason = "Squeeze on — waiting for release"
        elif not momentum_ok:
            reason = "Squeeze fired but momentum not bullish"
        elif not rsi_ok:
            reason = f"RSI {rsi:.1f} out of bounds [{rsi_min}, {rsi_max}]"
        elif not vol_ok:
            reason = f"Volume ratio {vol_ratio:.2f}x below {vol_min_ratio:.2f}x"
        else:
            reason = "Waiting for fresh BBKC squeeze release"

        return hold(reason, confidence=0.35, **common)
