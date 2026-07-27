"""Arming the API circuit breaker (roadmap P0.7).

The limiter and breaker were written and tested but never armed. Arming them is
not just a flag flip, because `CCXTClient._call` is the single chokepoint for
*every* CCXT call — including `create_order`. A breaker that is OPEN for 60s
while a venue flaps would also refuse the order that closes a position, and XAU
runs with `disable_stop_loss: true`, so the bot is the stop.

The rule these tests pin: **the breaker guards the read loop, never an order.**
"""
from __future__ import annotations

import os
import unittest

import yaml

from xauby.api.resilience import (
    DEFAULT_ALWAYS_ALLOW,
    CircuitBreaker,
    CircuitBreakerOpen,
    ResilienceGuard,
    always_allow_methods,
    build_guard,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _guard(**kw) -> ResilienceGuard:
    kw.setdefault("failure_threshold", 2)
    kw.setdefault("recovery_timeout", 3600.0)  # stay OPEN for the test
    return ResilienceGuard(**kw)


def _trip(guard: ResilienceGuard) -> None:
    def boom():
        raise RuntimeError("venue down")

    for _ in range(guard.breaker.failure_threshold):
        with __import__("contextlib").suppress(RuntimeError):
            guard.run(boom, label="fetch_ticker")
    assert guard.breaker.state == CircuitBreaker.OPEN, "test setup failed to open the breaker"


class TestOrdersAreNeverBlocked(unittest.TestCase):
    def test_read_call_is_blocked_when_open(self):
        guard = _guard()
        _trip(guard)
        with self.assertRaises(CircuitBreakerOpen):
            guard.run(lambda: "ticker", label="fetch_ticker")

    def test_critical_call_still_goes_through_when_open(self):
        # The whole point: an exit order must not be refused locally.
        guard = _guard()
        _trip(guard)
        self.assertEqual(
            guard.run(lambda: "filled", label="create_order", critical=True), "filled"
        )

    def test_critical_call_records_success_and_closes_the_breaker(self):
        guard = _guard()
        _trip(guard)
        guard.run(lambda: "ok", label="create_order", critical=True)
        self.assertEqual(guard.breaker.state, CircuitBreaker.CLOSED)

    def test_critical_call_still_records_failure(self):
        # Bypassing the gate must not blind the breaker.
        guard = _guard(failure_threshold=1, recovery_timeout=3600.0)

        def boom():
            raise RuntimeError("rejected")

        with self.assertRaises(RuntimeError):
            guard.run(boom, label="create_order", critical=True)
        self.assertEqual(guard.breaker.state, CircuitBreaker.OPEN)

    def test_critical_call_never_waits_for_a_token(self):
        # capacity 1, drained: a non-critical call would block until timeout.
        guard = ResilienceGuard(rate=0.001, capacity=1, acquire_timeout=0.05)
        guard.run(lambda: 1, label="fetch_ticker")  # drains the bucket
        with self.assertRaises(CircuitBreakerOpen):
            guard.run(lambda: 2, label="fetch_ticker")
        self.assertEqual(guard.run(lambda: 3, label="create_order", critical=True), 3)


class TestAlwaysAllowSet(unittest.TestCase):
    def test_order_mutating_methods_are_exempt_by_default(self):
        for method in ("create_order", "cancel_order", "cancel_all_orders"):
            self.assertIn(method, DEFAULT_ALWAYS_ALLOW)

    def test_read_methods_are_not_exempt(self):
        for method in ("fetch_ticker", "fetch_ohlcv", "fetch_balance",
                       "fetch_positions", "load_markets"):
            self.assertNotIn(method, DEFAULT_ALWAYS_ALLOW)

    def test_config_can_extend_but_not_shrink(self):
        allowed = always_allow_methods(
            {"architecture": {"api_resilience": {"always_allow": ["edit_order"]}}}
        )
        self.assertIn("edit_order", allowed)
        self.assertTrue(DEFAULT_ALWAYS_ALLOW <= allowed)

    def test_defaults_when_unconfigured(self):
        self.assertEqual(always_allow_methods(None), DEFAULT_ALWAYS_ALLOW)
        self.assertEqual(always_allow_methods({}), DEFAULT_ALWAYS_ALLOW)


class TestFlagWiring(unittest.TestCase):
    def test_guard_is_none_when_disabled(self):
        self.assertIsNone(build_guard({"architecture": {"api_circuit_breaker_enabled": False}}))

    def test_guard_reads_tuning_from_config(self):
        guard = build_guard({
            "architecture": {
                "api_circuit_breaker_enabled": True,
                "api_resilience": {"rate": 5.0, "capacity": 7, "failure_threshold": 9},
            }
        })
        self.assertIsNotNone(guard)
        self.assertEqual(guard.limiter.rate, 5.0)
        self.assertEqual(guard.limiter.capacity, 7)
        self.assertEqual(guard.breaker.failure_threshold, 9)


class TestShippedConfig(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(REPO_ROOT, "bot_config.yaml"), encoding="utf-8") as fh:
            self.cfg = yaml.safe_load(fh)

    def test_breaker_is_armed(self):
        arch = self.cfg.get("architecture") or {}
        self.assertTrue(arch.get("api_circuit_breaker_enabled"),
                        "P0.7 arms the breaker; flipping this back off is the rollback")

    def test_shipped_config_builds_a_guard(self):
        self.assertIsNotNone(build_guard(self.cfg))

    def test_shipped_tuning_leaves_headroom_for_the_tick_loop(self):
        # The engine ticks every 60s over 2 pairs. If the sustained rate were
        # ever tuned below that, the limiter would throttle normal operation
        # rather than only reconnect storms.
        guard = build_guard(self.cfg)
        self.assertGreaterEqual(guard.limiter.rate, 1.0)
        self.assertGreaterEqual(guard.limiter.capacity, 5)

    def test_orders_remain_exempt_under_shipped_config(self):
        self.assertIn("create_order", always_allow_methods(self.cfg))


if __name__ == "__main__":
    unittest.main()
