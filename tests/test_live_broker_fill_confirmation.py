"""LiveBroker must confirm a swap MARKET close/open actually filled before
reporting success — otherwise a stop-and-reverse can flip local state to the
new side while the exchange never closed/opened the position, producing a
local/exchange position-side mismatch (see reconcile.py's mismatch guard)."""

import unittest

from xauby.domain.models import Order
from xauby.engine.brokers.live_broker import LiveBroker


def _order(status, order_id="ord-1", price=100.0):
    return Order(
        order_id=order_id, client_id="", symbol="XAUUSDT", side="SELL",
        order_type="MARKET", price=price, amount=1.0, status=status,
        raw_payload={"executedQty": 1.0 if status == "FILLED" else 0.0, "filled": 1.0 if status == "FILLED" else 0.0},
    )


def _order_without_raw_payload(status, order_id="ord-1", price=100.0, amount=1.0):
    """Some IExchangeGateway implementations (the test mock included) only
    populate Order.amount on a terminal status and leave raw_payload unset."""
    return Order(
        order_id=order_id, client_id="", symbol="XAUUSDT", side="SELL",
        order_type="MARKET", price=price, amount=amount, status=status,
    )


def _contract_order(status, order_id="ord-contract", price=3991.1):
    contracts = 43.0 if status == "FILLED" else 0.0
    return Order(
        order_id=order_id, client_id="", symbol="XAUUSDT", side="SELL",
        order_type="MARKET", price=price, amount=43.0, status=status,
        raw_payload={
            "executedQty": contracts,
            "filled": contracts,
            "cost": contracts * 0.001 * price,
        },
    )


def _partial_contract_order(status, order_id="ord-partial", price=3991.1):
    contracts = 20.0
    return Order(
        order_id=order_id, client_id="", symbol="XAUUSDT", side="SELL",
        order_type="MARKET", price=price, amount=43.0, status=status,
        raw_payload={
            "executedQty": contracts,
            "filled": contracts,
            "cost": contracts * 0.001 * price,
        },
    )


class _FakeClient:
    def __init__(self, place_status, poll_statuses=None, order_factory=_order):
        self._place_status = place_status
        self._poll_statuses = list(poll_statuses or [])
        self._order_factory = order_factory
        self.get_order_calls = 0

    def place_order(self, *args, **kwargs):
        return self._order_factory(self._place_status)

    def get_order(self, symbol, order_id):
        self.get_order_calls += 1
        status = self._poll_statuses.pop(0) if self._poll_statuses else "NEW"
        filled = 1.0 if status == "FILLED" else 0.0
        return {"status": status, "executedQty": filled, "filled": filled}


def _broker(client):
    return LiveBroker(
        client, lambda *a, **k: None, quote_asset="USDT",
        order_timeout_seconds=1.0, order_poll_initial_seconds=0.01, order_poll_max_seconds=0.02,
    )


class TestLiveBrokerFillConfirmation(unittest.TestCase):
    def test_close_already_filled_skips_polling(self):
        client = _FakeClient(place_status="FILLED")
        result = _broker(client).execute_close("XAUUSDT", "LONG", 1.0, 100.0, 90.0, 90.0)
        self.assertTrue(result.success)
        self.assertEqual(result.qty, 1.0)
        self.assertEqual(client.get_order_calls, 0)

    def test_close_confirms_via_poll_then_succeeds(self):
        client = _FakeClient(place_status="NEW", poll_statuses=["NEW", "FILLED"])
        result = _broker(client).execute_close("XAUUSDT", "LONG", 1.0, 100.0, 90.0, 90.0)
        self.assertTrue(result.success)
        self.assertGreaterEqual(client.get_order_calls, 1)

    def test_close_never_fills_reports_failure_not_success(self):
        client = _FakeClient(place_status="NEW", poll_statuses=[])
        result = _broker(client).execute_close("XAUUSDT", "LONG", 1.0, 100.0, 90.0, 90.0)
        self.assertFalse(result.success)
        self.assertIn("not confirmed filled", result.error)

    def test_open_never_fills_reports_failure_not_success(self):
        client = _FakeClient(place_status="NEW", poll_statuses=[])
        result = _broker(client).execute_open("XAUUSDT", "SHORT", 1.0, 100.0, 100.0)
        self.assertFalse(result.success)
        self.assertIn("not confirmed filled", result.error)

    def test_open_already_filled_skips_polling(self):
        client = _FakeClient(place_status="FILLED")
        result = _broker(client).execute_open("XAUUSDT", "SHORT", 1.0, 100.0, 100.0)
        self.assertTrue(result.success)
        self.assertEqual(client.get_order_calls, 0)

    def test_close_filled_without_raw_payload_uses_order_amount(self):
        """Regression: a gateway that reports status=FILLED via Order.amount
        only (no raw_payload executedQty) must not be misread as unfilled."""
        client = _FakeClient(place_status="FILLED", order_factory=_order_without_raw_payload)
        result = _broker(client).execute_close("XAUUSDT", "LONG", 1.0, 100.0, 90.0, 90.0)
        self.assertTrue(result.success)
        self.assertEqual(result.qty, 1.0)
        self.assertEqual(client.get_order_calls, 0)

    def test_swap_close_converts_contract_fill_to_base_quantity(self):
        client = _FakeClient(place_status="FILLED", order_factory=_contract_order)
        result = _broker(client).execute_close(
            "XAUUSDT", "LONG", 0.043, 3991.1, 3989.8, 171.5614
        )
        self.assertTrue(result.success)
        self.assertAlmostEqual(result.qty, 0.043)
        self.assertAlmostEqual(result.price, 3991.1)

    def test_swap_open_converts_contract_fill_to_base_quantity(self):
        client = _FakeClient(place_status="FILLED", order_factory=_contract_order)
        result = _broker(client).execute_open(
            "XAUUSDT", "SHORT", 0.043, 3991.1, 171.6173
        )
        self.assertTrue(result.success)
        self.assertAlmostEqual(result.qty, 0.043)
        self.assertAlmostEqual(result.price, 3991.1)

    def test_canceled_partial_swap_close_preserves_fill_fraction(self):
        client = _FakeClient(place_status="CANCELED", order_factory=_partial_contract_order)
        result = _broker(client).execute_close(
            "XAUUSDT", "LONG", 0.043, 3991.1, 3989.8, 171.5614
        )
        self.assertTrue(result.success)
        self.assertAlmostEqual(result.qty, 0.020)
        self.assertAlmostEqual(result.price, 3991.1)


if __name__ == "__main__":
    unittest.main()
