"""ElliotV5 / SMAOffset (EWO) strategy plugin — port of the freqtrade original.

Mean-reversion entry against a moving average, gated by the Elliott Wave
Oscillator. Long-only, as the original is (freqtrade strategies of this family
are spot strategies).

FIDELITY NOTES — read before comparing numbers to a freqtrade backtest:

* ``minimal_roi`` **units differ**. freqtrade expresses the ladder as fractions
  (``0.099`` = 9.9%); ``xauby.runtime.exits.resolve_minimal_roi`` expects
  PERCENT. The values below are the published ElliotV5 ladder multiplied by
  100. Copying freqtrade's numbers verbatim would set a 0.099% take-profit and
  exit on noise.
* freqtrade's terminal ``"119": 0`` rung means "exit at any profit after 119
  minutes". ``resolve_minimal_roi`` drops rungs with ``roi <= 0``, and its
  docstring prescribes the workaround: use a small positive value. Hence
  ``"119": 0.01``.
* freqtrade's fixed-percent ``stoploss = -0.189`` has no equivalent — this
  engine sizes stops from ATR. It is approximated with ``sl_atr_mult``; a
  ``disable_stop_loss`` variant exists so both readings can be measured.
* freqtrade runs this over a large pair whitelist and trades whichever pair
  fires. On a single symbol that pair-selection alpha is absent, so this
  measures the signal, not the deployed system.

RESEARCH ONLY: declared ``maturity = "research"``, so the engine refuses to run
it on a ``mode: live`` pair.

Pure analyst: no orders, DB, or engine calls. The ROI ladder, stop, trailing and
breakeven are all engine-managed from config on both the live and replay paths.
"""
from __future__ import annotations

from typing import Any, Dict, List

from xauby.strategies.base import Strategy
from xauby.strategies.context import MarketContext
from xauby.strategies.elliotv5_ewo.indicators import elliotv5_snapshot
from xauby.strategies.registry import register
from xauby.strategies.signal import Signal, buy, hold, sell


@register("elliotv5_ewo")
class ElliotV5EWOStrategy(Strategy):
    version = "0.1.0"
    author = "xAuby"
    display_name = "ElliotV5 EWO"
    description = "ElliotV5/SMAOffset EWO mean-reversion (freqtrade port, long-only)."
    tags = ["elliotv5", "smaoffset", "ewo", "mean-reversion", "freqtrade", "research"]
    required_timeframes = ["1h"]
    min_bars = 260          # slow_ewo 200 + rolling warmup
    maturity = "research"

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        return {
            # Published ElliotV5 values.
            "base_nb_candles_buy": 14,
            "base_nb_candles_sell": 24,
            "low_offset": 0.975,
            "high_offset": 1.010,
            "ewo_low": -19.988,
            "ewo_high": 2.327,
            "rsi_buy": 69.0,
            "rsi_period": 14,
            "fast_ewo": 50,
            "slow_ewo": 200,
            # freqtrade ladder {0: 0.099, 10: 0.033, 38: 0.011, 119: 0}
            # converted to PERCENT, with the terminal 0 rung expressed as a
            # small positive value (resolve_minimal_roi drops roi <= 0).
            "minimal_roi": {"0": 9.9, "10": 3.3, "38": 1.1, "119": 0.01},
            # freqtrade stoploss -0.189 has no fixed-percent equivalent here.
            "atr_period": 14,
            "sl_atr_mult": 4.0,
            "disable_stop_loss": False,
            "trailing_atr_mult": 3.0,
            "fixed_tp_pct": 0.0,
            "breakeven_sl_enabled": False,
            "breakeven_activation_atr_mult": 1.5,
            "breakeven_buffer_atr_mult": 0.05,
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

    def validate_config(self) -> List[str]:
        warns: List[str] = []
        cfg = self.config
        if self._cfg_float("low_offset", 0.975, cfg) <= 0:
            warns.append("low_offset must be > 0")
        if self._cfg_float("high_offset", 1.010, cfg) <= 0:
            warns.append("high_offset must be > 0")
        if self._cfg_int("slow_ewo", 200, cfg) <= self._cfg_int("fast_ewo", 50, cfg):
            warns.append("slow_ewo must be greater than fast_ewo")
        if self._cfg_float("ewo_low", -19.988, cfg) >= self._cfg_float("ewo_high", 2.327, cfg):
            warns.append("ewo_low must be below ewo_high")
        roi = cfg.get("minimal_roi")
        if isinstance(roi, dict) and roi:
            try:
                first = max(float(v) for v in roi.values())
            except (TypeError, ValueError):
                first = 0.0
            if 0 < first < 0.5:
                # freqtrade fractions copied verbatim land here.
                warns.append(
                    "minimal_roi must be PERCENT, not freqtrade fractions "
                    "(0.099 -> 9.9); the ladder looks like fractions"
                )
        return warns

    def analyze(self, ctx: MarketContext) -> Signal:
        cfg = {**self.config, **(ctx.config or {})}
        df = ctx.df_primary
        if df is None or df.empty or len(df) < self.min_bars:
            return hold("Need more ElliotV5 candles", strategy_name=self.name,
                        timeframe=ctx.timeframe_primary)

        snap = elliotv5_snapshot(df, cfg)
        close = snap["close"]
        ewo = snap["ewo"]
        buy_line = snap["buy_line"]
        sell_line = snap["sell_line"]
        rsi_now = snap["rsi"]
        atr_now = snap["atr"]

        ewo_high = self._cfg_float("ewo_high", 2.327, cfg)
        ewo_low = self._cfg_float("ewo_low", -19.988, cfg)
        rsi_buy = self._cfg_float("rsi_buy", 69.0, cfg)
        sl_mult = self._cfg_float("sl_atr_mult", 4.0, cfg)
        trail_mult = self._cfg_float("trailing_atr_mult", 3.0, cfg)

        below_buy = buy_line > 0 and close < buy_line
        above_sell = sell_line > 0 and close > sell_line
        # The two published entry branches.
        impulse_dip = below_buy and ewo > ewo_high and rsi_now < rsi_buy
        capitulation = below_buy and ewo < ewo_low

        indicators = {
            "ewo": ewo, "rsi": rsi_now, "buy_line": buy_line,
            "sell_line": sell_line, "atr": atr_now,
        }
        checklist = [
            {"label": "Below MA offset", "value": f"{close:.4f}<{buy_line:.4f}",
             "ok": below_buy, "hint": "entry zone"},
            {"label": "EWO", "value": f"{ewo:+.2f}", "ok": impulse_dip or capitulation,
             "hint": f">{ewo_high} or <{ewo_low}"},
            {"label": "RSI", "value": f"{rsi_now:.1f}", "ok": rsi_now < rsi_buy,
             "hint": f"< {rsi_buy}"},
        ]
        common: Dict[str, Any] = {
            "indicators": indicators,
            "volatility": atr_now,
            "checklist": checklist,
            "status_summary": (
                f"EWO {ewo:+.2f} {snap['zone']} | buy<{buy_line:.4f} "
                f"sell>{sell_line:.4f}"
            ),
            "strategy_name": self.name,
            "timeframe": ctx.timeframe_primary,
        }

        # -------- position management (long-only) --------
        if ctx.has_position:
            if ctx.sl_confirmed:
                return sell("ElliotV5 SL confirmed", confidence=0.9, **common)
            if above_sell:
                return sell("ElliotV5 above MA sell offset", confidence=0.75, **common)
            return hold("ElliotV5 long open", confidence=0.5,
                        trail_distance=atr_now * trail_mult if atr_now > 0 else None,
                        **common)

        # -------- entry --------
        if atr_now <= 0:
            return hold("ElliotV5 waiting (no ATR)", confidence=0.3, **common)

        if impulse_dip or capitulation:
            reason = ("ElliotV5 dip in impulse" if impulse_dip
                      else "ElliotV5 capitulation entry")
            return buy(reason, confidence=0.66,
                       stop_loss_distance=atr_now * sl_mult,
                       trail_distance=atr_now * trail_mult, **common)

        return hold("ElliotV5 no entry condition", confidence=0.35, **common)
