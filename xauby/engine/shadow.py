"""Candidate-isolated shadow execution primitives.

This module is deliberately not imported by the live tick loop.  A forward-SIM
worker can use it to evaluate several certified strategies against one shared
market context while guaranteeing that no candidate receives a broker or order
sink.  Keeping the fan-out out of :mod:`xauby.engine.loop` means enabling the
control-plane arena cannot change live-engine startup or latency.
"""
from __future__ import annotations

import copy
import math
import time
from dataclasses import dataclass
from typing import Any, Mapping

from xauby.strategies import load_strategy
from xauby.strategies.sandbox import StrategyRunner

MAX_SHADOW_CANDIDATES = 4


@dataclass(frozen=True)
class ShadowCandidateSpec:
    """The immutable, catalog-resolved inputs needed by one shadow runner."""

    candidate_id: str
    strategy_name: str
    strategy_config: Mapping[str, Any]
    timeout_seconds: float = 5.0


@dataclass(frozen=True)
class ShadowSignalRecord:
    """Serializable signal telemetry; it contains no executable order data."""

    candidate_id: str
    action: str
    intent: str
    position_side: str | None
    confidence: float
    reason: str
    produced_at: float
    duration_ms: float
    healthy: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "action": self.action,
            "intent": self.intent,
            "position_side": self.position_side,
            "confidence": self.confidence,
            "reason": self.reason,
            "produced_at": self.produced_at,
            "duration_ms": self.duration_ms,
            "healthy": self.healthy,
        }


@dataclass
class ShadowPosition:
    side: str
    quantity: float
    entry_price: float
    entry_fee: float
    opened_at: float


class ShadowVirtualLedger:
    """Small candidate-scoped mark-to-market ledger with no broker access."""

    def __init__(self, candidate_id: str, *, initial_cash: float = 100.0) -> None:
        if not str(candidate_id or "").strip():
            raise ValueError("candidate id is required")
        if float(initial_cash) <= 0 or not math.isfinite(float(initial_cash)):
            raise ValueError("initial cash must be positive")
        self.candidate_id = str(candidate_id)
        self.initial_cash = float(initial_cash)
        self.realized_pnl = 0.0
        self.fees = 0.0
        self.position: ShadowPosition | None = None
        self.last_price: float | None = None
        self.last_timestamp: float | None = None
        self.last_event = ""

    @staticmethod
    def _price(value: float) -> float:
        price = float(value)
        if price <= 0 or not math.isfinite(price):
            raise ValueError("shadow mark price must be positive")
        return price

    @staticmethod
    def _fee(notional: float, fee_pct: float) -> float:
        value = float(notional)
        fee = float(fee_pct)
        if value <= 0 or fee < 0 or not math.isfinite(value) or not math.isfinite(fee):
            raise ValueError("shadow notional and fee must be non-negative")
        return value * fee / 100.0

    def _unrealized(self, price: float) -> float:
        if self.position is None:
            return 0.0
        move = price - self.position.entry_price
        return move * self.position.quantity * (1.0 if self.position.side == "LONG" else -1.0)

    def _close(self, price: float, fee_pct: float, timestamp: float) -> str:
        if self.position is None:
            return "ignored_flat_close"
        gross = self._unrealized(price)
        exit_fee = self._fee(self.position.quantity * price, fee_pct)
        self.realized_pnl += gross - self.position.entry_fee - exit_fee
        self.fees += exit_fee
        self.position = None
        self.last_price = price
        self.last_timestamp = timestamp
        return "closed"

    def apply(
        self,
        signal: Any,
        *,
        symbol: str,
        price: float,
        notional: float,
        fee_pct: float = 0.0,
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        """Apply one signal to this candidate's virtual account only."""
        mark = self._price(price)
        amount = float(notional)
        if amount < 0 or not math.isfinite(amount):
            raise ValueError("shadow notional must be non-negative")
        now = float(timestamp if timestamp is not None else time.time())
        if not math.isfinite(now):
            raise ValueError("shadow timestamp must be finite")
        action = str(getattr(signal, "action", "HOLD") or "HOLD").upper()
        intent = str(getattr(signal, "intent", "HOLD") or "HOLD").upper()
        side = str(getattr(signal, "position_side", "LONG") or "LONG").upper()
        if action == "HOLD" or intent == "HOLD":
            event = "hold"
        elif intent == "CLOSE" or (action == "SELL" and side not in {"SHORT", "LONG"}):
            event = self._close(mark, fee_pct, now)
        else:
            if side not in {"LONG", "SHORT"}:
                side = "LONG" if action == "BUY" else "SHORT"
            if self.position is not None and self.position.side != side:
                self._close(mark, fee_pct, now)
            if self.position is None and amount > 0:
                quantity = amount / mark
                entry_fee = self._fee(amount, fee_pct)
                self.position = ShadowPosition(side, quantity, mark, entry_fee, now)
                self.fees += entry_fee
                event = "opened"
            else:
                event = "ignored_same_side" if self.position is not None else "ignored_zero_notional"
        self.last_price = mark
        self.last_timestamp = now
        self.last_event = event
        return self.snapshot(symbol=symbol)

    def snapshot(self, *, symbol: str) -> dict[str, Any]:
        mark = self.last_price or (self.position.entry_price if self.position else 0.0)
        unrealized = self._unrealized(mark) if mark > 0 else 0.0
        return {
            "candidate_id": self.candidate_id,
            "symbol": str(symbol).upper().replace("_", ""),
            "position": (
                {
                    "side": self.position.side,
                    "quantity": self.position.quantity,
                    "entry_price": self.position.entry_price,
                    "opened_at": self.position.opened_at,
                }
                if self.position
                else None
            ),
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": unrealized,
            "fees": self.fees,
            "last_event": self.last_event,
            # Entry fees are held against the open virtual position; once the
            # position closes they are included in realized_pnl exactly once.
            "equity": self.initial_cash + self.realized_pnl + unrealized - (
                self.position.entry_fee if self.position else 0.0
            ),
            "last_price": self.last_price,
            "last_timestamp": self.last_timestamp,
        }


class ShadowRunnerPool:
    """Run up to four candidate plugins as signal-only shadow workers.

    The pool is sequential by design: it bounds CPU fan-out on the 1-vCPU
    production host and makes a candidate timeout visible without allowing a
    worker to race the live broker.  It returns signals only; callers must feed
    those signals into a candidate-scoped virtual ledger.
    """

    def __init__(
        self,
        candidates: list[ShadowCandidateSpec],
        *,
        strict: bool = True,
        default_timeout_seconds: float = 5.0,
    ) -> None:
        if not candidates:
            raise ValueError("at least one shadow candidate is required")
        if len(candidates) > MAX_SHADOW_CANDIDATES:
            raise ValueError(f"at most {MAX_SHADOW_CANDIDATES} shadow candidates are supported")
        self._runners: list[tuple[ShadowCandidateSpec, StrategyRunner]] = []
        for candidate in candidates:
            candidate_id = str(candidate.candidate_id or "").strip()
            strategy_name = str(candidate.strategy_name or "").strip()
            if not candidate_id or not strategy_name:
                raise ValueError("shadow candidate id and strategy name are required")
            timeout = float(candidate.timeout_seconds or default_timeout_seconds)
            if timeout <= 0 or timeout > 30:
                raise ValueError("shadow timeout must be between 0 and 30 seconds")
            strategy = load_strategy(
                strategy_name,
                dict(candidate.strategy_config or {}),
                strict=strict,
            )
            self._runners.append(
                (
                    ShadowCandidateSpec(
                        candidate_id=candidate_id,
                        strategy_name=strategy_name,
                        strategy_config=dict(candidate.strategy_config or {}),
                        timeout_seconds=timeout,
                    ),
                    StrategyRunner(strategy, timeout=timeout, check_imports=True, strict=strict),
                )
            )

    def run(self, context: Any) -> list[ShadowSignalRecord]:
        """Evaluate every candidate against the same market snapshot.

        Contexts are copied when possible so a plugin cannot mutate the object
        observed by the next candidate.  A copy failure is safe to continue
        with the original read-only contract used by the strategy plugins.
        """
        records: list[ShadowSignalRecord] = []
        for spec, runner in self._runners:
            try:
                candidate_context = copy.deepcopy(context)
            except Exception:
                candidate_context = context
            started = time.monotonic()
            signal = runner.run(candidate_context)
            duration_ms = (time.monotonic() - started) * 1000.0
            records.append(
                ShadowSignalRecord(
                    candidate_id=spec.candidate_id,
                    action=str(signal.action),
                    intent=str(signal.intent),
                    position_side=signal.position_side,
                    confidence=float(signal.confidence),
                    reason=str(signal.reason or ""),
                    produced_at=float(signal.timestamp),
                    duration_ms=round(duration_ms, 3),
                    healthy=duration_ms <= spec.timeout_seconds * 1000.0
                    and str(signal.action) in {"BUY", "SELL", "HOLD"},
                )
            )
        return records
