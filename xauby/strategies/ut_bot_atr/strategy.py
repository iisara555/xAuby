"""UT Bot ATR trailing-stop flip strategy plugin.

An ATR trailing stop ratchets in the direction of the trade; when price closes
through it the side flips. Because the flip is symmetric the strategy is
naturally stop-and-reverse: the flip bar closes the long, and the next bar the
plugin is flat with ``pos == -1`` and opens the short.

Knobs that matter on a fast timeframe:
* ``key_value`` — ATR multiplier. Lower = more sensitive = far more trades.
* ``confirm_bars`` — a flip must hold this many closed bars before entry.
* ``min_atr_pct`` — skip entries when ATR/price is below this, i.e. dead chop
  where a 14bp round trip eats the move.
* ``stop_and_reverse`` — False requires a *fresh* flip to enter, so the plugin
  sits flat instead of always being in the market.

RESEARCH ONLY: declared ``maturity = "research"``, so the engine refuses to run
it on a ``mode: live`` pair.

Pure analyst: no orders, DB, or engine calls.
"""
from __future__ import annotations

from typing import Any, Dict, List

from xauby.strategies.base import Strategy
from xauby.strategies.context import MarketContext
from xauby.strategies.registry import register
from xauby.strategies.signal import Signal, buy, close_short, hold, open_short, sell
from xauby.strategies.ut_bot_atr.indicators import ut_bot_snapshot


@register("ut_bot_atr")
class UTBotATRStrategy(Strategy):
    version = "0.1.0"
    author = "xAuby"
    display_name = "UT Bot ATR"
    description = "UT Bot Alerts ATR trailing-stop flip (stop-and-reverse), long+short."
    tags = ["ut-bot", "atr", "trailing-stop", "stop-and-reverse", "15m", "research"]
    required_timeframes = ["15m"]
    min_bars = 120
    maturity = "research"

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        return {
            "key_value": 1.0,          # ATR multiplier ("a" in Pine)
            "atr_period": 10,          # "c" in Pine
            "use_heikin_ashi": False,
            "confirm_bars": 1,         # flip must persist N closed bars
            "min_atr_pct": 0.0,        # skip entry when atr/price < this
            "stop_and_reverse": True,  # False = only enter on a fresh flip
            "atr_period_risk": 14,     # ATR used for SL/trail sizing
            "sl_atr_mult": 2.5,
            "trailing_atr_mult": 2.0,
            "fixed_tp_pct": 0.0,
            "breakeven_sl_enabled": True,
            "breakeven_activation_atr_mult": 1.2,
            "breakeven_buffer_atr_mult": 0.05,
            "enable_short": True,
            "max_calc_bars": 300,
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
        if self._cfg_float("key_value", 1.0, self.config) <= 0:
            warns.append("key_value must be > 0")
        if self._cfg_int("atr_period", 10, self.config) < 2:
            warns.append("atr_period must be >= 2")
        if self._cfg_float("min_atr_pct", 0.0, self.config) < 0:
            warns.append("min_atr_pct must be >= 0")
        return warns

    def analyze(self, ctx: MarketContext) -> Signal:
        cfg = {**self.config, **(ctx.config or {})}
        df = ctx.df_primary
        if df is None or df.empty or len(df) < self.min_bars:
            return hold("Need more UT Bot candles", strategy_name=self.name,
                        timeframe=ctx.timeframe_primary)

        snap = ut_bot_snapshot(df, cfg)
        atr = snap["atr"]
        atr_risk = snap["atr_risk"] or atr
        stop = snap["stop"]
        pos = snap["pos"]
        zone = snap["zone"]
        close = snap["close"]

        sl_mult = self._cfg_float("sl_atr_mult", 2.5, cfg)
        trail_mult = self._cfg_float("trailing_atr_mult", 2.0, cfg)
        confirm_bars = self._cfg_int("confirm_bars", 1, cfg)
        min_atr_pct = self._cfg_float("min_atr_pct", 0.0, cfg)
        sar = self._cfg_bool("stop_and_reverse", True, cfg)
        enable_short = self._cfg_bool("enable_short", True, cfg)

        atr_pct = (atr / close * 100.0) if close > 0 else 0.0
        confirmed = snap["bars_in_pos"] >= confirm_bars
        vol_ok = atr_pct >= min_atr_pct

        indicators = {
            "ut_stop": stop,
            "ut_pos": pos,
            "ut_zone": zone,
            "atr": atr,
            "atr_pct": atr_pct,
        }
        checklist = [
            {"label": "UT Zone", "value": zone, "ok": pos > 0, "hint": "LONG/SHORT"},
            {"label": "Trail Stop", "value": f"{stop:.4f}", "ok": pos > 0},
            {"label": "Confirm", "value": f"{snap['bars_in_pos']}/{confirm_bars}",
             "ok": confirmed, "hint": "bars held"},
            {"label": "ATR%", "value": f"{atr_pct:.2f}", "ok": vol_ok,
             "hint": f">= {min_atr_pct:.2f}"},
        ]
        common: Dict[str, Any] = {
            "indicators": indicators,
            "volatility": atr,
            "checklist": checklist,
            "status_summary": f"UT {zone} | stop {stop:.4f} | ATR% {atr_pct:.2f}",
            "strategy_name": self.name,
            "timeframe": ctx.timeframe_primary,
        }

        # -------- position management --------
        if ctx.has_position and str(ctx.position_side or "").upper() == "SHORT":
            if ctx.sl_confirmed:
                return close_short("UT Bot short SL confirmed", confidence=0.9, **common)
            if snap["flip_up"] or pos > 0:
                return close_short("UT Bot flipped up", confidence=0.75, **common)
            return hold("UT Bot short open", confidence=0.5,
                        trail_distance=atr_risk * trail_mult if atr_risk > 0 else None,
                        **common)

        if ctx.has_position:
            if ctx.sl_confirmed:
                return sell("UT Bot SL confirmed", confidence=0.9, **common)
            if snap["flip_down"] or pos < 0:
                return sell("UT Bot flipped down", confidence=0.75, **common)
            return hold("UT Bot long open", confidence=0.5,
                        trail_distance=atr_risk * trail_mult if atr_risk > 0 else None,
                        **common)

        # -------- entries (flat) --------
        if atr_risk <= 0:
            return hold("UT Bot waiting (no ATR)", confidence=0.3, **common)
        if not vol_ok:
            return hold("UT Bot volatility below floor", confidence=0.3, **common)

        # stop_and_reverse re-enters on the persisted side; otherwise the flip
        # itself must be fresh, so the plugin stays flat between signals.
        long_ok = (pos > 0 and confirmed) if sar else snap["flip_up"]
        short_ok = (pos < 0 and confirmed) if sar else snap["flip_down"]

        if long_ok:
            return buy("UT Bot long entry", confidence=0.64,
                       stop_loss_distance=atr_risk * sl_mult,
                       trail_distance=atr_risk * trail_mult, **common)
        if enable_short and short_ok:
            return open_short("UT Bot short entry", confidence=0.64,
                              stop_loss_distance=atr_risk * sl_mult,
                              trail_distance=atr_risk * trail_mult, **common)

        return hold("Waiting for UT Bot flip", confidence=0.35, **common)
