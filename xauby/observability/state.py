"""Centralized bot state export for dashboard and health checks."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from xauby.utils.atomic_io import atomic_json_write
from xauby.runtime.paths import bot_state_path


class StateExporter:
    """Build and write the bot state JSON (``<runtime>/logs/xauby_bot_state.json``)."""

    def __init__(self, path: Optional[str] = None, indent: Optional[int] = 2):
        self.path = path or bot_state_path()
        self.indent = indent

    def build(
        self,
        *,
        pid: int,
        engine_started_at: str,
        symbol: str,
        simulate_only: bool,
        read_only: bool,
        interval_seconds: int,
        current_price: float,
        bid: float,
        ask: float,
        percent_change_24h: float,
        equity: float,
        portfolio: Optional[Dict[str, float]],
        position: Dict[str, Any],
        indicators: Dict[str, Any],
        strategy_name: str,
        strategy_version: str,
        signal_meta: Dict[str, Any],
        risk: Dict[str, Any],
        macro_guard: Dict[str, Any],
        last_log_message: str,
        api_latency_ms: int = 0,
        clock_offset_seconds: float = 0.0,
        run_id: str = "",
        recent_events: Optional[list] = None,
        regime: Optional[Dict[str, Any]] = None,
        latency: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        latency_block = dict(latency or {})
        latency_block.setdefault("api_latency_ms", api_latency_ms)
        return {
            "pid": pid,
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "engine_started_at": engine_started_at,
            "api_latency_ms": api_latency_ms,
            "clock_offset_seconds": clock_offset_seconds,
            "latency": latency_block,
            "interval_seconds": interval_seconds,
            "symbol": symbol,
            "simulate_only": simulate_only,
            "read_only": read_only,
            "current_price": current_price,
            "bid": bid,
            "ask": ask,
            "percent_change_24h": percent_change_24h,
            "price_change_24h_pct": percent_change_24h,
            "total_equity_usdt": equity,
            "portfolio": portfolio,
            "position": position,
            "indicators": indicators,
            "strategy_name": strategy_name,
            "strategy_version": strategy_version,
            "signal_meta": signal_meta,
            "risk": risk,
            "macro_guard": macro_guard,
            "last_log_message": last_log_message,
            "recent_events": list(recent_events or [])[-20:],
            "regime": regime or {
                "regime": "UNKNOWN",
                "trend": "NEUTRAL",
                "volatility": "NORMAL",
                "macro_bias": "NEUTRAL",
                "confidence": 0.5,
                "gold_score": 50,
                "reasons": [],
            },
        }

    def write(self, snapshot: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        atomic_json_write(self.path, snapshot, indent=self.indent)
