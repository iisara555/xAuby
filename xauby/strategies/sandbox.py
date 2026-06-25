"""Strategy sandbox runner.

Wraps a :class:`Strategy` instance to enforce the "pure analyst" contract
at runtime:

* **Timeout** — ``analyze()`` must complete within a configurable limit.
* **Exception isolation** — a crashing strategy returns ``HOLD`` instead
  of propagating up to the engine loop.
* **Signal validation** — returned objects are checked for invariants.
* **Performance logging** — wall-clock time per call is recorded.
* **Import guard** — optionally checks that the strategy module does not
  import forbidden packages (side-effect-producing libraries).
"""
from __future__ import annotations

import logging
import math
import numbers
import sys
import threading
import time
import types
from typing import Any, List

from xauby.strategies.base import Strategy
from xauby.strategies.context import MarketContext
from xauby.strategies.sandbox_scan import scan_strategy
from xauby.strategies.signal import VALID_ACTIONS, Signal, hold

logger = logging.getLogger("xauby.strategies.sandbox")


class StrategySandboxError(Exception):
    """Raised when a strategy fails the static sandbox scan in strict mode."""

FORBIDDEN_IMPORTS: frozenset[str] = frozenset({
    "requests",
    "httpx",
    "urllib3",
    "websocket",
    "sqlalchemy",
    "sqlite3",
    "telegram",
})


class StrategyRunner:
    """Wraps a Strategy to enforce the plugin contract at runtime.

    Parameters:
        strategy:         The strategy instance to wrap.
        timeout:          Max seconds for a single ``analyze()`` call.
        check_imports:    If ``True``, statically scan the strategy's source for
                          forbidden capabilities at construction time.
        strict:           If ``True``, scan violations raise
                          :class:`StrategySandboxError` instead of just warning
                          (use for untrusted/marketplace plugins).
    """

    def __init__(
        self,
        strategy: Strategy,
        timeout: float = 5.0,
        check_imports: bool = True,
        strict: bool = False,
    ) -> None:
        self.strategy = strategy
        self.timeout = timeout
        self.strict = strict
        self.last_duration_ms: float = 0.0

        if check_imports:
            violations = scan_strategy(strategy)
            if violations:
                header = (
                    f"Strategy {strategy.name!r} (module "
                    f"{type(strategy).__module__}) failed the sandbox scan:"
                )
                detail = "; ".join(str(v) for v in violations)
                if strict:
                    raise StrategySandboxError(f"{header} {detail}")
                logger.warning("%s %s", header, detail)

    # ------------------------------------------------------------------ #
    # Main entry point                                                   #
    # ------------------------------------------------------------------ #
    def run(self, ctx: MarketContext) -> Signal:
        """Execute ``strategy.analyze(ctx)`` inside the sandbox.

        On timeout or exception a safe ``HOLD`` signal is returned so the
        engine loop is never interrupted.
        """
        result: List[Signal] = []
        error: List[Exception] = []

        def _target() -> None:
            try:
                result.append(self.strategy.analyze(ctx))
            except Exception as exc:
                error.append(exc)

        t0 = time.monotonic()
        worker = threading.Thread(target=_target, daemon=True)
        worker.start()
        worker.join(timeout=self.timeout)
        elapsed = time.monotonic() - t0
        self.last_duration_ms = elapsed * 1000

        if worker.is_alive():
            logger.error(
                f"Strategy {self.strategy.name!r} timed out after "
                f"{self.timeout:.1f}s — returning HOLD"
            )
            return self._safe_hold(
                f"strategy timeout ({self.timeout:.1f}s)", ctx
            )

        if error:
            logger.exception(
                f"Strategy {self.strategy.name!r} raised: {error[0]}",
                exc_info=error[0],
            )
            return self._safe_hold(f"strategy error: {error[0]}", ctx)

        if not result:
            return self._safe_hold("strategy returned no result", ctx)

        signal = result[0]
        if not isinstance(signal, Signal):
            logger.error(
                "Strategy %r returned %s instead of Signal — returning HOLD",
                self.strategy.name,
                type(signal).__name__,
            )
            return self._safe_hold(
                f"strategy returned invalid type: {type(signal).__name__}", ctx
            )
        signal = self._stamp(signal, ctx)
        signal = self._sanitize(signal)
        issues = self._validate(signal)
        if issues:
            for issue in issues:
                logger.warning(
                    f"[{self.strategy.name}] signal validation: {issue}"
                )

        if elapsed > 1.0:
            logger.warning(
                f"Strategy {self.strategy.name!r} took {elapsed:.2f}s "
                f"(threshold 1.0s)"
            )

        return signal

    # ------------------------------------------------------------------ #
    # Provenance stamping                                                #
    # ------------------------------------------------------------------ #
    def _stamp(self, signal: Signal, ctx: MarketContext) -> Signal:
        """Fill provenance fields if the strategy left them at defaults."""
        if not signal.strategy_name:
            signal.strategy_name = self.strategy.name
        if not signal.timeframe:
            signal.timeframe = ctx.timeframe_primary
        return signal

    @classmethod
    def _sanitize_value(cls, value: Any) -> Any:
        """Return a JSON-safe equivalent for nested strategy diagnostics."""
        if value is None or isinstance(value, (str, bool)):
            return value
        if isinstance(value, numbers.Integral):
            return int(value)
        if isinstance(value, numbers.Real):
            numeric = float(value)
            return numeric if math.isfinite(numeric) else 0.0
        if isinstance(value, dict):
            return {str(k): cls._sanitize_value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._sanitize_value(v) for v in value]
        return value

    @classmethod
    def _sanitize(cls, signal: Signal) -> Signal:
        """Enforce finite numeric values at the strategy/engine boundary."""
        confidence = cls._sanitize_value(signal.confidence)
        signal.confidence = float(confidence) if confidence is not None else 0.0
        timestamp = cls._sanitize_value(signal.timestamp)
        signal.timestamp = float(timestamp) if timestamp else time.time()
        for field_name in (
            "stop_loss_price",
            "stop_loss_distance",
            "trail_distance",
            "volatility",
        ):
            current = getattr(signal, field_name)
            if current is None:
                continue
            numeric = cls._sanitize_value(current)
            setattr(signal, field_name, float(numeric) if numeric is not None else None)
        signal.indicators = cls._sanitize_value(signal.indicators)
        signal.checklist = cls._sanitize_value(signal.checklist)
        signal.metadata = cls._sanitize_value(signal.metadata)
        return signal

    # ------------------------------------------------------------------ #
    # Validation                                                         #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _validate(signal: Signal) -> List[str]:
        issues: List[str] = []
        if signal.action not in VALID_ACTIONS:
            issues.append(
                f"action {signal.action!r} not in {VALID_ACTIONS}"
            )
        if not (0.0 <= signal.confidence <= 1.0):
            issues.append(
                f"confidence {signal.confidence} out of [0, 1]"
            )
        if signal.stop_loss_price is not None and signal.stop_loss_price < 0:
            issues.append(
                f"stop_loss_price {signal.stop_loss_price} is negative"
            )
        if signal.trail_distance is not None and signal.trail_distance < 0:
            issues.append(
                f"trail_distance {signal.trail_distance} is negative"
            )
        return issues

    # ------------------------------------------------------------------ #
    # Import guard                                                       #
    # ------------------------------------------------------------------ #
    def check_forbidden_imports(self) -> List[str]:
        """Check the strategy's module for direct imports of forbidden packages.

        Inspects only module-level attributes of the strategy's own module,
        so libraries loaded elsewhere in the process (e.g. ``requests`` used
        by the engine's Telegram worker) are NOT flagged.
        """
        result: List[str] = []
        mod_name = type(self.strategy).__module__
        mod = sys.modules.get(mod_name)
        if mod is None:
            return result

        seen: set[str] = set()
        for attr in vars(mod).values():
            if not isinstance(attr, types.ModuleType):
                continue
            top = attr.__name__.split(".")[0]
            if top in FORBIDDEN_IMPORTS and top not in seen:
                seen.add(top)
                result.append(
                    f"Strategy {self.strategy.name!r} (module {mod_name}) "
                    f"directly imports forbidden package {top!r}. "
                    f"Strategies should not use network/DB libraries."
                )
        return result

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #
    def _safe_hold(self, reason: str, ctx: MarketContext) -> Signal:
        return hold(
            reason,
            strategy_name=self.strategy.name,
            timeframe=ctx.timeframe_primary,
        )
