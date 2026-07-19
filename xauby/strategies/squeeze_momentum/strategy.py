"""Squeeze Momentum (LazyBear) strategy plugin.

Trades the LazyBear Squeeze Momentum histogram on its momentum line ``val``:

* ``entry_trigger = "zero_cross"`` — enter long when momentum crosses up through
  zero, short when it crosses down (mirror). This is the common mechanical read
  of the indicator.
* ``entry_trigger = "squeeze_release"`` — wait for a squeeze to fire (BB pops
  back outside KC) and enter in the direction of momentum at the release.

Exits: momentum zero-cross back the other way (default), optional momentum-fade,
plus the engine's ATR stop / trailing / breakeven. Short side is config-gated by
``enable_short`` (default off) so nothing changes for long-only pairs.

Pure analyst: no orders, DB, or engine calls. Long+short via buy/sell and
open_short/close_short.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from xauby.strategies.base import Strategy
from xauby.strategies.context import MarketContext
from xauby.strategies.registry import register
from xauby.strategies.signal import Signal, buy, close_short, hold, open_short, sell
from xauby.strategies.squeeze_momentum.indicators import squeeze_snapshot


@register("squeeze_momentum")
class SqueezeMomentumStrategy(Strategy):
    version = "0.1.0"
    author = "xAuby"
    display_name = "Squeeze Momentum"
    description = "LazyBear Squeeze Momentum (BB/KC squeeze + linreg momentum), long+short."
    tags = ["squeeze", "momentum", "lazybear", "bb", "kc", "4h"]
    required_timeframes = ["4h"]
    min_bars = 120

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        return {
            "bb_length": 20,
            "bb_mult": 2.0,
            "kc_length": 20,
            "kc_mult": 1.5,
            "use_true_range": True,
            "entry_trigger": "zero_cross",   # or "squeeze_release"
            "min_momentum": 0.0,             # require |val| >= this at entry
            "exit_on_zero_cross": True,
            "exit_on_momentum_fade": False,
            "atr_period": 14,
            "sl_atr_mult": 2.5,
            "trailing_atr_mult": 2.0,
            "fixed_tp_pct": 0.0,
            "breakeven_sl_enabled": True,
            "breakeven_activation_atr_mult": 1.2,
            "breakeven_buffer_atr_mult": 0.05,
            "enable_short": False,
        }

    def _cfg_int(self, key: str, default: int, cfg: Dict[str, Any]) -> int:
        try:
            return max(1, int(cfg.get(key, default)))
        except (TypeError, ValueError):
            return default

    def _cfg_float(self, key: str, default: float, cfg: Dict[str, Any]) -> float:
        try:
            return float(cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    def _cfg_bool(self, key: str, default: bool, cfg: Dict[str, Any]) -> bool:
        value = cfg.get(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def validate_config(self) -> List[str]:
        warns: List[str] = []
        if self._cfg_int("kc_length", 20, self.config) < 5:
            warns.append("kc_length should usually be >= 5")
        if self.config.get("entry_trigger") not in (None, "zero_cross", "squeeze_release"):
            warns.append("entry_trigger must be 'zero_cross' or 'squeeze_release'")
        return warns

    def analyze(self, ctx: MarketContext) -> Signal:
        cfg = {**self.config, **(ctx.config or {})}
        df = ctx.df_primary
        if df is None or df.empty or len(df) < self.min_bars:
            return hold("Need more Squeeze Momentum candles", strategy_name=self.name,
                        timeframe=ctx.timeframe_primary)

        snap = squeeze_snapshot(df, cfg)
        mom = snap["mom"]
        mom_prev = snap["mom_prev"]
        atr = snap["atr"]
        sqz_state = snap["sqz_state"]
        released = (not snap["sqz_on"]) and snap["prev_sqz_on"]

        trigger = str(cfg.get("entry_trigger", "zero_cross"))
        min_mom = self._cfg_float("min_momentum", 0.0, cfg)
        sl_mult = self._cfg_float("sl_atr_mult", 2.5, cfg)
        trail_mult = self._cfg_float("trailing_atr_mult", 2.0, cfg)
        exit_zc = self._cfg_bool("exit_on_zero_cross", True, cfg)
        exit_fade = self._cfg_bool("exit_on_momentum_fade", False, cfg)
        enable_short = self._cfg_bool("enable_short", False, cfg)

        zero_up = mom_prev <= 0.0 and mom > 0.0
        zero_down = mom_prev >= 0.0 and mom < 0.0
        rising = mom > mom_prev
        strong = abs(mom) >= min_mom

        indicators = {
            "mom": mom,
            "mom_prev": mom_prev,
            "sqz_state": sqz_state,
            "mom_zone": snap["mom_zone"],
            "atr": atr,
        }
        checklist = [
            {"label": "Squeeze", "value": sqz_state,
             "ok": sqz_state != "ON", "hint": "ON=coiled, OFF=fired"},
            {"label": "Momentum", "value": f"{mom:+.2f}", "ok": mom > 0,
             "hint": "green>0"},
            {"label": "Trend", "value": "rising" if rising else "falling", "ok": rising},
        ]
        common: Dict[str, Any] = {
            "indicators": indicators,
            "volatility": atr,
            "checklist": checklist,
            "status_summary": f"SQZ {sqz_state} | mom {mom:+.2f} {snap['mom_zone']}",
            "strategy_name": self.name,
            "timeframe": ctx.timeframe_primary,
        }

        # -------- position management --------
        if ctx.has_position and str(ctx.position_side or "").upper() == "SHORT":
            if ctx.sl_confirmed:
                return close_short("Squeeze short Stop loss confirmed", confidence=0.9, **common)
            if exit_zc and mom > 0.0:
                return close_short("Squeeze short momentum flipped positive", confidence=0.72, **common)
            if exit_fade and mom < 0.0 and rising:
                return close_short("Squeeze short momentum fading", confidence=0.6, **common)
            return hold("Squeeze momentum short open", confidence=0.5,
                        trail_distance=atr * trail_mult if atr > 0 else None, **common)

        if ctx.has_position:
            if ctx.sl_confirmed:
                return sell("Squeeze Momentum SL confirmed", confidence=0.9, **common)
            if exit_zc and mom < 0.0:
                return sell("Squeeze momentum flipped negative", confidence=0.72, **common)
            if exit_fade and mom > 0.0 and not rising:
                return sell("Squeeze momentum fading", confidence=0.6, **common)
            return hold("Squeeze momentum long open", confidence=0.5,
                        trail_distance=atr * trail_mult if atr > 0 else None, **common)

        # -------- entries (flat) --------
        if atr <= 0:
            return hold("Squeeze momentum waiting (no ATR)", confidence=0.3, **common)

        if trigger == "squeeze_release":
            long_ok = released and mom > 0.0 and strong
            short_ok = released and mom < 0.0 and strong
        else:  # zero_cross
            long_ok = zero_up and strong
            short_ok = zero_down and strong

        if long_ok:
            return buy("Squeeze momentum long entry", confidence=0.64,
                       stop_loss_distance=atr * sl_mult, trail_distance=atr * trail_mult, **common)
        if enable_short and short_ok:
            return open_short("Squeeze momentum short entry", confidence=0.64,
                              stop_loss_distance=atr * sl_mult, trail_distance=atr * trail_mult, **common)

        return hold(f"Waiting for squeeze momentum {trigger}", confidence=0.35, **common)
