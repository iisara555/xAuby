"""Tests for the optional exchange-call resilience guard."""
import time
import unittest

from xauby.api.resilience import (
    CircuitBreaker,
    CircuitBreakerOpen,
    ResilienceGuard,
    TokenBucketRateLimiter,
    build_guard,
)


class TestTokenBucket(unittest.TestCase):
    def test_burst_then_throttle(self):
        rl = TokenBucketRateLimiter(rate=1.0, capacity=3, name="t")
        self.assertTrue(rl.try_acquire())
        self.assertTrue(rl.try_acquire())
        self.assertTrue(rl.try_acquire())
        self.assertFalse(rl.try_acquire())  # bucket drained

    def test_refill_over_time(self):
        rl = TokenBucketRateLimiter(rate=100.0, capacity=2, name="t")
        self.assertTrue(rl.try_acquire())
        self.assertTrue(rl.try_acquire())
        self.assertFalse(rl.try_acquire())
        time.sleep(0.05)  # ~5 tokens refilled at 100/s
        self.assertTrue(rl.try_acquire())

    def test_request_above_capacity_rejected(self):
        rl = TokenBucketRateLimiter(rate=1.0, capacity=2)
        self.assertFalse(rl.acquire(tokens=5, blocking=False))


class TestCircuitBreaker(unittest.TestCase):
    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
        self.assertTrue(cb.is_available())
        for _ in range(3):
            cb.record_failure("boom")
        self.assertEqual(cb.state, CircuitBreaker.OPEN)
        self.assertFalse(cb.is_available())

    def test_half_open_recovery_to_closed(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
        cb.record_failure()
        cb.record_failure()
        self.assertEqual(cb.state, CircuitBreaker.OPEN)
        time.sleep(0.06)
        self.assertTrue(cb.is_available())  # transitions to HALF
        self.assertEqual(cb.state, CircuitBreaker.HALF)
        cb.record_success()
        self.assertEqual(cb.state, CircuitBreaker.CLOSED)

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05)
        cb.record_failure()
        time.sleep(0.06)
        self.assertTrue(cb.is_available())  # HALF
        cb.record_failure()
        self.assertEqual(cb.state, CircuitBreaker.OPEN)


class TestResilienceGuard(unittest.TestCase):
    def test_run_passes_through_and_counts(self):
        guard = ResilienceGuard(rate=1000, capacity=100)
        self.assertEqual(guard.run(lambda x: x + 1, 41, label="m"), 42)

    def test_breaker_blocks_after_failures(self):
        guard = ResilienceGuard(rate=1000, capacity=100, failure_threshold=2, recovery_timeout=60.0)

        def boom():
            raise ValueError("nope")

        for _ in range(2):
            with self.assertRaises(ValueError):
                guard.run(boom, label="boom")
        with self.assertRaises(CircuitBreakerOpen):
            guard.run(lambda: "ok", label="blocked")


class TestBuildGuard(unittest.TestCase):
    def test_disabled_returns_none(self):
        self.assertIsNone(build_guard({"architecture": {"api_circuit_breaker_enabled": False}}))
        self.assertIsNone(build_guard({}))

    def test_enabled_returns_guard(self):
        guard = build_guard({"architecture": {"api_circuit_breaker_enabled": True}})
        self.assertIsInstance(guard, ResilienceGuard)


if __name__ == "__main__":
    unittest.main()
