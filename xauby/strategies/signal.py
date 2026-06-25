"""Standard signal contract returned by every Strategy.

The Engine ONLY consumes this object. It is intentionally engine-agnostic
and indicator-agnostic — strategies decide what to put inside, but the engine
will only act on a small, well-known surface.
"""
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from xauby.domain.models import PositionSide, TradeIntent


# Allowed actions returned by a strategy. Kept as plain strings so they are
# trivially serialisable (JSON, DB, dashboard) without an enum import.
ACTION_BUY = "BUY"
ACTION_SELL = "SELL"
ACTION_HOLD = "HOLD"
VALID_ACTIONS = (ACTION_BUY, ACTION_SELL, ACTION_HOLD)


@dataclass
class Signal:
    """Standard signal returned by every strategy.

    Required fields:
        action: One of "BUY" / "SELL" / "HOLD".
        reason: Human-readable explanation (used in logs / Telegram / dashboard).

    Provenance (auto-populated by StrategyRunner / engine when left at defaults):
        timestamp:         UTC epoch seconds when the signal was produced.
        strategy_name:     Plugin id that produced this signal.
        timeframe:         Primary timeframe used for analysis.

    Risk hints (consumed by Engine if present, otherwise Engine falls back
    to its own defaults):
        confidence:        0.0–1.0 score from the strategy.
        stop_loss_price:   Absolute SL level recommended by the strategy.
                           Engine uses this *instead of* ATR-based SL when set.
        stop_loss_distance: Distance (in price units) for SL placement.
        trail_distance:    Distance for trailing stop. Engine uses this
                           *instead of* trailing_atr_mult when set.
        volatility:        Generic volatility metric (e.g. ATR). Engine may
                           use this for position sizing / trailing if no
                           explicit stop_loss_* is provided.

    Diagnostics (engine logs / dashboard, never affects execution logic):
        indicators:        Raw indicator snapshot (for debugging / UI).
        status_summary:    Short single-line status the engine prints in
                           periodic heartbeats (strategy-defined format).
        checklist:         Structured items the dashboard renders verbatim,
                           so each strategy describes its own setup gates
                           without the dashboard knowing about indicators.
                           Each item is a dict, supported shapes:

                               {"label": str, "value": Any,
                                "ok": bool, "hint": str (optional)}

                           Optional ``bar`` key for visual progress:
                               "bar": {"type": "range",
                                       "min": float, "max": float,
                                       "low": float, "high": float,
                                       "value": float}
                               "bar": {"type": "progress",
                                       "max": float,
                                       "threshold": float,
                                       "value": float}
        metadata:          Free-form bag for anything else (backtests, ML, …).
    """

    action: str
    reason: str = ""
    confidence: float = 0.0

    timestamp: float = field(default_factory=_time.time)
    strategy_name: str = ""
    timeframe: str = ""

    stop_loss_price: Optional[float] = None
    stop_loss_distance: Optional[float] = None
    trail_distance: Optional[float] = None
    volatility: Optional[float] = None

    indicators: Dict[str, Any] = field(default_factory=dict)
    status_summary: str = ""
    checklist: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    intent: Optional[str] = None
    position_side: Optional[str] = None

    def __post_init__(self) -> None:
        if self.action not in VALID_ACTIONS:
            raise ValueError(
                f"Signal.action must be one of {VALID_ACTIONS}, got {self.action!r}"
            )
        if not (0.0 <= float(self.confidence) <= 1.0):
            self.confidence = max(0.0, min(1.0, float(self.confidence)))
        if self.intent is None:
            self.intent = (
                TradeIntent.OPEN.value if self.action == ACTION_BUY
                else TradeIntent.CLOSE.value if self.action == ACTION_SELL
                else TradeIntent.HOLD.value
            )
        self.intent = str(self.intent).upper()
        if self.intent not in {item.value for item in TradeIntent}:
            raise ValueError(f"Invalid trade intent: {self.intent!r}")
        if self.position_side is None and self.intent != TradeIntent.HOLD.value:
            self.position_side = PositionSide.LONG.value
        if self.position_side is not None:
            self.position_side = str(self.position_side).upper()
            if self.position_side not in {item.value for item in PositionSide}:
                raise ValueError(f"Invalid position side: {self.position_side!r}")

    @property
    def is_short(self) -> bool:
        return self.position_side == PositionSide.SHORT.value


def hold(reason: str, **kwargs: Any) -> Signal:
    """Convenience constructor for the common HOLD case."""
    return Signal(action=ACTION_HOLD, reason=reason, **kwargs)


def buy(reason: str, **kwargs: Any) -> Signal:
    return Signal(action=ACTION_BUY, reason=reason, **kwargs)


def sell(reason: str, **kwargs: Any) -> Signal:
    return Signal(action=ACTION_SELL, reason=reason, **kwargs)


def open_short(reason: str, **kwargs: Any) -> Signal:
    return Signal(
        action=ACTION_SELL,
        intent=TradeIntent.OPEN.value,
        position_side=PositionSide.SHORT.value,
        reason=reason,
        **kwargs,
    )


def close_short(reason: str, **kwargs: Any) -> Signal:
    return Signal(
        action=ACTION_BUY,
        intent=TradeIntent.CLOSE.value,
        position_side=PositionSide.SHORT.value,
        reason=reason,
        **kwargs,
    )
