"""SimpleScalp+ confluence strategy plugin (ported from Binance_Cryptonice).

A fast multi-indicator scalper. It opens a LONG only when at least
``min_confirmations_buy`` of an 8-point confluence matrix agree (Hull MA slope,
EMA9/21 trend, RSI regime, ADX strength, MACD, Stochastic, VWAP location,
volume participation) and closes the long on the softer bearish confluence
("hard entry, soft exit"). Stop distance is ATR-based.

This is the **long-only** xAuby port: the original Cryptonice strategy is
spot/long-biased, so SELL means *close the long*, never open a short. The
take-profit leg (``SL * risk_reward`` in the source) is delegated to the engine
via the per-pair ``fixed_tp_pct`` / trailing config — the strategy only emits the
SL distance and the bearish-confluence exit.

When the engine supplies a regime (confirm) timeframe (``ctx.df_regime``) the
strategy applies a higher-timeframe bias: a BUY that contradicts the macro trend
has its confidence halved (mirrors the Cryptonice MTF filter). With no regime
data the check is skipped.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from xauby.strategies.base import Strategy
from xauby.strategies.context import MarketContext
from xauby.strategies.registry import register
from xauby.strategies.signal import Signal, buy, hold, sell
from xauby.strategies.simple_scalp_plus import indicators as ind


def _series(df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    return (
        df["close"].astype(float),
        df["high"].astype(float),
        df["low"].astype(float),
        df["volume"].astype(float),
    )


def annotate(df: pd.DataFrame, cfg: Dict[str, Any]) -> pd.DataFrame:
    """Append indicator + confluence columns to ``df`` (single source of truth).

    Used by both :meth:`SimpleScalpPlusStrategy.analyze` and the chart indicator
    plugin so the dashboard never drifts from the live signal logic.
    """
    close, high, low, volume = _series(df)
    out = df.copy()
    out["ema_fast"] = ind.ema(close, int(cfg["ema_fast"]))
    out["ema_slow"] = ind.ema(close, int(cfg["ema_slow"]))
    out["rsi"] = ind.rsi(close, int(cfg["rsi_period"]))
    out["adx"] = ind.adx(high, low, close, int(cfg["adx_period"]))
    macd_line, macd_sig, macd_hist = ind.macd(
        close, int(cfg["macd_fast"]), int(cfg["macd_slow"]), int(cfg["macd_signal"])
    )
    out["macd"] = macd_line
    out["macd_signal"] = macd_sig
    out["macd_hist"] = macd_hist
    stoch_k, stoch_d = ind.stochastic(high, low, close, int(cfg["stoch_period"]))
    out["stoch_k"] = stoch_k
    out["stoch_d"] = stoch_d
    out["hull"] = ind.hull_ma(close, int(cfg["hull_period"]))
    out["hull_sig"] = ind.hull_signal(close, int(cfg["hull_period"]))
    out["vwap"] = ind.vwap(high, low, close, volume, int(cfg["volume_period"]))
    out["atr"] = ind.atr(high, low, close, int(cfg["atr_period"]))
    out["vol_ok"] = ind.volume_confirmation(
        volume, int(cfg["volume_period"]), float(cfg["volume_threshold"])
    )

    # Confluence booleans (vectorised so the chart can colour every bar).
    out["c_hull_up"] = out["hull_sig"] == 1
    out["c_ema_up"] = out["ema_fast"] > out["ema_slow"]
    out["c_rsi_bull"] = (out["rsi"] >= float(cfg["rsi_buy_min"])) & (
        out["rsi"] <= float(cfg["rsi_buy_max"])
    )
    out["c_adx"] = out["adx"] >= float(cfg["adx_threshold"])
    out["c_macd_bull"] = (out["macd"] > out["macd_signal"]) & (out["macd_hist"] > 0)
    out["c_stoch_bull"] = (
        (out["stoch_k"] >= float(cfg["stoch_buy_k_min"]))
        & (out["stoch_k"] <= float(cfg["stoch_buy_k_max"]))
        & (out["stoch_k"] >= out["stoch_d"])
    )
    out["c_above_vwap"] = close >= out["vwap"]

    out["c_hull_down"] = out["hull_sig"] == -1
    out["c_ema_down"] = out["ema_fast"] < out["ema_slow"]
    out["c_rsi_bear"] = out["rsi"] <= float(cfg["rsi_sell_max"])
    out["c_macd_bear"] = (out["macd"] < out["macd_signal"]) & (out["macd_hist"] < 0)
    out["c_stoch_bear"] = (out["stoch_k"] >= float(cfg["stoch_sell_k_max"])) & (
        out["stoch_k"] <= out["stoch_d"]
    )
    out["c_below_vwap"] = close <= out["vwap"]

    out["buy_count"] = (
        out["c_hull_up"].astype(int)
        + out["c_ema_up"].astype(int)
        + out["c_rsi_bull"].astype(int)
        + out["c_adx"].astype(int)
        + out["c_macd_bull"].astype(int)
        + out["c_stoch_bull"].astype(int)
        + out["c_above_vwap"].astype(int)
        + out["vol_ok"].astype(int)
    )
    # The long-EXIT counts DIRECTIONAL bearish signals only. ADX (trend
    # strength) and Volume (participation) are direction-neutral and are
    # (almost) always on inside a trend, so including them here turned the
    # "close long" into a hair-trigger that churned mid-uptrend. They stay in
    # buy_count (entry wants strength + participation) but not in the exit.
    out["sell_count"] = (
        out["c_hull_down"].astype(int)
        + out["c_ema_down"].astype(int)
        + out["c_rsi_bear"].astype(int)
        + out["c_macd_bear"].astype(int)
        + out["c_stoch_bear"].astype(int)
        + out["c_below_vwap"].astype(int)
    )

    min_buy = int(cfg["min_confirmations_buy"])
    min_sell = int(cfg["min_confirmations_sell"])
    zone = pd.Series("NEUTRAL", index=out.index)
    zone[out["sell_count"] >= min_sell] = "BEAR"
    zone[out["buy_count"] >= min_buy] = "BULL"
    out["scalp_zone"] = zone
    return out


# Pretty names for the reason / metadata trail.
_BUY_LABELS = {
    "c_hull_up": "HULL_UP",
    "c_ema_up": "EMA_UP",
    "c_rsi_bull": "RSI_BULL",
    "c_adx": "ADX_TRENDING",
    "c_macd_bull": "MACD_BULL",
    "c_stoch_bull": "STOCH_BULL",
    "c_above_vwap": "ABOVE_VWAP",
    "vol_ok": "VOLUME_OK",
}
_SELL_LABELS = {
    "c_hull_down": "HULL_DOWN",
    "c_ema_down": "EMA_DOWN",
    "c_rsi_bear": "RSI_BEAR",
    "c_macd_bear": "MACD_BEAR",
    "c_stoch_bear": "STOCH_BEAR",
    "c_below_vwap": "BELOW_VWAP",
}


@register("simple_scalp_plus")
class SimpleScalpPlusStrategy(Strategy):
    version = "0.1.0"
    author = "xAuby"
    description = "SimpleScalp+ 8-point confluence scalper (long-only, ATR stop)."
    tags = ["scalping", "confluence", "5m"]
    required_timeframes = ["5m"]
    min_bars = 120

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        return {
            "hull_period": 16,
            "ema_fast": 9,
            "ema_slow": 21,
            "rsi_period": 14,
            "rsi_buy_min": 30.0,
            "rsi_buy_max": 60.0,
            "rsi_sell_max": 48.0,
            "adx_period": 14,
            "adx_threshold": 18.0,
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
            "stoch_period": 14,
            "stoch_buy_k_min": 20.0,
            "stoch_buy_k_max": 80.0,
            "stoch_sell_k_max": 80.0,
            "volume_period": 20,
            "volume_threshold": 1.05,
            "atr_period": 14,
            "atr_multiplier": 1.2,
            "risk_reward": 1.8,
            "min_buy_confidence": 0.70,
            "min_confirmations_buy": 4,
            "min_confirmations_sell": 3,
            "trailing_atr_mult": 1.5,
            "mtf_filter": True,
            "require_macro_bull": False,
            "max_calc_bars": 400,
        }

    # ------------------------------------------------------------------ #
    def validate_config(self) -> List[str]:
        cfg = self.config
        warnings: List[str] = []

        def _f(key: str) -> float:
            return float(cfg.get(key, self.default_config()[key]))

        def _i(key: str) -> int:
            return int(cfg.get(key, self.default_config()[key]))

        if _f("rsi_buy_min") >= _f("rsi_buy_max"):
            warnings.append("rsi_buy_min must be less than rsi_buy_max")
        if _i("ema_fast") >= _i("ema_slow"):
            warnings.append("ema_fast must be less than ema_slow")
        if _i("macd_fast") >= _i("macd_slow"):
            warnings.append("macd_fast must be less than macd_slow")
        if not (1 <= _i("min_confirmations_buy") <= 8):
            warnings.append("min_confirmations_buy must be between 1 and 8")
        if not (1 <= _i("min_confirmations_sell") <= 6):
            warnings.append("min_confirmations_sell must be between 1 and 6")
        if _f("atr_multiplier") <= 0:
            warnings.append("atr_multiplier must be greater than 0")
        return warnings

    # ------------------------------------------------------------------ #
    @staticmethod
    def _macro_bullish(df_regime: Optional[pd.DataFrame], cfg: Dict[str, Any]) -> Optional[bool]:
        """Higher-timeframe trend bias: None when no/short regime data."""
        if df_regime is None or len(df_regime) < int(cfg["ema_slow"]) + 2:
            return None
        close = df_regime["close"].astype(float)
        ema_fast = ind.ema(close, int(cfg["ema_fast"])).iloc[-1]
        ema_slow = ind.ema(close, int(cfg["ema_slow"])).iloc[-1]
        if pd.isna(ema_fast) or pd.isna(ema_slow):
            return None
        return bool(ema_fast >= ema_slow)

    def analyze(self, ctx: MarketContext) -> Signal:
        cfg = {**self.default_config(), **self.config, **(ctx.config or {})}
        tf = ctx.timeframe_primary
        max_calc = max(self.min_bars, int(cfg["max_calc_bars"]))

        src = ctx.df_primary
        df = src.tail(max_calc) if len(src) > max_calc else src
        if df is None or df.empty or len(df) < self.min_bars:
            return hold("Need more scalp candles", strategy_name=self.name, timeframe=tf)
        if not {"close", "high", "low", "volume"}.issubset(df.columns):
            return hold("Missing OHLCV columns", strategy_name=self.name, timeframe=tf)

        ann = annotate(df, cfg)
        row = ann.iloc[-1]
        price = float(row["close"])
        atr_now = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
        buy_count = int(row["buy_count"])
        sell_count = int(row["sell_count"])
        trail_mult = float(cfg["trailing_atr_mult"])

        buy_hits = [name for col, name in _BUY_LABELS.items() if bool(row[col])]
        sell_hits = [name for col, name in _SELL_LABELS.items() if bool(row[col])]

        indicators = {
            "atr": atr_now,
            "rsi": round(float(row["rsi"]), 2) if pd.notna(row["rsi"]) else None,
            "adx": round(float(row["adx"]), 2) if pd.notna(row["adx"]) else None,
            "macd_hist": round(float(row["macd_hist"]), 6) if pd.notna(row["macd_hist"]) else None,
            "stoch_k": round(float(row["stoch_k"]), 2) if pd.notna(row["stoch_k"]) else None,
            "vwap": round(float(row["vwap"]), 6) if pd.notna(row["vwap"]) else None,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "zone": str(row["scalp_zone"]),
        }
        checklist = [
            {"label": "Confluence", "value": f"BUY {buy_count}/8 · SELL {sell_count}/6",
             "ok": buy_count >= int(cfg["min_confirmations_buy"])},
            {"label": "Hull", "value": "up" if row["c_hull_up"] else ("down" if row["c_hull_down"] else "flat"),
             "ok": bool(row["c_hull_up"])},
            {"label": "EMA9/21", "value": "bull" if row["c_ema_up"] else "bear", "ok": bool(row["c_ema_up"])},
            {"label": "RSI", "value": f"{indicators['rsi']}", "ok": bool(row["c_rsi_bull"]),
             "hint": f"{cfg['rsi_buy_min']:.0f}-{cfg['rsi_buy_max']:.0f}"},
            {"label": "ADX", "value": f"{indicators['adx']}", "ok": bool(row["c_adx"]),
             "hint": f">={cfg['adx_threshold']:.0f}"},
            {"label": "MACD", "value": "bull" if row["c_macd_bull"] else "—", "ok": bool(row["c_macd_bull"])},
            {"label": "Stoch", "value": f"{indicators['stoch_k']}", "ok": bool(row["c_stoch_bull"])},
            {"label": "VWAP", "value": "above" if row["c_above_vwap"] else "below", "ok": bool(row["c_above_vwap"])},
            {"label": "Volume", "value": "ok" if row["vol_ok"] else "low", "ok": bool(row["vol_ok"])},
        ]

        # ---- Exit (long-only): close on confirmed SL or bearish confluence ---
        if ctx.has_position and (ctx.position_side or "LONG").upper() == "LONG":
            if ctx.sl_confirmed:
                return sell("SimpleScalp+ SL confirmed", confidence=0.9, volatility=atr_now,
                            indicators=indicators, checklist=checklist,
                            strategy_name=self.name, timeframe=tf)
            if sell_count >= int(cfg["min_confirmations_sell"]):
                conf = min(0.93, sell_count / 6.0 + 0.18)
                return sell(
                    f"SimpleScalp+ SELL {sell_count}/6 [{', '.join(sell_hits)}]",
                    confidence=conf, volatility=atr_now, indicators=indicators, checklist=checklist,
                    status_summary=f"Scalp SELL {sell_count}/6", strategy_name=self.name, timeframe=tf,
                )
            return hold("SimpleScalp+ riding long", confidence=0.5, volatility=atr_now,
                        trail_distance=atr_now * trail_mult if atr_now > 0 else None,
                        indicators=indicators, checklist=checklist,
                        status_summary=f"Riding · SELL {sell_count}/6", strategy_name=self.name, timeframe=tf)

        # ---- Entry: long only -------------------------------------------------
        if buy_count >= int(cfg["min_confirmations_buy"]) and atr_now > 0:
            macro = self._macro_bullish(ctx.df_regime, cfg)
            # Hard trend gate: only go long when the higher timeframe is bullish.
            # Fixes "buy the top in chop": skips entries against the macro trend.
            # macro is None (no/short regime data) -> do not block.
            if bool(cfg.get("require_macro_bull", False)) and macro is False:
                return hold(
                    "SimpleScalp+ entry blocked: macro not bullish",
                    confidence=0.3, volatility=atr_now, indicators=indicators, checklist=checklist,
                    status_summary=f"Scalp gated · BUY {buy_count}/8", strategy_name=self.name, timeframe=tf,
                )
            conf = min(0.97, buy_count / 7.0 + 0.22)
            mtf_note = ""
            if bool(cfg.get("mtf_filter", True)):
                if macro is False:
                    conf *= 0.5
                    mtf_note = " | MTF misaligned"
                elif macro is True:
                    mtf_note = " | MTF aligned"
            if conf >= float(cfg["min_buy_confidence"]):
                return buy(
                    f"SimpleScalp+ BUY {buy_count}/8 [{', '.join(buy_hits)}]{mtf_note}",
                    confidence=conf,
                    stop_loss_distance=atr_now * float(cfg["atr_multiplier"]),
                    trail_distance=atr_now * trail_mult if trail_mult > 0 else None,
                    volatility=atr_now,
                    indicators=indicators,
                    checklist=checklist,
                    status_summary=f"Scalp BUY {buy_count}/8 ATR {atr_now:.4f}",
                    strategy_name=self.name,
                    timeframe=tf,
                    metadata={"confirmations": buy_hits, "risk_reward": float(cfg["risk_reward"])},
                )
            return hold(f"SimpleScalp+ low confidence {conf:.2f}{mtf_note}", confidence=conf,
                        volatility=atr_now, indicators=indicators, checklist=checklist,
                        status_summary=f"Scalp wait · BUY {buy_count}/8", strategy_name=self.name, timeframe=tf)

        return hold(f"SimpleScalp+ waiting · BUY {buy_count}/8", confidence=0.3,
                    volatility=atr_now if atr_now > 0 else None,
                    indicators=indicators, checklist=checklist,
                    status_summary=f"Scalp wait · BUY {buy_count}/8", strategy_name=self.name, timeframe=tf)
