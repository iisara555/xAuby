"""xAuby EMA/VWAP pullback strategy.

Long-only intraday setup adapted from the standalone BTC pullback script:
trade with the EMA20/50/200 trend, require price above session VWAP, wait for
a pullback into EMA20, then buy a bullish reclaim candle with sufficient volume.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

from xauby.strategies.base import Strategy
from xauby.strategies.context import MarketContext
from xauby.strategies.registry import register
from xauby.strategies.signal import Signal, buy, hold, sell


def _to_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.dropna(subset=["open", "high", "low", "close", "volume"])


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def _session_key(out: pd.DataFrame) -> pd.Series:
    if "open_time" in out.columns:
        raw = out["open_time"]
        numeric = pd.to_numeric(raw, errors="coerce")
        if numeric.notna().any():
            unit = "ms" if float(numeric.abs().max() or 0.0) > 10_000_000_000 else "s"
            return pd.to_datetime(numeric, unit=unit, utc=True, errors="coerce").dt.date
        return pd.to_datetime(raw, utc=True, errors="coerce").dt.date
    if "timestamp" in out.columns:
        numeric = pd.to_numeric(out["timestamp"], errors="coerce")
        unit = "ms" if float(numeric.abs().max() or 0.0) > 10_000_000_000 else "s"
        return pd.to_datetime(numeric, unit=unit, utc=True, errors="coerce").dt.date
    return pd.Series(0, index=out.index)


def annotate(df: pd.DataFrame, cfg: Dict[str, Any]) -> pd.DataFrame:
    out = _to_ohlcv(df)
    if out.empty:
        return out

    ema_fast_n = int(cfg["ema_fast"])
    ema_slow_n = int(cfg["ema_slow"])
    ema_trend_n = int(cfg["ema_trend"])
    atr_n = int(cfg["atr_period"])
    vol_n = int(cfg["volume_ma_period"])
    pullback_lookback = int(cfg["pullback_lookback"])
    pullback_mult = float(cfg["pullback_atr_mult"])
    body_min_atr = float(cfg["min_body_atr"])
    vol_min = float(cfg["volume_min_ratio"])
    reclaim_lookback = int(cfg["reclaim_lookback"])

    close = out["close"].astype(float)
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    open_ = out["open"].astype(float)
    volume = out["volume"].astype(float)

    out["ema_fast"] = close.ewm(span=ema_fast_n, adjust=False).mean()
    out["ema_slow"] = close.ewm(span=ema_slow_n, adjust=False).mean()
    out["ema_trend"] = close.ewm(span=ema_trend_n, adjust=False).mean()
    out["atr"] = _atr(high, low, close, atr_n).fillna(0.0)

    typical = (high + low + close) / 3.0
    pv = typical * volume
    session = _session_key(out)
    out["vwap"] = pv.groupby(session).cumsum() / volume.groupby(session).cumsum().replace(0.0, float("nan"))

    out["volume_ma"] = volume.rolling(vol_n).mean()
    out["volume_ratio"] = volume / out["volume_ma"].replace(0.0, float("nan"))

    out["trend_ok"] = (close > out["ema_trend"]) & (out["ema_fast"] > out["ema_slow"])
    out["vwap_ok"] = (close > out["vwap"]).fillna(False)
    pullback_touch = low <= (out["ema_fast"] + out["atr"] * pullback_mult)
    out["pullback_ok"] = pullback_touch.rolling(pullback_lookback, min_periods=1).max().astype(bool)
    body_atr = (close - open_).abs() / out["atr"].replace(0.0, float("nan"))
    out["body_atr"] = body_atr.fillna(0.0)
    out["bullish_candle"] = close > open_
    prior_high = high.shift(1).rolling(reclaim_lookback).max()
    out["reclaim_ok"] = (close > out["ema_fast"]) & (close > prior_high.fillna(out["ema_fast"]))
    out["volume_ok"] = out["volume_ratio"] >= vol_min
    out["body_ok"] = out["body_atr"] >= body_min_atr

    zone = pd.Series("NEUTRAL", index=out.index)
    zone[close <= out["ema_trend"]] = "BEAR"
    zone[out["trend_ok"] & out["vwap_ok"]] = "TREND"
    zone[out["trend_ok"] & out["vwap_ok"] & out["pullback_ok"]] = "PULLBACK"
    zone[
        out["trend_ok"]
        & out["vwap_ok"]
        & out["pullback_ok"]
        & out["reclaim_ok"]
        & out["bullish_candle"]
        & out["volume_ok"]
        & out["body_ok"]
    ] = "ENTRY"
    out["vwap_pullback_zone"] = zone
    return out


@register("xauby_vwap_pullback")
class XAubyVWAPPullbackStrategy(Strategy):
    version = "0.1.0"
    author = "xAuby"
    description = "Long-only EMA20/50/200 + session VWAP pullback strategy with ATR risk."
    tags = ["intraday", "15m", "ema", "vwap", "pullback", "long-only"]
    required_timeframes = ["15m"]
    min_bars = 220

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        return {
            "ema_fast": 20,
            "ema_slow": 50,
            "ema_trend": 200,
            "atr_period": 14,
            "volume_ma_period": 20,
            "volume_min_ratio": 1.0,
            "pullback_lookback": 3,
            "pullback_atr_mult": 0.35,
            "reclaim_lookback": 2,
            "min_body_atr": 0.05,
            "sl_atr_mult": 1.5,
            "risk_reward": 2.0,
            "trailing_atr_mult": 1.2,
            "fixed_tp_pct": 1.2,
            "breakeven_sl_enabled": True,
            "breakeven_activation_atr_mult": 1.0,
            "breakeven_buffer_atr_mult": 0.05,
            "exit_on_trend_loss": True,
            "exit_on_vwap_loss": True,
            "max_calc_bars": 500,
        }

    def validate_config(self) -> List[str]:
        cfg = {**self.default_config(), **self.config}
        warnings: List[str] = []
        if int(cfg["ema_fast"]) >= int(cfg["ema_slow"]):
            warnings.append("ema_fast must be less than ema_slow")
        if int(cfg["ema_slow"]) >= int(cfg["ema_trend"]):
            warnings.append("ema_slow must be less than ema_trend")
        if float(cfg["risk_reward"]) < 2.0:
            warnings.append("risk_reward must be at least 2.0")
        if float(cfg["sl_atr_mult"]) <= 0:
            warnings.append("sl_atr_mult must be greater than 0")
        return warnings

    @staticmethod
    def _item(label: str, value: str, ok: bool, hint: str = "") -> Dict[str, Any]:
        return {"label": label, "value": value, "ok": bool(ok), "hint": hint}

    def analyze(self, ctx: MarketContext) -> Signal:
        cfg = {**self.default_config(), **self.config, **(ctx.config or {})}
        max_calc = max(self.min_bars, int(cfg["max_calc_bars"]))
        df = ctx.df_primary.tail(max_calc) if len(ctx.df_primary) > max_calc else ctx.df_primary
        df = _to_ohlcv(df)
        if len(df) < self.min_bars:
            return hold(
                "Need more xAuby VWAP pullback candles",
                strategy_name=self.name,
                timeframe=ctx.timeframe_primary,
            )

        ann = annotate(df, cfg)
        row = ann.iloc[-1]
        price = float(row["close"])
        atr = float(row["atr"] or 0.0)
        ema_fast = float(row["ema_fast"])
        ema_slow = float(row["ema_slow"])
        ema_trend = float(row["ema_trend"])
        vwap = float(row["vwap"]) if pd.notna(row["vwap"]) else 0.0
        vol_ratio = float(row["volume_ratio"]) if pd.notna(row["volume_ratio"]) else 0.0
        sl_dist = atr * float(cfg["sl_atr_mult"]) if atr > 0 else 0.0
        sl_price = price - sl_dist if sl_dist > 0 else 0.0
        rr = float(cfg["risk_reward"])
        tp_price = price + sl_dist * rr if sl_dist > 0 else 0.0
        trail = atr * float(cfg["trailing_atr_mult"]) if atr > 0 else None

        trend_ok = bool(row["trend_ok"])
        vwap_ok = bool(row["vwap_ok"])
        pullback_ok = bool(row["pullback_ok"])
        reclaim_ok = bool(row["reclaim_ok"])
        bullish_ok = bool(row["bullish_candle"])
        volume_ok = bool(row["volume_ok"])
        body_ok = bool(row["body_ok"])
        risk_ok = sl_dist > 0 and rr >= 2.0
        entry_ok = all([trend_ok, vwap_ok, pullback_ok, reclaim_ok, bullish_ok, volume_ok, body_ok, risk_ok])
        passed = sum([trend_ok, vwap_ok, pullback_ok, reclaim_ok, bullish_ok, volume_ok, body_ok, risk_ok])
        confidence = min(0.95, 0.25 + passed / 8.0 * 0.65)

        indicators = {
            "ema_fast": round(ema_fast, 6),
            "ema_slow": round(ema_slow, 6),
            "ema_trend": round(ema_trend, 6),
            "vwap": round(vwap, 6),
            "atr": round(atr, 6),
            "volume_ratio": round(vol_ratio, 3),
            "body_atr": round(float(row["body_atr"] or 0.0), 3),
            "zone": str(row["vwap_pullback_zone"]),
            "risk_reward": rr,
            "take_profit_price": round(tp_price, 6) if tp_price > 0 else None,
        }
        checklist = [
            self._item("EMA Trend", f"{price:.2f}>{ema_trend:.2f}", trend_ok, "close>EMA200, EMA20>50"),
            self._item("VWAP", f"{price:.2f}>{vwap:.2f}", vwap_ok, "session VWAP"),
            self._item("Pullback", f"low near EMA{int(cfg['ema_fast'])}", pullback_ok),
            self._item("Reclaim", "close > EMA fast + prior high", reclaim_ok),
            self._item("Bull Candle", "close > open", bullish_ok),
            self._item("Volume", f"{vol_ratio:.2f}x", volume_ok, f">={float(cfg['volume_min_ratio']):.2f}x"),
            self._item("Body", f"{indicators['body_atr']:.2f} ATR", body_ok, f">={float(cfg['min_body_atr']):.2f}"),
            self._item("RR", f"1:{rr:.1f}", risk_ok, ">=1:2"),
        ]
        metadata = {
            "stop_loss_price": round(sl_price, 6) if sl_price > 0 else None,
            "take_profit_price": round(tp_price, 6) if tp_price > 0 else None,
            "risk_reward": rr,
            "checklist_passed": passed,
        }

        if ctx.has_position:
            if ctx.sl_confirmed:
                return sell(
                    "xAuby VWAP pullback SL confirmed",
                    confidence=0.9,
                    volatility=atr,
                    indicators=indicators,
                    checklist=checklist,
                    metadata=metadata,
                    strategy_name=self.name,
                    timeframe=ctx.timeframe_primary,
                )
            trend_lost = price < ema_slow or ema_fast < ema_slow
            vwap_lost = price < vwap
            if bool(cfg["exit_on_trend_loss"]) and trend_lost:
                return sell(
                    "xAuby VWAP pullback trend lost",
                    confidence=0.65,
                    volatility=atr,
                    indicators=indicators,
                    checklist=checklist,
                    metadata=metadata,
                    strategy_name=self.name,
                    timeframe=ctx.timeframe_primary,
                )
            if bool(cfg["exit_on_vwap_loss"]) and vwap_lost:
                return sell(
                    "xAuby VWAP pullback VWAP lost",
                    confidence=0.6,
                    volatility=atr,
                    indicators=indicators,
                    checklist=checklist,
                    metadata=metadata,
                    strategy_name=self.name,
                    timeframe=ctx.timeframe_primary,
                )
            return hold(
                "xAuby VWAP pullback position managed",
                confidence=0.5,
                volatility=atr,
                trail_distance=trail,
                indicators=indicators,
                checklist=checklist,
                metadata=metadata,
                strategy_name=self.name,
                timeframe=ctx.timeframe_primary,
            )

        if entry_ok:
            return buy(
                "xAuby EMA/VWAP pullback confirmed",
                confidence=confidence,
                stop_loss_price=sl_price if sl_price > 0 else None,
                stop_loss_distance=sl_dist if sl_dist > 0 else None,
                trail_distance=trail,
                volatility=atr,
                indicators=indicators,
                checklist=checklist,
                metadata=metadata,
                status_summary=f"xAuby VWAP BUY RR 1:{rr:.1f}",
                strategy_name=self.name,
                timeframe=ctx.timeframe_primary,
            )

        return hold(
            "Waiting for xAuby EMA/VWAP pullback",
            confidence=confidence,
            volatility=atr if atr > 0 else None,
            indicators=indicators,
            checklist=checklist,
            metadata=metadata,
            status_summary=f"xAuby VWAP pullback {passed}/8",
            strategy_name=self.name,
            timeframe=ctx.timeframe_primary,
        )
