"""CDC Action Zone strategy, rewritten as a plugin.

The strategy is a self-contained plugin: it owns its indicator math and its
entry/exit rules. The engine knows nothing about EMAs or zones — it only
sees the :class:`~xauby.strategies.signal.Signal` returned by ``analyze``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from xauby.strategies.base import Strategy
from xauby.strategies.context import MarketContext
from xauby.strategies.registry import register
from xauby.strategies.signal import Signal, buy, close_short, hold, open_short, sell
from xauby.strategies.cdc_action_zone.indicators import (
    classify_zone,  # re-exported for backward compatibility
    compute_indicators,
)

__all__ = ["CDCActionZoneStrategy", "classify_zone"]


@register("cdc_action_zone")
class CDCActionZoneStrategy(Strategy):
    """CDC Action Zone trend-following strategy on the primary timeframe.

    Reads its tuning from the ``strategies.cdc_action_zone`` config block:
        rsi_min, rsi_max, vol_min_ratio,
        use_d1_regime_filter, require_fresh_zone
    """

    version = "1.0.0"
    author = "xAuby"
    description = (
        "CDC Action Zone trend-following strategy using EMA 12/26 crossover "
        "zones on the 4H timeframe with optional D1 regime filter."
    )
    tags = ["trend", "swing", "4h", "ema"]
    required_timeframes = ["4h", "1d"]
    min_bars = 100

    # ------------------------------------------------------------------ #
    # Config contract                                                    #
    # ------------------------------------------------------------------ #
    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        return {
            "rsi_min": 45.0,
            "rsi_max": 70.0,
            "vol_min_ratio": 1.0,
            "use_d1_regime_filter": False,
            "require_fresh_zone": True,
            "fresh_zone_window": 3,
            "ap_smoothing": 2,
            "exit_on_bear_cross": False,
            # CDC stop-and-reverse: when true, a fresh RED zone opens a SHORT
            # (mirror of the GREEN long entry) and a GREEN zone covers it.
            # Default False keeps the classic long-only behaviour.
            "enable_short": False,
            "trailing_atr_mult": 1.5,
            "sl_atr_mult": 2.0,
            "breakeven_sl_enabled": False,
            "breakeven_activation_atr_mult": 1.5,
            "breakeven_buffer_atr_mult": 0.1,
        }

    def validate_config(self) -> List[str]:
        warnings: List[str] = []
        rsi_min = self.config.get("rsi_min", 45.0)
        rsi_max = self.config.get("rsi_max", 70.0)
        try:
            if float(rsi_min) >= float(rsi_max):
                warnings.append(
                    f"rsi_min ({rsi_min}) must be less than rsi_max ({rsi_max})"
                )
        except (TypeError, ValueError):
            warnings.append("rsi_min / rsi_max must be numeric")

        vol = self.config.get("vol_min_ratio", 1.0)
        try:
            if float(vol) < 0:
                warnings.append(f"vol_min_ratio ({vol}) must be >= 0")
        except (TypeError, ValueError):
            warnings.append("vol_min_ratio must be numeric")

        return warnings

    def _config_schema(self) -> Optional[Dict[str, Any]]:
        return {
            "rsi_min": {"type": "float", "default": 45.0, "description": "Minimum RSI for entry"},
            "rsi_max": {"type": "float", "default": 70.0, "description": "Maximum RSI for entry"},
            "vol_min_ratio": {"type": "float", "default": 1.0, "description": "Minimum volume ratio vs 20-SMA"},
            "use_d1_regime_filter": {"type": "bool", "default": False, "description": "Require D1 regime confirmation"},
            "require_fresh_zone": {"type": "bool", "default": True, "description": "Require fresh GREEN transition"},
            "fresh_zone_window": {"type": "int", "default": 3, "description": "Max bars since GREEN crossover still counted as fresh (2-3 recommended)"},
            "ap_smoothing": {"type": "int", "default": 2, "description": "EMA source smoothing for CDC V3 (AP=ema(close,n)); 1 = raw close, 2 = Piriya V3 original"},
            "exit_on_bear_cross": {"type": "bool", "default": False, "description": "Sell on bearish EMA cross (TV behaviour) instead of waiting for RED zone"},
            "enable_short": {"type": "bool", "default": False, "description": "Stop-and-reverse: open SHORT on fresh RED, cover on GREEN (TradingView CDC long+short)"},
            "trailing_atr_mult": {"type": "float", "default": 1.5, "description": "ATR multiplier for trailing stop"},
            "sl_atr_mult": {"type": "float", "default": 2.0, "description": "ATR multiplier for initial stop loss"},
            "breakeven_sl_enabled": {"type": "bool", "default": False, "description": "Enable breakeven stop loss"},
            "breakeven_activation_atr_mult": {"type": "float", "default": 1.5, "description": "ATR mult to activate breakeven SL"},
            "breakeven_buffer_atr_mult": {"type": "float", "default": 0.1, "description": "ATR mult buffer above entry for breakeven SL"},
        }

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #
    def _cfg_float(self, key: str, default: float) -> float:
        try:
            return float(self.config.get(key, default))
        except (TypeError, ValueError):
            return default

    def _cfg_bool(self, key: str, default: bool) -> bool:
        v = self.config.get(key, default)
        if isinstance(v, bool):
            return v
        return str(v).lower() in ("true", "1", "yes", "on")

    def _cfg_int(self, key: str, default: int) -> int:
        try:
            return max(1, int(self.config.get(key, default)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _ema_cross_check(
        ema_fast: float,
        ema_slow: float,
        ema_fast_prev: float,
        ema_slow_prev: float,
        green_streak: int = 1,
        fresh_zone_window: int = 0,
    ) -> tuple[bool, str]:
        """Bullish EMA cross: prev bar fast <= slow, current bar fast > slow.

        On the crossover bar (green_streak == 1) the prev<= rule is enforced.
        Later bars inside fresh_zone_window only require the current bull state.
        fresh_zone_window == 0 skips the prev<= rule (current bull only).
        """
        if ema_fast <= 0 or ema_slow <= 0:
            return False, "EMA values unavailable"
        if ema_fast <= ema_slow:
            return False, f"curr EMA12 {ema_fast:.2f} <= EMA26 {ema_slow:.2f}"
        if fresh_zone_window == 0:
            return True, "bull (fresh zone disabled)"
        if ema_fast_prev <= 0 or ema_slow_prev <= 0:
            return True, "bull (no prev EMA data)"
        prev_not_bull = ema_fast_prev <= ema_slow_prev
        if green_streak > 1 and green_streak <= fresh_zone_window:
            return True, "bull within fresh window"
        if not prev_not_bull:
            return False, (
                f"prev EMA12 {ema_fast_prev:.2f} > EMA26 {ema_slow_prev:.2f} "
                f"(need prev <= on cross bar)"
            )
        return True, "fresh bull cross (prev <=, curr >)"

    @staticmethod
    def _ema_cross_check_bear(
        ema_fast: float,
        ema_slow: float,
        ema_fast_prev: float,
        ema_slow_prev: float,
        red_streak: int = 1,
        fresh_zone_window: int = 0,
    ) -> tuple[bool, str]:
        """Bearish EMA cross: prev fast >= slow, current fast < slow.

        Mirror of :meth:`_ema_cross_check` for SHORT entries. On the crossover
        bar (red_streak == 1) the prev>= rule is enforced; later bars inside
        fresh_zone_window only require the current bear state.
        """
        if ema_fast <= 0 or ema_slow <= 0:
            return False, "EMA values unavailable"
        if ema_fast >= ema_slow:
            return False, f"curr EMA12 {ema_fast:.2f} >= EMA26 {ema_slow:.2f}"
        if fresh_zone_window == 0:
            return True, "bear (fresh zone disabled)"
        if ema_fast_prev <= 0 or ema_slow_prev <= 0:
            return True, "bear (no prev EMA data)"
        prev_not_bear = ema_fast_prev >= ema_slow_prev
        if red_streak > 1 and red_streak <= fresh_zone_window:
            return True, "bear within fresh window"
        if not prev_not_bear:
            return False, (
                f"prev EMA12 {ema_fast_prev:.2f} < EMA26 {ema_slow_prev:.2f} "
                f"(need prev >= on cross bar)"
            )
        return True, "fresh bear cross (prev >=, curr <)"

    @staticmethod
    def _ema_cross_display(
        ema_fast: float,
        ema_slow: float,
        ema_fast_prev: float,
        ema_slow_prev: float,
        green_streak: int,
        fresh_zone_window: int,
        require_fresh_zone: bool,
    ) -> tuple[bool, str]:
        """Checklist row for fresh bullish EMA cross (separate from bull alignment)."""
        ok, _ = CDCActionZoneStrategy._ema_cross_check(
            ema_fast,
            ema_slow,
            ema_fast_prev,
            ema_slow_prev,
            green_streak=green_streak,
            fresh_zone_window=fresh_zone_window if require_fresh_zone else 0,
        )
        if ema_fast <= 0 or ema_slow <= 0:
            return False, "N/A"
        if ema_fast <= ema_slow:
            return False, "No cross"
        if (
            require_fresh_zone
            and fresh_zone_window > 1
            and green_streak > 1
            and green_streak <= fresh_zone_window
        ):
            return True, f"Win {green_streak}/{fresh_zone_window}"
        if ema_fast_prev > 0 and ema_slow_prev > 0 and ema_fast_prev <= ema_slow_prev:
            return ok, "Fresh cross"
        return False, "No cross"

    @staticmethod
    def _ema_cross_display_bear(
        ema_fast: float,
        ema_slow: float,
        ema_fast_prev: float,
        ema_slow_prev: float,
        red_streak: int,
        fresh_zone_window: int,
        require_fresh_zone: bool,
    ) -> tuple[bool, str]:
        """Checklist row for a fresh bearish EMA cross."""
        ok, _ = CDCActionZoneStrategy._ema_cross_check_bear(
            ema_fast,
            ema_slow,
            ema_fast_prev,
            ema_slow_prev,
            red_streak=red_streak,
            fresh_zone_window=fresh_zone_window if require_fresh_zone else 0,
        )
        if ema_fast <= 0 or ema_slow <= 0:
            return False, "N/A"
        if ema_fast >= ema_slow:
            return False, "No cross"
        if (
            require_fresh_zone
            and fresh_zone_window > 1
            and red_streak > 1
            and red_streak <= fresh_zone_window
        ):
            return True, f"Win {red_streak}/{fresh_zone_window}"
        if ema_fast_prev > 0 and ema_slow_prev > 0 and ema_fast_prev >= ema_slow_prev:
            return ok, "Fresh cross"
        return False, "No cross"

    # ------------------------------------------------------------------ #
    # Main hook                                                          #
    # ------------------------------------------------------------------ #
    def analyze(self, ctx: MarketContext) -> Signal:
        runtime_cfg = {**self.config, **(ctx.config or {})}

        def cfg_float(key: str, default: float) -> float:
            try:
                return float(runtime_cfg.get(key, default))
            except (TypeError, ValueError):
                return default

        def cfg_bool(key: str, default: bool) -> bool:
            value = runtime_cfg.get(key, default)
            if isinstance(value, bool):
                return value
            return str(value).lower() in ("true", "1", "yes", "on")

        def cfg_int(key: str, default: int) -> int:
            try:
                return max(1, int(runtime_cfg.get(key, default)))
            except (TypeError, ValueError):
                return default

        rsi_min = cfg_float("rsi_min", 45.0)
        rsi_max = cfg_float("rsi_max", 70.0)
        vol_min_ratio = cfg_float("vol_min_ratio", 1.0)
        use_d1_regime = cfg_bool("use_d1_regime_filter", False)
        require_fresh_zone = cfg_bool("require_fresh_zone", True)
        fresh_zone_window = cfg_int("fresh_zone_window", 3)
        ap_smoothing = cfg_int("ap_smoothing", 2)

        indicators = compute_indicators(
            ctx.df_primary,
            ctx.df_regime,
            ap_smoothing,
            use_d1_regime=use_d1_regime,
            last_bar_is_forming=bool(ctx.extras.get("last_bar_is_forming", True)),
        )
        atr = float(indicators.get("atr_4h", 0.0))
        d1_status = indicators["cdc_zone_d1"] if use_d1_regime else "OFF"

        status = (
            f"D1: {d1_status} | "
            f"4H: {indicators['cdc_zone_4h']} | "
            f"RSI: {indicators['rsi_4h']:.1f} | "
            f"Vol: {indicators['volume_ratio_4h']:.2f}x"
        )

        checklist = self._build_checklist(
            ctx=ctx,
            indicators=indicators,
            rsi_min=rsi_min,
            rsi_max=rsi_max,
            vol_min_ratio=vol_min_ratio,
            use_d1_regime=use_d1_regime,
            require_fresh_zone=require_fresh_zone,
            fresh_zone_window=fresh_zone_window,
        )

        common: Dict[str, Any] = {
            "indicators": indicators,
            "volatility": atr,
            "status_summary": status,
            "checklist": checklist,
        }

        h4_zone = indicators["cdc_zone_4h"]
        green_streak = int(indicators.get("cdc_zone_4h_green_streak", 1))
        red_streak = int(indicators.get("cdc_zone_4h_red_streak", 1))
        rsi = indicators["rsi_4h"]
        vol_ratio = indicators["volume_ratio_4h"]
        ema_fast = float(indicators.get("ema_fast_4h", 0.0))
        ema_slow = float(indicators.get("ema_slow_4h", 0.0))
        ema_fast_prev = float(indicators.get("ema_fast_4h_prev", 0.0))
        ema_slow_prev = float(indicators.get("ema_slow_4h_prev", 0.0))
        enable_short = cfg_bool("enable_short", False)

        def filters_pass() -> Optional[Signal]:
            """Shared RSI / volume gates for both long and short entries."""
            if not (rsi_min <= rsi <= rsi_max):
                return hold(f"RSI {rsi:.1f} out of bounds [{rsi_min}, {rsi_max}]", **common)
            if vol_ratio < vol_min_ratio:
                return hold(f"Volume ratio {vol_ratio:.2f}x below {vol_min_ratio:.2f}x", **common)
            return None

        # ---------------- ENTRY (no position) ----------------
        if not ctx.has_position:
            # ----- LONG on a fresh GREEN zone -----
            if h4_zone == "GREEN":
                if require_fresh_zone and green_streak > fresh_zone_window:
                    return hold(
                        f"4H zone GREEN but not fresh "
                        f"(green for {green_streak} bars > window {fresh_zone_window})",
                        **common,
                    )
                ema_ok, ema_reason = self._ema_cross_check(
                    ema_fast, ema_slow, ema_fast_prev, ema_slow_prev,
                    green_streak=green_streak,
                    fresh_zone_window=fresh_zone_window if require_fresh_zone else 0,
                )
                if not ema_ok:
                    return hold(f"EMA cross check failed: {ema_reason}", **common)
                if use_d1_regime:
                    d1_zone = indicators.get("cdc_zone_d1", "UNKNOWN")
                    if d1_zone not in ("GREEN", "YELLOW", "ORANGE"):
                        return hold(f"Daily regime filter blocked (D1 zone: {d1_zone})", **common)
                blocked = filters_pass()
                if blocked is not None:
                    return blocked
                return buy(
                    reason=(
                        f"Fresh long entry (4H={h4_zone}, streak={green_streak}/{fresh_zone_window}, "
                        f"D1={indicators.get('cdc_zone_d1', 'N/A')}, "
                        f"RSI={rsi:.1f}, Vol={vol_ratio:.2f}x)"
                    ),
                    confidence=0.7,
                    **common,
                )

            # ----- SHORT on a fresh RED zone (CDC stop-and-reverse) -----
            if enable_short and h4_zone == "RED":
                if require_fresh_zone and red_streak > fresh_zone_window:
                    return hold(
                        f"4H zone RED but not fresh "
                        f"(red for {red_streak} bars > window {fresh_zone_window})",
                        **common,
                    )
                ema_ok, ema_reason = self._ema_cross_check_bear(
                    ema_fast, ema_slow, ema_fast_prev, ema_slow_prev,
                    red_streak=red_streak,
                    fresh_zone_window=fresh_zone_window if require_fresh_zone else 0,
                )
                if not ema_ok:
                    return hold(f"Bearish EMA cross check failed: {ema_reason}", **common)
                if use_d1_regime:
                    d1_zone = indicators.get("cdc_zone_d1", "UNKNOWN")
                    if d1_zone not in ("RED", "BLUE", "LBLUE"):
                        return hold(f"Daily regime filter blocked for short (D1 zone: {d1_zone})", **common)
                blocked = filters_pass()
                if blocked is not None:
                    return blocked
                return open_short(
                    reason=(
                        f"Fresh short entry (4H={h4_zone}, streak={red_streak}/{fresh_zone_window}, "
                        f"D1={indicators.get('cdc_zone_d1', 'N/A')}, "
                        f"RSI={rsi:.1f}, Vol={vol_ratio:.2f}x)"
                    ),
                    confidence=0.7,
                    **common,
                )

            return hold(f"4H zone not actionable (current: {h4_zone})", **common)

        # ---------------- EXIT / MANAGE (has position) ----------------
        side = str(ctx.position_side or "LONG").upper()
        price_to_check = ctx.current_price if ctx.current_price > 0 else indicators["close_4h"]

        if side == "SHORT":
            # SL for a short sits ABOVE entry: breach when price rises into it.
            if ctx.stop_loss > 0 and price_to_check >= ctx.stop_loss:
                if not ctx.sl_confirmed:
                    return hold(
                        f"Stop loss breach pending confirmation "
                        f"(price {price_to_check:.4f} >= SL {ctx.stop_loss:.4f})",
                        **common,
                    )
                return close_short(
                    f"Stop loss confirmed (price {price_to_check:.4f} >= SL {ctx.stop_loss:.4f})",
                    confidence=1.0,
                    **common,
                )
            if h4_zone == "GREEN":
                return close_short("4H zone turned GREEN", confidence=0.9, **common)
            if cfg_bool("exit_on_bear_cross", False) and 0 < ema_slow <= ema_fast:
                return close_short(
                    f"Bullish EMA cross (EMA12 {ema_fast:.2f} >= EMA26 {ema_slow:.2f})",
                    confidence=0.85,
                    **common,
                )
            return hold("Holding active short position", **common)

        # ----- LONG position (default / legacy) -----
        if ctx.stop_loss > 0 and price_to_check <= ctx.stop_loss:
            if not ctx.sl_confirmed:
                return hold(
                    f"Stop loss breach pending confirmation "
                    f"(price {price_to_check:.4f} <= SL {ctx.stop_loss:.4f})",
                    **common,
                )
            return sell(
                f"Stop loss confirmed (price {price_to_check:.4f} <= SL {ctx.stop_loss:.4f})",
                confidence=1.0,
                **common,
            )

        if h4_zone == "RED":
            return sell("4H zone turned RED", confidence=0.9, **common)

        # TradingView CDC V3 sells on the bearish cross itself; waiting for the
        # RED zone usually exits later (price passes BLUE/LBLUE first).
        if cfg_bool("exit_on_bear_cross", False) and 0 < ema_fast <= ema_slow:
            return sell(
                f"Bearish EMA cross (EMA12 {ema_fast:.2f} <= EMA26 {ema_slow:.2f})",
                confidence=0.85,
                **common,
            )

        return hold("Holding active position", **common)

    # ------------------------------------------------------------------ #
    # Dashboard checklist (strategy-owned, dashboard-agnostic)           #
    # ------------------------------------------------------------------ #
    def _build_checklist(
        self,
        ctx: MarketContext,
        indicators: Dict[str, Any],
        rsi_min: float,
        rsi_max: float,
        vol_min_ratio: float,
        use_d1_regime: bool,
        require_fresh_zone: bool = True,
        fresh_zone_window: int = 1,
    ) -> List[Dict[str, Any]]:
        h4_zone = indicators.get("cdc_zone_4h", "UNKNOWN")
        rsi = float(indicators.get("rsi_4h", 0.0))
        vol_ratio = float(indicators.get("volume_ratio_4h", 0.0))
        d1_zone = indicators.get("cdc_zone_d1", "UNKNOWN")
        ema_fast = float(indicators.get("ema_fast_4h", 0.0))
        ema_slow = float(indicators.get("ema_slow_4h", 0.0))
        ema_fast_prev = float(indicators.get("ema_fast_4h_prev", 0.0))
        ema_slow_prev = float(indicators.get("ema_slow_4h_prev", 0.0))
        green_streak = int(indicators.get("cdc_zone_4h_green_streak", 0))
        red_streak = int(indicators.get("cdc_zone_4h_red_streak", 0))

        if ctx.has_position:
            price = ctx.current_price if ctx.current_price > 0 else float(
                indicators.get("close_4h", 0.0)
            )
            sl = float(ctx.stop_loss)
            is_short = str(getattr(ctx, "position_side", None) or "LONG").upper() == "SHORT"
            # Short SL sits above entry (breach on a rise); long SL below (breach
            # on a drop). Exit zone is GREEN for shorts, RED for longs.
            sl_hit = sl > 0 and (price >= sl if is_short else price <= sl)
            exit_zone = "GREEN" if is_short else "RED"
            return [
                {
                    "label": "Trailing Stop",
                    "value": f"{price:,.2f} vs SL {sl:,.2f}",
                    "ok": not sl_hit,
                    "hint": "Stop loss hit" if sl_hit else "Safe",
                },
                {
                    "label": "Exit Signal",
                    "value": f"4H Zone: {h4_zone} ({'SHORT' if is_short else 'LONG'})",
                    "ok": h4_zone != exit_zone,
                    "hint": "Cover trigger" if (is_short and h4_zone == "GREEN")
                    else "Sell trigger" if (not is_short and h4_zone == "RED")
                    else "Safe",
                },
            ]

        ema_gap_pct = ((ema_fast - ema_slow) / ema_slow * 100.0) if ema_slow else 0.0
        ctx_config = getattr(ctx, "config", {}) or {}
        enable_short = bool(ctx_config.get("enable_short", False))
        short_bias = enable_short and h4_zone == "RED"
        target_zone = "RED" if short_bias else "GREEN"
        target_streak = red_streak if short_bias else green_streak
        aligned = ema_fast < ema_slow if short_bias else ema_fast > ema_slow
        if short_bias:
            ema_cross_ok, cross_val = self._ema_cross_display_bear(
                ema_fast, ema_slow, ema_fast_prev, ema_slow_prev,
                red_streak=red_streak,
                fresh_zone_window=fresh_zone_window,
                require_fresh_zone=require_fresh_zone,
            )
        else:
            ema_cross_ok, cross_val = self._ema_cross_display(
                ema_fast, ema_slow, ema_fast_prev, ema_slow_prev,
                green_streak=green_streak,
                fresh_zone_window=fresh_zone_window,
                require_fresh_zone=require_fresh_zone,
            )

        in_target_zone = h4_zone == target_zone
        if in_target_zone and 1 <= target_streak <= fresh_zone_window:
            zone_col = f"Fresh {target_streak}/{fresh_zone_window}"
        elif in_target_zone:
            zone_col = (
                f"Fresh {target_streak}/{fresh_zone_window}"
                if target_streak <= fresh_zone_window
                else f"Stale ({target_streak}/{fresh_zone_window})"
            )
        else:
            zone_col = f"Need {target_zone}"

        if not ema_cross_ok:
            if not aligned:
                cross_col = "Need bearish" if short_bias else "Need bullish"
            else:
                cross_col = "Need fresh cross"
        elif (
            require_fresh_zone
            and target_streak > 1
            and target_streak <= fresh_zone_window
        ):
            cross_col = "In fresh window"
        else:
            cross_col = "Cross OK"

        if rsi < rsi_min:
            rsi_zone = "Too low"
        elif rsi > rsi_max:
            rsi_zone = "Too high"
        else:
            rsi_zone = "Neutral"

        items: List[Dict[str, Any]] = [
            {
                "label": "EMA Bearish" if short_bias else "EMA Bullish",
                "value": f"{'Bull' if ema_fast > ema_slow else 'Bear'} ({ema_gap_pct:+.2f}%)",
                "ok": aligned,
                "hint": "EMA12 < EMA26" if short_bias else "EMA12 > EMA26",
                "columns": [
                    ("Bear OK" if aligned else "Bullish") if short_bias
                    else ("Bull OK" if aligned else "Bearish")
                ],
            },
            {
                "label": "EMA Cross",
                "value": cross_val,
                "ok": ema_cross_ok,
                "hint": "prev fast>=slow, curr fast<slow" if short_bias
                else "prev fast<=slow, curr fast>slow",
                "columns": [cross_col],
            },
            {
                "label": "4H Zone",
                "value": h4_zone,
                "ok": in_target_zone,
                "hint": f"Need {target_zone}",
                "columns": [zone_col],
            },
        ]

        # Disabled filters are not entry gates and should not appear as
        # permanently-green checklist rows.  This keeps the dashboard aligned
        # with CDC-pure presets (RSI 0..100, volume threshold 0, D1 off).
        if rsi_min > 0.0 or rsi_max < 100.0:
            items.append({
                "label": "RSI(14)",
                "value": round(rsi, 1),
                "ok": rsi_min <= rsi <= rsi_max,
                "hint": f"{rsi_min:.0f}-{rsi_max:.0f}",
                "columns": [rsi_zone],
                "bar": {
                    "type": "range",
                    "min": 0.0,
                    "max": 100.0,
                    "low": rsi_min,
                    "high": rsi_max,
                    "value": rsi,
                },
            })

        if vol_min_ratio > 0.0:
            items.append({
                "label": "Vol Ratio",
                "value": f"{vol_ratio:.2f}x",
                "ok": vol_ratio >= vol_min_ratio,
                "hint": f">= {vol_min_ratio:.1f}x",
                "columns": [f"Need: {vol_min_ratio:.1f}x"],
                "bar": {
                    "type": "progress",
                    "max": 3.0,
                    "threshold": vol_min_ratio,
                    "value": vol_ratio,
                },
            })

        if require_fresh_zone:
            # green_streak == 0 means the zone is not GREEN yet (no entry window
            # open). 1..window = fresh and eligible; > window = trend too mature.
            fresh_ok = in_target_zone and 1 <= target_streak <= fresh_zone_window
            if not in_target_zone:
                fresh_val = f"wait {target_zone}"
                fresh_hint = f"Need {target_zone} zone"
            elif target_streak <= fresh_zone_window:
                fresh_val = f"Fresh {target_streak}/{fresh_zone_window}"
                fresh_hint = "In window"
            else:
                fresh_val = f"EXPIRED ({target_streak}/{fresh_zone_window})"
                fresh_hint = f"Cross: {target_streak}b ago"
            items.append(
                {
                    "label": "Fresh",
                    "value": fresh_val,
                    "ok": fresh_ok,
                    "hint": fresh_hint,
                    "columns": [fresh_hint],
                }
            )

        if use_d1_regime:
            allowed_d1 = ("RED", "BLUE", "LBLUE") if short_bias else ("GREEN", "YELLOW", "ORANGE")
            items.append(
                {
                    "label": "D1 Regime",
                    "value": d1_zone,
                    "ok": d1_zone in allowed_d1,
                    "hint": "/".join(allowed_d1),
                }
            )

        return items
