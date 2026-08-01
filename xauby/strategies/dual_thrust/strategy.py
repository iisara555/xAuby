"""Dual Thrust session range-breakout strategy plugin.

Buys when price closes above the session's upper breakout line and (when
``enable_short``) sells short below the lower one. The lines are built from the
prior N completed sessions only — see ``indicators.py`` for the look-ahead
guard.

Knobs that matter on a fast timeframe:
* ``k_upper`` / ``k_lower`` — how far beyond the session open a breakout must
  travel. Lower = more trades, more noise.
* ``one_entry_per_session`` — without it price ping-pongs across a line and the
  trade count (and fee bill) explodes.
* ``min_range_pct`` — skip sessions whose prior range is degenerate.

RESEARCH ONLY: declared ``maturity = "research"``, so the engine refuses to run
it on a ``mode: live`` pair.

Pure analyst: no orders, DB, or engine calls.
"""
from __future__ import annotations

from typing import Any, Dict, List

from xauby.strategies.base import Strategy
from xauby.strategies.context import MarketContext
from xauby.strategies.dual_thrust.indicators import dual_thrust_snapshot
from xauby.strategies.registry import register
from xauby.strategies.signal import Signal, buy, close_short, hold, open_short, sell


@register("dual_thrust")
class DualThrustStrategy(Strategy):
    version = "0.1.0"
    author = "xAuby"
    display_name = "Dual Thrust"
    description = "Dual Thrust session range breakout (prior-N-session range), long+short."
    tags = ["dual-thrust", "breakout", "range", "intraday", "15m", "research"]
    required_timeframes = ["15m"]
    min_bars = 500
    maturity = "research"

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        return {
            "lookback_days": 4,
            "k_upper": 0.5,
            "k_lower": 0.5,
            "bars_per_session": 96,        # 15m bars in a UTC day
            "one_entry_per_session": True,
            "min_range_pct": 0.0,          # skip degenerate prior ranges
            "exit_on_opposite_line": True,
            "exit_at_session_end": False,
            "atr_period": 14,
            "sl_atr_mult": 2.0,
            "trailing_atr_mult": 1.5,
            "fixed_tp_pct": 0.0,
            "breakeven_sl_enabled": True,
            "breakeven_activation_atr_mult": 1.0,
            "breakeven_buffer_atr_mult": 0.05,
            "enable_short": True,
            "max_calc_bars": 600,
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
        if self._cfg_int("lookback_days", 4, self.config) < 1:
            warns.append("lookback_days must be >= 1")
        if self._cfg_float("k_upper", 0.5, self.config) <= 0:
            warns.append("k_upper must be > 0")
        if self._cfg_float("k_lower", 0.5, self.config) <= 0:
            warns.append("k_lower must be > 0")
        if self._cfg_int("bars_per_session", 96, self.config) < 2:
            warns.append("bars_per_session must be >= 2")
        return warns

    def analyze(self, ctx: MarketContext) -> Signal:
        cfg = {**self.config, **(ctx.config or {})}
        df = ctx.df_primary
        if df is None or df.empty or len(df) < self.min_bars:
            return hold("Need more Dual Thrust candles", strategy_name=self.name,
                        timeframe=ctx.timeframe_primary)

        snap = dual_thrust_snapshot(df, cfg)
        atr = snap["atr"]
        close = snap["close"]
        zone = snap["zone"]
        buy_line = snap["buy_line"]
        sell_line = snap["sell_line"]
        rng = snap["range"]

        sl_mult = self._cfg_float("sl_atr_mult", 2.0, cfg)
        trail_mult = self._cfg_float("trailing_atr_mult", 1.5, cfg)
        min_range_pct = self._cfg_float("min_range_pct", 0.0, cfg)
        one_per_session = self._cfg_bool("one_entry_per_session", True, cfg)
        exit_opposite = self._cfg_bool("exit_on_opposite_line", True, cfg)
        exit_session_end = self._cfg_bool("exit_at_session_end", False, cfg)
        enable_short = self._cfg_bool("enable_short", True, cfg)

        range_pct = (rng / close * 100.0) if close > 0 else 0.0
        range_ok = rng > 0 and range_pct >= min_range_pct

        indicators = {
            "dt_buy_line": buy_line,
            "dt_sell_line": sell_line,
            "dt_range": rng,
            "dt_zone": zone,
            "atr": atr,
        }
        checklist = [
            {"label": "DT Zone", "value": zone, "ok": zone == "LONG_BREAK",
             "hint": "breakout side"},
            {"label": "Buy Line", "value": f"{buy_line:.4f}", "ok": close > buy_line},
            {"label": "Sell Line", "value": f"{sell_line:.4f}", "ok": close < sell_line},
            {"label": "Range%", "value": f"{range_pct:.2f}", "ok": range_ok,
             "hint": f">= {min_range_pct:.2f}"},
        ]
        common: Dict[str, Any] = {
            "indicators": indicators,
            "volatility": atr,
            "checklist": checklist,
            "status_summary": (
                f"DT {zone} | buy {buy_line:.4f} / sell {sell_line:.4f}"
            ),
            "strategy_name": self.name,
            "timeframe": ctx.timeframe_primary,
        }

        # -------- position management --------
        if ctx.has_position and str(ctx.position_side or "").upper() == "SHORT":
            if ctx.sl_confirmed:
                return close_short("Dual Thrust short SL confirmed", confidence=0.9, **common)
            if exit_opposite and zone == "LONG_BREAK":
                return close_short("Dual Thrust upper line reclaimed", confidence=0.75, **common)
            if exit_session_end and snap["is_session_last_bar"]:
                return close_short("Dual Thrust session end", confidence=0.6, **common)
            return hold("Dual Thrust short open", confidence=0.5,
                        trail_distance=atr * trail_mult if atr > 0 else None, **common)

        if ctx.has_position:
            if ctx.sl_confirmed:
                return sell("Dual Thrust SL confirmed", confidence=0.9, **common)
            if exit_opposite and zone == "SHORT_BREAK":
                return sell("Dual Thrust lower line broken", confidence=0.75, **common)
            if exit_session_end and snap["is_session_last_bar"]:
                return sell("Dual Thrust session end", confidence=0.6, **common)
            return hold("Dual Thrust long open", confidence=0.5,
                        trail_distance=atr * trail_mult if atr > 0 else None, **common)

        # -------- entries (flat) --------
        if atr <= 0 or not range_ok:
            return hold("Dual Thrust waiting (no range)", confidence=0.3, **common)

        long_ok = zone == "LONG_BREAK"
        short_ok = zone == "SHORT_BREAK"
        if one_per_session:
            long_ok = long_ok and not snap["entered_long_this_session"]
            short_ok = short_ok and not snap["entered_short_this_session"]

        if long_ok:
            return buy("Dual Thrust upper breakout", confidence=0.64,
                       stop_loss_distance=atr * sl_mult,
                       trail_distance=atr * trail_mult, **common)
        if enable_short and short_ok:
            return open_short("Dual Thrust lower breakdown", confidence=0.64,
                              stop_loss_distance=atr * sl_mult,
                              trail_distance=atr * trail_mult, **common)

        return hold("Dual Thrust inside range", confidence=0.35, **common)
