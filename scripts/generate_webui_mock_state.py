#!/usr/bin/env python3
"""Seed core/logs/xauby_bot_state.json + core/xauby.db with realistic mock
data so the WebUI (xauby/webui/) can be screenshotted and design-reviewed
without a live engine. Writes only into gitignored core/ — safe to rerun.

Usage:
    python3 scripts/generate_webui_mock_state.py --variant typical
    python3 scripts/generate_webui_mock_state.py --variant stress
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

SYMBOL = "XAUUSDT"
TIMEFRAME = "4h"
PORTFOLIO_SYMBOLS = ["BTCUSDT", "ETHUSDT", "XAGUSDT", "SOLUSDT"]

STRESS_STRINGS = {
    "regime": "BEAR_TREND_STRONG_WITH_LIQUIDITY_SQUEEZE",
    "macro_summary": (
        "USD strength accelerating as DXY breaks above the 105 pivot while "
        "10Y real yields grind higher — gold's inverse correlation to the "
        "dollar has firmed over the trailing 20 sessions, and the blended "
        "macro score has crossed the blocking threshold for new entries."
    ),
    "headline": (
        "Fed officials signal extended hold as core inflation print beats "
        "expectations for a third consecutive month, dollar index surges"
    ),
    "reason": (
        "Zone flipped RED on the 4h close with EMA12 crossing below EMA26 "
        "while daily regime confirmation remains BEARISH and volatility is "
        "in EXPANDING state — stop-and-reverse conditions met"
    ),
}


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def build_checklist(mix: bool) -> List[Dict[str, Any]]:
    items = [
        {"label": "Zone Fresh", "value": "3 bars", "ok": True},
        {"label": "Slow Slope", "value": "Rising", "ok": True},
        {"label": "RSI Range", "value": "58.4", "ok": True},
        {"label": "Volume Ratio", "value": "1.24x", "ok": True},
        {"label": "D1 Regime Filter", "value": "Bullish", "ok": mix},
        {"label": "Cooldown Clear", "value": "Yes", "ok": True},
        {"label": "Max Open Positions", "value": "1 / 1", "ok": not mix},
        {"label": "WS Feed Fresh", "value": "2s", "ok": not mix if mix else True},
    ]
    return items


def build_regime_reasons(stress: bool) -> List[Dict[str, Any]]:
    if stress:
        return [
            {"label": STRESS_STRINGS["reason"], "supportive": False},
            {"label": "Momentum 20 negative", "supportive": False},
            {"label": "Volume confirms breakdown", "supportive": False},
            {"label": "Liquidity thinning into close", "supportive": False},
        ]
    return [
        {"label": "Zone GREEN with rising slow EMA", "supportive": True},
        {"label": "Momentum 20 positive", "supportive": True},
        {"label": "Macro bias RISK-ON", "supportive": True},
        {"label": "Volatility NORMAL", "supportive": False},
    ]


def build_macro_guard(blocking: bool, stress: bool) -> Dict[str, Any]:
    score = -0.62 if blocking else 0.34
    return {
        "enabled": True,
        "applies_to_symbol": True,
        "scope": "gold_macro",
        "blocks_buy": blocking,
        "score": round(score, 3),
        "blocking_threshold": -0.5,
        "dxy_score": -0.7 if blocking else 0.4,
        "dxy_price": 105.82 if blocking else 101.14,
        "fred_score": -0.3 if blocking else 0.1,
        "fred_rate": 5.25,
        "news_score": -0.5 if blocking else 0.2,
        "news_reason": STRESS_STRINGS["headline"] if stress else "USD strength pressures gold",
        "headlines": (
            [
                STRESS_STRINGS["headline"],
                "Gold slips as Treasury yields extend climb",
                "Physical demand from Asia softens on price strength",
                "ETF outflows continue for fourth straight week",
            ]
            if stress
            else [
                "Safe-haven buying picks up on geopolitical risk",
                "Weak dollar lifts bullion toward monthly high",
                "Central bank gold purchases remain robust",
            ]
        ),
        "summary": (
            STRESS_STRINGS["macro_summary"]
            if stress
            else "USD Weak, Rates 5.25%, News: Safe-haven buying picks up"
        ),
    }


def build_position(open_pos: bool, side: str, profitable: bool, current_price: float) -> Dict[str, Any]:
    # Derive entry from the live price (current_price) rather than a fixed
    # constant, so entry/stop/target/mark all stay in the same band as the
    # generated candle series instead of floating off the visible chart.
    mark = current_price
    ratio = 1.012 if profitable else 0.988
    entry = mark / ratio if side == "LONG" else mark / (2.0 - ratio)
    pnl_pct = 1.2 if profitable else -1.2
    qty = 0.045
    gross = entry * qty * (pnl_pct / 100.0)
    fees = 1.85
    return {
        "state": "bought" if open_pos else "idle",
        "position_side": side,
        "quantity": qty if open_pos else 0.0,
        "entry_price": entry if open_pos else 0.0,
        "mark_price": round(mark, 2),
        "stop_loss": round(entry * 0.965, 2) if open_pos else 0.0,
        "take_profit": round(entry * 1.08, 2) if open_pos else 0.0,
        "highest_price_seen": round(mark * 1.01, 2),
        "unrealized_pnl": round(gross - fees, 2) if open_pos else 0.0,
        "unrealized_pnl_pct": round(pnl_pct, 2) if open_pos else 0.0,
        "unrealized_pnl_gross": round(gross, 2) if open_pos else 0.0,
        "estimated_entry_fee": 0.92,
        "estimated_exit_fee": 0.93,
        "estimated_total_fees": round(fees, 2),
        "opened_at": "2026-07-11T08:00:00",
        "stop_loss_order_id": "sl-8823471" if open_pos else None,
        "leverage": 1.0,
        "margin_mode": "isolated",
        "liquidation_price": round(entry * 0.42, 2) if open_pos else 0.0,
        "funding_paid": 0.31,
        "management_mode": "strategy",
        "partial_tp_taken": profitable,
        "partial_tp_pct": 12.0,
        "partial_tp_fraction": 0.5,
        "partial_tp_trigger_price": round(entry * 1.12, 2),
        "mark_price_ts": now_iso(),
        "exchange": "okx",
        "market_type": "SWAP",
        "feed_health": "OK",
    }


def build_symbol_snapshot(
    symbol: str,
    *,
    price: float,
    position_open: bool,
    side: str,
    profitable: bool,
    mode: str,
    read_only: bool,
    macro_blocking: bool,
    stress: bool,
    base_value_usdt: float,
) -> Dict[str, Any]:
    position = build_position(position_open, side, profitable, price)
    return {
        # pid=1 (init) is always alive, so HealthMonitor reports engine
        # status "RUNNING" for the typical variant; the stress variant uses
        # an intentionally-dead pid to exercise the "stale/unknown" status
        # fallback string instead (see xauby/observability/health.py).
        "pid": 999999 if stress else 1,
        "run_id": "mock-run-0001",
        "timestamp": now_iso(),
        "engine_started_at": "2026-07-10T00:00:00",
        "api_latency_ms": 184 if stress else 42,
        "clock_offset_seconds": 0.8,
        "latency": {
            "api_latency_ms": 184 if stress else 42,
            "ws_tick_age_ms": 8200 if stress else 900,
            "tick_duration_ms": 1240 if stress else 380,
            "sync_candles_ms": 210,
            "state_export_ms": 14,
            "strategy_ms": 6,
            "regime_ms": 22,
            "event_store_ms": 3,
            "clock_offset_seconds": 0.8,
        },
        "interval_seconds": 30,
        "symbol": symbol,
        "simulate_only": mode == "sim",
        "read_only": read_only,
        "execution_mode": mode,
        "current_price": price,
        "bid": round(price - 0.4, 2),
        "ask": round(price + 0.4, 2),
        "percent_change_24h": -1.84 if stress else 2.31,
        "price_change_24h_pct": -1.84 if stress else 2.31,
        "high_24h": round(price * 1.015, 2),
        "low_24h": round(price * 0.985, 2),
        "total_equity_usdt": round(base_value_usdt + 4200.0, 2),
        "portfolio": {"USDT": 4200.0, symbol.replace("USDT", ""): round(base_value_usdt / price, 4)},
        "equity_breakdown": {
            "portfolio_total_usdt": round(base_value_usdt + 4200.0, 2),
            "usdt_balance_usdt": 4200.0,
            "base_asset": symbol.replace("USDT", ""),
            "base_quantity": round(base_value_usdt / price, 4),
            "base_value_usdt": round(base_value_usdt, 2),
            "unrealized_pnl_usdt": position["unrealized_pnl"],
            "unrealized_pnl_gross_usdt": position["unrealized_pnl_gross"],
            "estimated_total_fees_usdt": position["estimated_total_fees"],
            "symbol_exposure_usdt": round(base_value_usdt, 2),
        },
        "position": position,
        "indicators": {
            "cdc_zone_4h": "RED" if stress else "GREEN",
            "cdc_zone_4h_prev": "GREEN" if stress else "RED",
            "cdc_zone_4h_green_streak": 0 if stress else 5,
            "cdc_zone_4h_red_streak": 3 if stress else 0,
            "ema_fast_4h": round(price * (0.994 if stress else 1.004), 2),
            "ema_slow_4h": round(price * 1.0, 2),
            "ema_fast_4h_prev": round(price * 1.001, 2),
            "ema_slow_4h_prev": round(price * 1.0, 2),
            "ema_slow_4h_slope": -0.08 if stress else 0.12,
        },
        "strategy_name": "xauby_actionzone",
        "strategy_version": "1.4.0",
        "signal_meta": {
            "action": "SELL" if stress else "BUY",
            "reason": STRESS_STRINGS["reason"] if stress else "CDC zone flipped GREEN with confirmed slow EMA slope",
            "checklist": build_checklist(mix=stress),
            "status_summary": "Watching for zone flip" if stress else "Holding long, trailing stop active",
            "confidence": 0.42 if stress else 0.81,
            "intent": "reverse" if stress else "hold",
            "position_side": side,
            "timestamp": now_iso(),
            "strategy_name": "xauby_actionzone",
            "timeframe": TIMEFRAME,
            "metadata": {},
        },
        "risk": {
            "risk_pct": 0.03,
            "sl_atr_mult": 2.0,
            "trailing_atr_mult": 1.8,
            "breakeven_sl_enabled": False,
            "breakeven_activation_atr_mult": 1.5,
            "breakeven_buffer_atr_mult": 0.1,
            "use_d1_regime_filter": True,
            "require_fresh_zone": True,
            "fresh_zone_window": 3,
        },
        "macro_guard": build_macro_guard(blocking=macro_blocking, stress=stress),
        "last_log_message": "Position management: trailing stop updated",
        "recent_events": build_recent_events(stress),
        "regime": {
            "regime": STRESS_STRINGS["regime"] if stress else "BULL_TREND_STRONG",
            "trend": "BEARISH" if stress else "BULLISH",
            "volatility": "EXPANDING" if stress else "NORMAL",
            "macro_bias": "RISK-OFF" if stress else "RISK-ON",
            "confidence": 0.38 if stress else 0.86,
            "gold_score": 28 if stress else 78,
            "reasons": build_regime_reasons(stress),
            "phase": "DISTRIBUTION" if stress else "MARKUP",
            "risk_state": "DEFENSIVE" if stress else "NORMAL",
            "trend_strength": "STRONG",
            "volatility_state": "EXPANDING" if stress else "NORMAL",
            "liquidity_state": "THIN" if stress else "NORMAL",
            "transition_risk": "HIGH" if stress else "LOW",
            "strategy_bias": {
                "family": "trend_following",
                "posture": "cautious" if stress else "confident",
                "allowed_actions": ["HOLD", "CLOSE"] if stress else ["BUY", "HOLD", "ADD"],
                "preferred": ["xauby_actionzone"],
            },
            "features": {
                "ema_spread_pct": -0.62 if stress else 0.41,
                "atr_pct": 1.84,
                "atr_percentile": 0.72,
                "volume_ratio": 0.88 if stress else 1.24,
                "momentum_5_pct": -0.9 if stress else 0.6,
                "momentum_20_pct": -2.4 if stress else 1.8,
                "trend_quality": 0.31 if stress else 0.77,
                "macro_alignment": "CONFLICT" if stress else "ALIGNED",
                "institutional_confidence": 0.35 if stress else 0.74,
            },
        },
        "primary_timeframe": TIMEFRAME,
        "confirm_timeframe": "1d",
        "exchange": {
            "id": "okx",
            "name": "OKX",
            "provider": "ccxt",
            "market_type": "swap",
            "fees": {"maker": 0.0002, "taker": 0.0005, "source": "exchange"},
        },
        "degraded": stress,
        "degrade_reason": "WebSocket reconnecting, falling back to REST polling" if stress else "",
        "last_candle_timestamp": now_iso(),
        "regime_router": {
            "no_trade_state": "PANIC_SELL" if stress else "",
            "confirmed_regime": STRESS_STRINGS["regime"] if stress else "BULL_TREND_STRONG",
            "pending_regime": "",
            "warning": "Live routing not confirmed for this pair" if stress else "",
            "enabled": True,
            "live_confirmed": not stress,
        },
    }


def build_recent_events(stress: bool) -> List[Dict[str, Any]]:
    day0 = now_iso()
    day1 = "2026-07-12T14:20:00"
    events = [
        {"event_type": "tick_price_update", "ts": day1, "payload": {"symbol": SYMBOL, "price": 4108.2}},
        {"event_type": "signal_evaluated", "ts": day1, "payload": {"reason": "Checklist passed, awaiting confirmation", "action": "HOLD"}},
        {"event_type": "regime_changed", "ts": day1, "payload": {"regime": "BULL_TREND_WEAK", "reason": "Confidence rose above threshold"}},
        {"event_type": "risk_guard_rejected", "ts": day1, "payload": {"reason": "Max open positions reached", "symbol": SYMBOL}},
        {"event_type": "order_filled", "ts": day0, "payload": {"reason": "Entry limit filled", "price": 4115.3, "qty": 0.045}},
        {"event_type": "position_opened", "ts": day0, "payload": {"reason": "CDC zone flip GREEN", "symbol": SYMBOL, "price": 4115.3}},
        {"event_type": "position_closed", "ts": day0, "payload": {"reason": "Partial take-profit banked", "pnl": 18.42}},
        {
            "event_type": "risk_guard_rejected" if stress else "signal_evaluated",
            "ts": day0,
            "payload": {"reason": STRESS_STRINGS["reason"] if stress else "Trailing stop advanced", "symbol": SYMBOL},
        },
    ]
    return events


def build_state_json(stress: bool, focus_price: float) -> Dict[str, Any]:
    mode = "sim" if stress else "live"
    focus = build_symbol_snapshot(
        SYMBOL,
        price=focus_price,
        position_open=True,
        side="SHORT" if stress else "LONG",
        profitable=not stress,
        mode=mode,
        read_only=stress,
        macro_blocking=stress,
        stress=stress,
        base_value_usdt=1850.0,
    )
    by_symbol = {SYMBOL: focus}
    seed_prices = {"BTCUSDT": 61200.0, "ETHUSDT": 3380.0, "XAGUSDT": 29.4, "SOLUSDT": 142.0}
    for idx, sym in enumerate(PORTFOLIO_SYMBOLS):
        by_symbol[sym] = build_symbol_snapshot(
            sym,
            price=seed_prices[sym],
            position_open=False,
            side="LONG",
            profitable=True,
            mode=mode,
            read_only=stress,
            macro_blocking=False,
            stress=False,
            base_value_usdt=[640.0, 410.0, 260.0, 180.0][idx],
        )

    total_equity = round(focus["equity_breakdown"]["portfolio_total_usdt"] + sum(
        by_symbol[s]["equity_breakdown"]["base_value_usdt"] for s in PORTFOLIO_SYMBOLS
    ), 2)

    envelope: Dict[str, Any] = {
        "schema_version": 2,
        "focus_symbol": SYMBOL,
        "pairs": [SYMBOL] + PORTFOLIO_SYMBOLS,
        "aggregate": {
            "total_equity_usdt": total_equity,
            "open_positions": 1,
            "simulate_only": mode == "sim",
            "read_only": stress,
            "macro_guard": focus["macro_guard"],
        },
        "by_symbol": by_symbol,
    }
    envelope.update(focus)
    envelope["total_equity_usdt"] = total_equity
    return envelope


def build_prices_rows(bars: int = 90) -> List[Dict[str, Any]]:
    rng = random.Random(20260713)
    rows = []
    price = 4050.0
    ts = int(time.time()) - bars * 4 * 3600
    for i in range(bars):
        # Downtrend for the first 2/3, then a clean uptrend into a crossover
        # for the visible (last ~24) window so EMA12/EMA26 have warmed up.
        drift = -1.4 if i < bars * 0.55 else 2.1
        change = rng.gauss(drift, 6.0)
        open_p = price
        close_p = max(10.0, price + change)
        high_p = max(open_p, close_p) + abs(rng.gauss(2.0, 1.5))
        low_p = min(open_p, close_p) - abs(rng.gauss(2.0, 1.5))
        vol = abs(rng.gauss(1200, 300))
        rows.append({
            "symbol": SYMBOL,
            "timestamp": ts,
            "timeframe": TIMEFRAME,
            "open": round(open_p, 2),
            "high": round(high_p, 2),
            "low": round(low_p, 2),
            "close": round(close_p, 2),
            "volume": round(vol, 2),
        })
        price = close_p
        ts += 4 * 3600
    return rows


def build_closed_trades_rows() -> List[Dict[str, Any]]:
    base = int(time.time())
    day = 86400
    trades = [
        ("LONG", 4082.10, 4131.40, "take_profit", 22.4, "2026-07-10T02:00:00", "2026-07-10T14:00:00"),
        ("SHORT", 4145.00, 4098.50, "take_profit", 18.9, "2026-07-10T16:00:00", "2026-07-11T00:00:00"),
        ("LONG", 4110.00, 4076.20, "stop_loss", -14.2, "2026-07-11T04:00:00", "2026-07-11T09:00:00"),
        ("LONG", 4062.30, 4059.80, "manual", -1.1, "2026-07-11T10:00:00", "2026-07-11T10:45:00"),
        ("SHORT", 4130.00, 4171.60, "stop_loss", -19.8, "2026-07-11T15:00:00", "2026-07-12T02:00:00"),
        ("LONG", 4095.40, 4142.10, "take_profit", 21.3, "2026-07-12T06:00:00", "2026-07-12T18:00:00"),
        ("SHORT", 4150.20, 4103.90, "take_profit", 20.6, "2026-07-12T19:00:00", "2026-07-13T01:00:00"),
        ("LONG", 4108.00, 4111.50, "manual", 1.6, "2026-07-13T02:00:00", "2026-07-13T03:10:00"),
    ]
    rows = []
    for side, entry, exit_p, trigger, pnl, opened, closed in trades:
        amount = 0.045
        entry_cost = entry * amount
        gross_exit = exit_p * amount
        fees = round(abs(entry_cost) * 0.0006, 2)
        rows.append({
            "symbol": SYMBOL,
            "side": side,
            "amount": amount,
            "entry_price": entry,
            "exit_price": exit_p,
            "entry_cost": round(entry_cost, 2),
            "gross_exit": round(gross_exit, 2),
            "entry_fee": fees,
            "exit_fee": fees,
            "total_fees": round(fees * 2, 2),
            "net_pnl": pnl,
            "net_pnl_pct": round(pnl / entry_cost * 100, 2),
            "trigger": trigger,
            "opened_at": opened,
            "closed_at": closed,
            "entry_regime": "BULL_TREND_STRONG",
            "exit_regime": "BULL_TREND_WEAK",
            "strategy_name": "xauby_actionzone",
        })
    return rows


def write_state(project_root: Path, state: Dict[str, Any]) -> Path:
    path = project_root / "core" / "logs" / "xauby_bot_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
    return path


def write_db(project_root: Path, prices: List[Dict[str, Any]], trades: List[Dict[str, Any]]) -> Path:
    path = project_root / "core" / "xauby.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                timeframe TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                UNIQUE(symbol, timeframe, timestamp)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE closed_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                amount REAL NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                entry_cost REAL NOT NULL,
                gross_exit REAL NOT NULL,
                entry_fee REAL DEFAULT 0.0,
                exit_fee REAL DEFAULT 0.0,
                total_fees REAL DEFAULT 0.0,
                net_pnl REAL DEFAULT 0.0,
                net_pnl_pct REAL DEFAULT 0.0,
                trigger TEXT,
                opened_at TEXT,
                closed_at TEXT,
                entry_regime TEXT,
                exit_regime TEXT,
                strategy_name TEXT
            )
            """
        )
        for row in prices:
            conn.execute(
                "INSERT INTO prices (symbol, timestamp, timeframe, open, high, low, close, volume) "
                "VALUES (:symbol, :timestamp, :timeframe, :open, :high, :low, :close, :volume)",
                row,
            )
        for row in trades:
            conn.execute(
                "INSERT INTO closed_trades (symbol, side, amount, entry_price, exit_price, entry_cost, "
                "gross_exit, entry_fee, exit_fee, total_fees, net_pnl, net_pnl_pct, trigger, opened_at, "
                "closed_at, entry_regime, exit_regime, strategy_name) VALUES (:symbol, :side, :amount, "
                ":entry_price, :exit_price, :entry_cost, :gross_exit, :entry_fee, :exit_fee, :total_fees, "
                ":net_pnl, :net_pnl_pct, :trigger, :opened_at, :closed_at, :entry_regime, :exit_regime, "
                ":strategy_name)",
                row,
            )
        conn.commit()
    finally:
        conn.close()
    return path


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=["typical", "stress"], default="typical")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    args = parser.parse_args(argv)

    project_root = Path(args.project_root)
    stress = args.variant == "stress"

    prices = build_prices_rows()
    # Anchor the "live" price to the last generated candle's close so the
    # chart's current-price marker sits on the visible series instead of
    # floating off it.
    focus_price = prices[-1]["close"]
    state = build_state_json(stress=stress, focus_price=focus_price)
    trades = build_closed_trades_rows()

    state_path = write_state(project_root, state)
    db_path = write_db(project_root, prices, trades)

    print(f"[{args.variant}] wrote {state_path}")
    print(f"[{args.variant}] wrote {db_path} ({len(prices)} price rows, {len(trades)} trades)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
