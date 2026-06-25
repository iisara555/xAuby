"""BB + RSI mean reversion strategy plugin.

Long-only spot strategy for sideways/choppy regimes: buy oversold touches of
the lower Bollinger band with RSI confirmation, exit toward the mean.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from xauby.strategies.base import Strategy
from xauby.strategies.bbrsi_mean_reversion.indicators import compute_indicators
from xauby.strategies.context import MarketContext
from xauby.strategies.registry import register
from xauby.strategies.signal import Signal, buy, hold, sell


@register("bbrsi_mean_reversion")
class BBRSIMeanReversionStrategy(Strategy):
    version = "0.1.0"
    author = "xAuby"
    description = "Bollinger Band + RSI mean reversion strategy (long-only spot)."
    tags = ["mean-reversion", "bb", "rsi", "1h", "sideways"]
    required_timeframes = ["1h"]
    min_bars = 50

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        return {
            "bb_length": 20,
            "bb_std": 2.0,
            "rsi_period": 14,
            "rsi_oversold": 30.0,
            "rsi_overbought": 70.0,
            "rsi_exit": 55.0,
            "atr_period": 14,
            "volume_ma_period": 20,
            "vol_min_ratio": 0.5,
            "require_below_lower_band": True,
            "sl_atr_mult": 1.8,
            "trailing_atr_mult": 1.2,
            "fixed_tp_pct": 1.2,
            "breakeven_sl_enabled": True,
            "breakeven_activation_atr_mult": 0.8,
            "breakeven_buffer_atr_mult": 0.05,
            "exit_at_mid_band": True,
            "exit_at_rsi_target": True,
            "exit_at_upper_band": True,
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
        if self._cfg_float("rsi_oversold", 30.0) >= self._cfg_float("rsi_overbought", 70.0):
            warnings.append("rsi_oversold must be less than rsi_overbought")
        return warnings

    def _build_checklist(
        self,
        indicators: Dict[str, Any],
        *,
        rsi_oversold: float,
        rsi_overbought: float,
        entry_ok: bool,
        vol_ok: bool,
    ) -> List[Dict[str, Any]]:
        rsi = float(indicators.get("rsi", 50.0))
        return [
            {
                "label": "Zone",
                "value": indicators.get("zone", "NEUTRAL"),
                "ok": indicators.get("zone") == "OVERSOLD",
            },
            {
                "label": "BB Lower",
                "value": f"{float(indicators.get('close', 0.0)):.2f} vs {float(indicators.get('bb_lower', 0.0)):.2f}",
                "ok": bool(indicators.get("below_lower_band")),
            },
            {
                "label": "RSI",
                "value": f"{rsi:.1f}",
                "ok": rsi <= rsi_oversold,
                "hint": f"<= {rsi_oversold:.0f}",
                "bar": {
                    "type": "range",
                    "min": 0.0,
                    "max": 100.0,
                    "low": 0.0,
                    "high": rsi_oversold,
                    "value": rsi,
                },
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
        rsi_oversold = self._cfg_float("rsi_oversold", 30.0, runtime_cfg)
        rsi_overbought = self._cfg_float("rsi_overbought", 70.0, runtime_cfg)
        rsi_exit = self._cfg_float("rsi_exit", 55.0, runtime_cfg)
        vol_min_ratio = self._cfg_float("vol_min_ratio", 0.5, runtime_cfg)
        require_below_lower = self._cfg_bool("require_below_lower_band", True, runtime_cfg)
        sl_mult = self._cfg_float("sl_atr_mult", 1.8, runtime_cfg)
        trail_mult = self._cfg_float("trailing_atr_mult", 1.2, runtime_cfg)
        exit_at_mid = self._cfg_bool("exit_at_mid_band", True, runtime_cfg)
        exit_at_rsi_target = self._cfg_bool("exit_at_rsi_target", True, runtime_cfg)
        exit_at_upper = self._cfg_bool("exit_at_upper_band", True, runtime_cfg)

        df = ctx.df_primary
        if df.empty or len(df) < self.min_bars:
            return hold(
                "Need more BBRSI mean reversion candles",
                strategy_name=self.name,
                timeframe=ctx.timeframe_primary,
            )

        indicators = compute_indicators(df, runtime_cfg)
        atr = float(indicators.get("atr", 0.0))
        rsi = float(indicators.get("rsi", 50.0))
        vol_ratio = float(indicators.get("volume_ratio", 0.0))
        vol_ok = vol_min_ratio <= 0.0 or vol_ratio >= vol_min_ratio

        band_ok = bool(indicators.get("below_lower_band")) if require_below_lower else rsi <= rsi_oversold
        rsi_ok = rsi <= rsi_oversold
        entry_ok = band_ok and rsi_ok and vol_ok and atr > 0

        checklist = self._build_checklist(
            indicators,
            rsi_oversold=rsi_oversold,
            rsi_overbought=rsi_overbought,
            entry_ok=entry_ok,
            vol_ok=vol_ok,
        )
        common: Dict[str, Any] = {
            "indicators": indicators,
            "volatility": atr,
            "checklist": checklist,
            "status_summary": (
                f"Zone {indicators.get('zone', 'NEUTRAL')} | "
                f"RSI {rsi:.1f} | BB% {float(indicators.get('bb_pct', 0.5)):.2f} | "
                f"Vol {vol_ratio:.2f}x"
            ),
            "strategy_name": self.name,
            "timeframe": ctx.timeframe_primary,
        }

        if ctx.has_position:
            if ctx.sl_confirmed:
                return sell("BBRSI mean reversion SL confirmed", confidence=0.9, **common)
            if exit_at_upper and indicators.get("above_upper_band"):
                return sell("BBRSI price reached upper band", confidence=0.85, **common)
            if exit_at_mid and indicators.get("above_mid_band"):
                return sell("BBRSI reverted to mid band", confidence=0.75, **common)
            if exit_at_rsi_target and rsi >= rsi_exit:
                return sell(f"BBRSI RSI reached exit target {rsi_exit:.0f}", confidence=0.7, **common)
            if rsi >= rsi_overbought:
                return sell("BBRSI RSI overbought", confidence=0.72, **common)
            return hold(
                "BBRSI mean reversion position open",
                confidence=0.5,
                trail_distance=atr * trail_mult if atr > 0 else None,
                **common,
            )

        if entry_ok:
            return buy(
                "BBRSI oversold mean reversion entry",
                confidence=0.64,
                stop_loss_distance=atr * sl_mult,
                trail_distance=atr * trail_mult,
                **common,
            )

        if not rsi_ok:
            reason = f"RSI {rsi:.1f} not oversold (<= {rsi_oversold:.0f})"
        elif require_below_lower and not indicators.get("below_lower_band"):
            reason = "Price not at/below lower Bollinger band"
        elif not vol_ok:
            reason = f"Volume ratio {vol_ratio:.2f}x below {vol_min_ratio:.2f}x"
        else:
            reason = "Waiting for BBRSI oversold setup"

        return hold(reason, confidence=0.35, **common)
