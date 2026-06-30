"""Per-symbol runtime state for multi-pair engine."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from xauby.runtime.pair_registry import PairSpec


@dataclass
class SymbolContext:
    """Mutable state isolated per trading pair."""

    symbol: str
    strategy_name: str = ""
    primary_timeframe: str = "4h"
    confirm_timeframe: Optional[str] = None
    use_d1_regime_filter: bool = False
    degraded: bool = False
    degrade_reason: str = ""

    latest_tick: Dict[str, Any] = field(default_factory=dict)
    market_data: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    feed_degraded: bool = False
    # Persistent safety halt (orphan position needing manual reconcile). Unlike
    # feed_degraded it is NOT cleared on WS reconnect — it requires an operator
    # to verify the exchange state, so new entries stay blocked until then.
    trading_halted: bool = False
    halt_reason: str = ""
    # Set each tick when the newest closed candle is too old (REST candle sync
    # failing while ticks still flow). Blocks new entries until candles refresh;
    # re-evaluated every tick so it self-clears — no manual reset needed.
    candle_stale: bool = False
    indicators_cache: Dict[str, Any] = field(default_factory=dict)
    last_signal_meta: Dict[str, Any] = field(default_factory=dict)
    current_regime: Any = None
    confirmed_regime: str = ""
    pending_regime: str = ""
    regime_debounce_count: int = 0
    no_trade_state: str = "ACTIVE"
    handoff_from_strategy: str = ""
    handoff_to_strategy: str = ""
    position_strategy: str = ""
    regime_router_warning: str = ""
    trailing_atr_mult: float = 2.0
    no_trade_candle_count: int = 0
    no_trade_recovery_count: int = 0
    chart_df_cache: Any = None
    last_candle_timestamp: int = 0
    # Timestamp of the closed candle the regime router last advanced on, so the
    # router/debounce advance once per candle instead of once per tick.
    _last_routed_candle_ts: int = 0
    _sl_breach_count: int = 0
    _last_regime_snapshot: Optional[Dict[str, Any]] = None
    _semi_auto_pending: Optional[Dict[str, Any]] = None
    _semi_auto_confirm: threading.Event = field(default_factory=threading.Event)
    _tick_lock: threading.Lock = field(default_factory=threading.Lock)
    _state_lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def from_spec(cls, spec: PairSpec) -> "SymbolContext":
        regime_tf = spec.regime_timeframe
        return cls(
            symbol=spec.symbol,
            strategy_name=spec.strategy_name,
            primary_timeframe=spec.primary_timeframe,
            confirm_timeframe=regime_tf,
            use_d1_regime_filter=bool(spec.use_d1_regime_filter),
            degraded=spec.degraded,
            degrade_reason=spec.degrade_reason,
            latest_tick={
                "symbol": spec.symbol,
                "last": 0.0,
                "bid": 0.0,
                "ask": 0.0,
                "percent_change_24h": 0.0,
                "timestamp": 0.0,
                "monotonic_ts": 0.0,
            },
        )

    def get_tick_snapshot(self) -> Dict[str, Any]:
        with self._tick_lock:
            return dict(self.latest_tick)

    def set_tick(self, tick: Dict[str, Any]) -> None:
        with self._tick_lock:
            self.latest_tick = dict(tick)

    def set_market_data(self, event: Dict[str, Any]) -> None:
        kind = str(event.get("kind") or "")
        if not kind:
            return
        with self._state_lock:
            self.market_data[kind] = dict(event)
            self.feed_degraded = False

    def market_data_snapshot(self) -> Dict[str, Dict[str, Any]]:
        with self._state_lock:
            return {k: dict(v) for k, v in self.market_data.items()}

    def set_feed_degraded(self, degraded: bool, reason: str = "") -> None:
        with self._state_lock:
            self.feed_degraded = bool(degraded)
            if degraded or reason or not self.degraded:
                self.degrade_reason = str(reason or "")

    def set_degrade_reason(self, reason: str) -> None:
        with self._state_lock:
            self.degrade_reason = str(reason or "")

    def feed_snapshot(self) -> Dict[str, Any]:
        with self._state_lock:
            return {
                "feed_degraded": bool(self.feed_degraded),
                "degrade_reason": self.degrade_reason,
                "trading_halted": bool(self.trading_halted),
                "halt_reason": self.halt_reason,
                "candle_stale": bool(self.candle_stale),
            }

    def candle_snapshot(self) -> Dict[str, Any]:
        with self._state_lock:
            return {
                "last_candle_timestamp": int(self.last_candle_timestamp or 0),
                "candle_stale": bool(self.candle_stale),
                "last_routed_candle_ts": int(self._last_routed_candle_ts or 0),
            }

    def set_candle_status(self, last_candle_timestamp: int, candle_stale: bool) -> bool:
        with self._state_lock:
            was_stale = bool(self.candle_stale)
            self.last_candle_timestamp = int(last_candle_timestamp or 0)
            self.candle_stale = bool(candle_stale)
            return was_stale

    def mark_routed_candle(self, last_candle_timestamp: int) -> None:
        with self._state_lock:
            self._last_routed_candle_ts = int(last_candle_timestamp or 0)

    def set_trading_halted(self, halted: bool, reason: str = "") -> None:
        with self._state_lock:
            self.trading_halted = bool(halted)
            self.halt_reason = str(reason or "")

    def has_semi_auto_pending(self) -> bool:
        with self._state_lock:
            return bool(self._semi_auto_pending)

    def set_semi_auto_pending(self, pending: Optional[Dict[str, Any]]) -> None:
        with self._state_lock:
            self._semi_auto_pending = dict(pending) if pending else None
            self._semi_auto_confirm.clear()

    def clear_semi_auto_pending(self) -> Optional[Dict[str, Any]]:
        with self._state_lock:
            pending = self._semi_auto_pending
            self._semi_auto_pending = None
            self._semi_auto_confirm.clear()
            return dict(pending) if pending else None

    def confirm_semi_auto_pending(self) -> bool:
        with self._state_lock:
            if not self._semi_auto_pending:
                return False
            self._semi_auto_confirm.set()
            return True

    def semi_auto_confirmed(self) -> bool:
        with self._state_lock:
            return self._semi_auto_confirm.is_set()

    def pop_confirmed_or_expired_semi_auto(self, now: float) -> tuple[str, Optional[Dict[str, Any]]]:
        with self._state_lock:
            pending = self._semi_auto_pending
            if not pending:
                self._semi_auto_confirm.clear()
                return "none", None
            if now > float(pending.get("expires_at", 0)):
                self._semi_auto_pending = None
                self._semi_auto_confirm.clear()
                return "expired", dict(pending)
            if self._semi_auto_confirm.is_set():
                self._semi_auto_pending = None
                self._semi_auto_confirm.clear()
                return "confirmed", dict(pending)
            return "pending", dict(pending)

    def blocks_new_entries(self) -> bool:
        """True when RegimeRouter forbids new BUY orders."""
        return self.no_trade_state in ("NO_TRADE_PENDING", "NO_TRADE", "HANDOFF")

    @property
    def timeframe_regime(self) -> Optional[str]:
        if not self.use_d1_regime_filter:
            return None
        return self.confirm_timeframe
