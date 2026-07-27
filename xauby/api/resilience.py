"""Outbound-call resilience: token-bucket rate limiter + circuit breaker.

Ported (stdlib-only) from the Binance_Cryptonice ``core/rate_limiter.py`` and
``integrations/api_client.py`` CircuitBreaker. xAuby already lets CCXT throttle
itself via ``enable_rate_limit``; this adds an independent, exchange-agnostic
guard that (a) smooths bursts with a token bucket and (b) trips a circuit
breaker after consecutive failures so a flapping venue stops being hammered.

Gated by ``architecture.api_circuit_breaker_enabled`` (default off); when off,
:func:`build_guard` returns ``None`` and the call path is unchanged.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("xauby.api.resilience")


class CircuitBreakerOpen(RuntimeError):
    """Raised when a call is blocked because the circuit breaker is OPEN."""


class TokenBucketRateLimiter:
    """Thread-safe token bucket: ``rate`` tokens/sec, burst up to ``capacity``."""

    def __init__(self, rate: float = 10.0, capacity: int = 10, name: str = "default"):
        if float(rate) <= 0:
            raise ValueError("rate must be > 0")
        if int(capacity) <= 0:
            raise ValueError("capacity must be > 0")
        self.rate = float(rate)
        self.capacity = int(capacity)
        self.name = name
        self._tokens = float(capacity)
        self._last_update = time.time()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.time()
        elapsed = max(now - self._last_update, 0.0)  # guard NTP drift
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_update = now

    def acquire(self, tokens: int = 1, blocking: bool = True, timeout: Optional[float] = None) -> bool:
        if int(tokens) <= 0:
            raise ValueError("tokens must be > 0")
        if tokens > self.capacity:
            logger.warning("RateLimiter[%s] acquire(%s) exceeds capacity=%s", self.name, tokens, self.capacity)
            return False
        if timeout is not None and float(timeout) < 0:
            return False
        start = time.time()
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True
                if not blocking:
                    return False
                wait_for = (tokens - self._tokens) / self.rate
                if timeout is not None and (time.time() - start) + wait_for > timeout:
                    return False
                sleep_for = min(wait_for, 0.1)
            time.sleep(sleep_for)

    def try_acquire(self, tokens: int = 1) -> bool:
        return self.acquire(tokens, blocking=False)

    def available_tokens(self) -> float:
        with self._lock:
            self._refill()
            return self._tokens


class CircuitBreaker:
    """CLOSED → OPEN after ``failure_threshold`` failures; OPEN → HALF after
    ``recovery_timeout``; HALF → CLOSED on success, HALF → OPEN on failure."""

    CLOSED = "closed"
    OPEN = "open"
    HALF = "half"

    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 60.0, half_max_calls: int = 2):
        self.failure_threshold = int(failure_threshold)
        self.recovery_timeout = float(recovery_timeout)
        self.half_max_calls = int(half_max_calls)
        self._state = self.CLOSED
        self._failure_count = 0
        self._last_failure = 0.0
        self._half_calls = 0
        self._lock = threading.RLock()

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def is_available(self) -> bool:
        with self._lock:
            if self._state == self.CLOSED:
                return True
            if self._state == self.OPEN:
                if time.time() - self._last_failure >= self.recovery_timeout:
                    self._state = self.HALF
                    self._half_calls = 0
                    logger.warning("CircuitBreaker: OPEN -> HALF (testing recovery)")
                    return True
                return False
            # HALF: allow a limited number of probe calls
            if self._half_calls < self.half_max_calls:
                self._half_calls += 1
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            if self._state == self.HALF:
                self._state = self.CLOSED
                self._half_calls = 0
                logger.info("CircuitBreaker: HALF -> CLOSED (recovered)")
            elif self._state == self.OPEN:
                # Only a call that bypassed the gate (ResilienceGuard critical=True,
                # i.e. an order) can succeed while OPEN. Real traffic that worked is
                # stronger evidence of recovery than a HALF probe, and staying OPEN
                # would keep the engine blind to market data — on a pair with no
                # exchange-side stop, that is its own risk. Close and let the normal
                # failure count reopen it if the venue is still flapping.
                self._state = self.CLOSED
                self._half_calls = 0
                logger.info("CircuitBreaker: OPEN -> CLOSED (critical call succeeded)")

    def record_failure(self, error_msg: str = "") -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure = time.time()
            if self._state == self.HALF:
                self._state = self.OPEN
                logger.warning("CircuitBreaker: HALF -> OPEN (still failing)")
            elif self._failure_count >= self.failure_threshold:
                self._state = self.OPEN
                logger.warning("CircuitBreaker: CLOSED -> OPEN (%d consecutive failures)", self._failure_count)
            if error_msg:
                logger.debug("CircuitBreaker failure #%d: %s", self._failure_count, error_msg)

    def reset(self) -> None:
        with self._lock:
            self._state = self.CLOSED
            self._failure_count = 0
            self._half_calls = 0


class ResilienceGuard:
    """Wraps a callable with rate limiting + circuit breaking."""

    def __init__(
        self,
        rate: float = 10.0,
        capacity: int = 20,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
        acquire_timeout: float = 30.0,
        name: str = "exchange",
    ):
        self.limiter = TokenBucketRateLimiter(rate=rate, capacity=capacity, name=name)
        self.breaker = CircuitBreaker(failure_threshold=failure_threshold, recovery_timeout=recovery_timeout)
        self.acquire_timeout = float(acquire_timeout)

    def run(
        self,
        fn: Callable[..., Any],
        *args: Any,
        label: str = "",
        critical: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Run ``fn`` under rate limiting and circuit breaking.

        ``critical=True`` means "this call must not be blocked by *us*": it skips
        the breaker gate and never waits for a token. It still reports its
        outcome to the breaker, so breaker state stays accurate.

        This exists because the breaker sits on the same chokepoint as order
        placement (``CCXTClient._call``). A breaker that is OPEN for 60s while a
        venue flaps would also refuse the order that closes a position — and XAU
        runs with ``disable_stop_loss: true``, so the bot *is* the stop. Pausing
        a polling loop is protective; standing between the engine and an exit is
        not. The breaker guards the read loop; it never guards an order.
        """
        if not critical:
            if not self.breaker.is_available():
                raise CircuitBreakerOpen(
                    f"Circuit breaker OPEN for {label or 'exchange'} — calls paused until recovery."
                )
            if not self.limiter.acquire(blocking=True, timeout=self.acquire_timeout):
                # Don't trip the breaker on local throttling; just signal saturation.
                raise CircuitBreakerOpen(f"Rate limit timeout acquiring token for {label or 'exchange'}.")
        else:
            # Consume a token when one is free so critical traffic still counts
            # against the bucket, but never block waiting for it.
            self.limiter.acquire(blocking=False)
        try:
            result = fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - breaker must see every failure
            self.breaker.record_failure(str(e))
            raise
        self.breaker.record_success()
        return result


# CCXT methods that mutate orders. These are never gated by the breaker: they are
# rare, always urgent, and are not the source of the hammering the breaker exists
# to stop (that is the per-tick read loop). Blocking one of these can leave a
# live position unmanaged. Extendable via architecture.api_resilience.always_allow.
DEFAULT_ALWAYS_ALLOW = frozenset({"create_order", "cancel_order", "cancel_all_orders"})


def always_allow_methods(config: Dict[str, Any] | None) -> frozenset:
    """Method names that bypass the breaker gate, from defaults + config."""
    cfg = config or {}
    arch = cfg.get("architecture") or {}
    rc = arch.get("api_resilience")
    extra = (rc or {}).get("always_allow") if isinstance(rc, dict) else None
    if not extra:
        return DEFAULT_ALWAYS_ALLOW
    return DEFAULT_ALWAYS_ALLOW | {str(m) for m in extra}


def build_guard(config: Dict[str, Any] | None) -> Optional[ResilienceGuard]:
    """Build a :class:`ResilienceGuard` when the architecture flag is on, else None."""
    cfg = config or {}
    arch = cfg.get("architecture") or {}
    if not bool(arch.get("api_circuit_breaker_enabled", False)):
        return None
    rc = (arch.get("api_resilience") or {}) if isinstance(arch.get("api_resilience"), dict) else {}
    return ResilienceGuard(
        rate=float(rc.get("rate", 10.0)),
        capacity=int(rc.get("capacity", 20)),
        failure_threshold=int(rc.get("failure_threshold", 3)),
        recovery_timeout=float(rc.get("recovery_timeout", 60.0)),
        acquire_timeout=float(rc.get("acquire_timeout", 30.0)),
        name="exchange",
    )
