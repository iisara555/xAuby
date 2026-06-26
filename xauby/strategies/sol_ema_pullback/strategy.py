"""SOL EMA20/50 pullback strategy.

Long-only trend-following setup for SOL: trade with EMA20 above EMA50, wait for
a pullback into EMA20, require a bullish reversal candle with stronger volume,
then enter after the confirmation candle closes. Stop-loss is anchored below the
recent swing low; target metadata is calculated at RR >= 1:2 while engine exits
still use the shared fixed-TP/trailing-stop controls.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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


def _closed_trade_dt(trade: Dict[str, Any]) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(trade.get("closed_at", "")).replace("Z", ""))
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _closed_trade_is_stop_loss(trade: Dict[str, Any]) -> bool:
    trigger = str(trade.get("trigger") or "").lower()
    try:
        pnl = float(trade.get("net_pnl", 0.0) or 0.0)
    except (TypeError, ValueError):
        pnl = 0.0
    return pnl < 0 or "stop loss" in trigger or "stop_loss" in trigger or " sl" in trigger


def _series_as_utc_datetime(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().any():
        max_abs = float(numeric.abs().max() or 0.0)
        unit = "ms" if max_abs > 10_000_000_000 else "s"
        return pd.to_datetime(numeric, unit=unit, utc=True, errors="coerce")
    return pd.to_datetime(values, utc=True, errors="coerce")


def annotate(df: pd.DataFrame, cfg: Dict[str, Any]) -> pd.DataFrame:
    out = _to_ohlcv(df)
    if out.empty:
        return out

    ema_fast_n = int(cfg["ema_fast"])
    ema_slow_n = int(cfg["ema_slow"])
    atr_n = int(cfg["atr_period"])
    vol_n = int(cfg["volume_ma_period"])
    swing_n = int(cfg["swing_lookback"])
    pullback_lookback = int(cfg["pullback_lookback"])
    pullback_mult = float(cfg["pullback_atr_mult"])
    body_min_atr = float(cfg["min_body_atr"])
    vol_min = float(cfg["volume_min_ratio"])
    confirm_lookback = int(cfg["confirm_lookback"])

    close = out["close"]
    high = out["high"]
    low = out["low"]
    open_ = out["open"]
    volume = out["volume"]

    out["ema20"] = close.ewm(span=ema_fast_n, adjust=False).mean()
    out["ema50"] = close.ewm(span=ema_slow_n, adjust=False).mean()
    out["atr"] = _atr(high, low, close, atr_n).fillna(0.0)
    out["volume_ma"] = volume.rolling(vol_n).mean()
    out["vol_ratio"] = volume / out["volume_ma"].replace(0.0, pd.NA)
    out["swing_low"] = low.rolling(swing_n).min()

    out["trend_ok"] = out["ema20"] > out["ema50"]
    pullback_touch = (low <= (out["ema20"] + out["atr"] * pullback_mult)) & (
        close >= (out["ema20"] - out["atr"] * pullback_mult)
    )
    out["pullback_ok"] = pullback_touch.rolling(pullback_lookback, min_periods=1).max().astype(bool)
    body_atr = (close - open_).abs() / out["atr"].replace(0.0, pd.NA)
    out["body_atr"] = body_atr.fillna(0.0)
    out["reversal_ok"] = (close > open_) & (close > close.shift(1)) & (out["body_atr"] >= body_min_atr)
    out["volume_ok"] = (out["vol_ratio"] >= vol_min) & (volume > volume.shift(1))
    recent_high = high.shift(1).rolling(confirm_lookback).max()
    out["confirm_ok"] = (close > out["ema20"]) & (close > recent_high)
    out["momentum_ok"] = close >= out["ema20"]

    zone = pd.Series("NEUTRAL", index=out.index)
    zone[out["ema20"] <= out["ema50"]] = "BEAR"
    zone[out["trend_ok"] & out["pullback_ok"]] = "PULLBACK"
    zone[
        out["trend_ok"]
        & out["pullback_ok"]
        & out["reversal_ok"]
        & out["volume_ok"]
        & out["confirm_ok"]
    ] = "ENTRY"
    out["ema_pullback_zone"] = zone
    return out


@register("sol_ema_pullback")
class SOLEMAPullbackStrategy(Strategy):
    version = "0.1.0"
    author = "xAuby"
    description = "SOL long-only EMA20/50 pullback strategy with swing-low stop."
    tags = ["sol", "15m", "ema", "pullback", "long-only"]
    required_timeframes = ["15m"]
    min_bars = 90

    @classmethod
    def default_config(cls) -> Dict[str, Any]:
        return {
            "ema_fast": 20,
            "ema_slow": 50,
            "atr_period": 14,
            "volume_ma_period": 20,
            "volume_min_ratio": 1.10,
            "pullback_lookback": 5,
            "pullback_atr_mult": 0.7,
            "min_body_atr": 0.08,
            "confirm_lookback": 2,
            "swing_lookback": 8,
            "sl_buffer_atr": 0.10,
            "min_sl_atr_mult": 0.8,
            "risk_reward": 2.0,
            "trailing_atr_mult": 1.2,
            "exit_on_momentum_loss": True,
            "fixed_tp_pct": 2.0,
            "max_calc_bars": 300,
            "reentry_guard_enabled": True,
            "cooldown_after_tp_minutes": 15,
            "cooldown_after_sl_minutes": 60,
            "require_new_pullback_after_exit": True,
            "require_new_pullback_after_sl": True,
        }

    def validate_config(self) -> List[str]:
        cfg = {**self.default_config(), **self.config}
        warnings: List[str] = []
        if int(cfg["ema_fast"]) >= int(cfg["ema_slow"]):
            warnings.append("ema_fast must be less than ema_slow")
        if float(cfg["risk_reward"]) < 2.0:
            warnings.append("risk_reward should be at least 2.0")
        if float(cfg["volume_min_ratio"]) <= 0:
            warnings.append("volume_min_ratio must be greater than 0")
        return warnings

    def config_errors(self) -> List[str]:
        cfg = {**self.default_config(), **self.config}
        errors: List[str] = []
        if int(cfg["ema_fast"]) >= int(cfg["ema_slow"]):
            errors.append("ema_fast must be less than ema_slow")
        if float(cfg["risk_reward"]) < 2.0:
            errors.append("risk_reward must be at least 2.0")
        return errors

    @staticmethod
    def _item(label: str, value: str, ok: bool, hint: str = "") -> Dict[str, Any]:
        return {"label": label, "value": value, "ok": bool(ok), "hint": hint}

    def analyze(self, ctx: MarketContext) -> Signal:
        cfg = {**self.default_config(), **self.config, **(ctx.config or {})}
        max_calc = max(self.min_bars, int(cfg["max_calc_bars"]))
        df = ctx.df_primary.tail(max_calc) if len(ctx.df_primary) > max_calc else ctx.df_primary
        df = _to_ohlcv(df)
        if len(df) < self.min_bars:
            return hold("Need more SOL EMA pullback candles", strategy_name=self.name, timeframe=ctx.timeframe_primary)

        ann = annotate(df, cfg)
        if ann.empty:
            return hold("Missing SOL OHLCV data", strategy_name=self.name, timeframe=ctx.timeframe_primary)

        row = ann.iloc[-1]
        price = float(row["close"])
        atr = float(row["atr"] or 0.0)
        ema20 = float(row["ema20"])
        ema50 = float(row["ema50"])
        swing_low = float(row["swing_low"]) if pd.notna(row["swing_low"]) else float(row["low"])
        sl_price = swing_low - (atr * float(cfg["sl_buffer_atr"]) if atr > 0 else 0.0)
        min_sl_dist = atr * float(cfg["min_sl_atr_mult"]) if atr > 0 else 0.0
        sl_dist = max(price - sl_price, min_sl_dist)
        if sl_dist <= 0 and atr > 0:
            sl_dist = atr * float(cfg["min_sl_atr_mult"])
            sl_price = price - sl_dist
        rr = float(cfg["risk_reward"])
        tp_price = price + sl_dist * rr if sl_dist > 0 else 0.0
        trail = atr * float(cfg["trailing_atr_mult"]) if atr > 0 else None

        trend_ok = bool(row["trend_ok"])
        pullback_ok = bool(row["pullback_ok"])
        reversal_ok = bool(row["reversal_ok"])
        volume_ok = bool(row["volume_ok"])
        confirm_ok = bool(row["confirm_ok"])
        rr_ok = rr >= 2.0 and sl_dist > 0
        momentum_ok = bool(row["momentum_ok"])
        entry_ok = trend_ok and pullback_ok and reversal_ok and volume_ok and confirm_ok and rr_ok
        passed = sum([trend_ok, pullback_ok, reversal_ok, volume_ok, confirm_ok, rr_ok])
        confidence = min(0.95, 0.35 + passed / 6 * 0.55)

        indicators = {
            "ema20": round(ema20, 6),
            "ema50": round(ema50, 6),
            "atr": round(atr, 6),
            "swing_low": round(swing_low, 6),
            "vol_ratio": round(float(row["vol_ratio"] or 0.0), 3) if pd.notna(row["vol_ratio"]) else 0.0,
            "zone": str(row["ema_pullback_zone"]),
            "risk_reward": rr,
            "take_profit_price": round(tp_price, 6) if tp_price > 0 else None,
        }
        checklist = [
            self._item("EMA20>50", f"{ema20:.2f}>{ema50:.2f}", trend_ok),
            self._item("Pullback", f"near EMA20 {ema20:.2f}", pullback_ok),
            self._item("Reversal", f"body {float(row['body_atr']):.2f} ATR", reversal_ok, f">={float(cfg['min_body_atr']):.2f}"),
            self._item("Volume", f"{indicators['vol_ratio']:.2f}x", volume_ok, f">={float(cfg['volume_min_ratio']):.2f}x"),
            self._item("Confirm", "close > EMA20 + prior high", confirm_ok),
            self._item("RR", f"1:{rr:.1f}", rr_ok, ">=1:2"),
        ]
        metadata = {
            "stop_loss_price": round(sl_price, 6) if sl_price > 0 else None,
            "take_profit_price": round(tp_price, 6) if tp_price > 0 else None,
            "risk_reward": rr,
            "checklist_passed": passed,
        }

        def reentry_reset_pending() -> bool:
            if ctx.has_position or not bool(cfg.get("reentry_guard_enabled", False)):
                return False
            trade = (ctx.extras or {}).get("last_closed_trade") or {}
            if not isinstance(trade, dict) or not trade:
                return False
            require_exit = bool(cfg.get("require_new_pullback_after_exit", False))
            require_sl = bool(cfg.get("require_new_pullback_after_sl", False))
            is_sl = _closed_trade_is_stop_loss(trade)
            if not require_exit and not (is_sl and require_sl):
                return False
            closed_at = _closed_trade_dt(trade)
            if closed_at is None or "timestamp" not in ann.columns:
                return False
            ann_dt = _series_as_utc_datetime(ann["timestamp"])
            after_exit = ann.loc[ann_dt > closed_at]
            if after_exit.empty:
                return True
            zones = after_exit["ema_pullback_zone"].astype(str)
            prior_zones = zones.iloc[:-1] if len(zones) > 1 else zones.iloc[:0]
            return not prior_zones.isin(["PULLBACK", "NEUTRAL", "BEAR"]).any()

        if ctx.has_position:
            if ctx.sl_confirmed:
                return sell(
                    "SOL EMA pullback SL confirmed",
                    confidence=0.9,
                    volatility=atr,
                    indicators=indicators,
                    checklist=checklist,
                    metadata=metadata,
                    strategy_name=self.name,
                    timeframe=ctx.timeframe_primary,
                )
            if bool(cfg["exit_on_momentum_loss"]) and (not momentum_ok or ema20 <= ema50):
                return sell(
                    "SOL EMA pullback momentum lost",
                    confidence=0.65,
                    volatility=atr,
                    indicators=indicators,
                    checklist=checklist,
                    metadata=metadata,
                    strategy_name=self.name,
                    timeframe=ctx.timeframe_primary,
                )
            return hold(
                "SOL EMA pullback position managed",
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
            if reentry_reset_pending():
                return hold(
                    "SOL re-entry guard waiting for new pullback reset",
                    confidence=confidence,
                    volatility=atr if atr > 0 else None,
                    indicators=indicators,
                    checklist=checklist,
                    metadata=metadata,
                    status_summary="SOL re-entry guard active",
                    strategy_name=self.name,
                    timeframe=ctx.timeframe_primary,
                )
            return buy(
                "SOL EMA20/50 pullback confirmed",
                confidence=confidence,
                stop_loss_price=sl_price if sl_price > 0 else None,
                stop_loss_distance=sl_dist if sl_dist > 0 else None,
                trail_distance=trail,
                volatility=atr,
                indicators=indicators,
                checklist=checklist,
                metadata=metadata,
                status_summary=f"SOL EMA pullback BUY RR 1:{rr:.1f}",
                strategy_name=self.name,
                timeframe=ctx.timeframe_primary,
            )

        return hold(
            "Waiting for SOL EMA20/50 pullback",
            confidence=confidence,
            volatility=atr if atr > 0 else None,
            indicators=indicators,
            checklist=checklist,
            metadata=metadata,
            status_summary=f"SOL EMA pullback {passed}/6",
            strategy_name=self.name,
            timeframe=ctx.timeframe_primary,
        )
