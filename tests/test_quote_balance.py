"""Tests for quote-asset-aware live balance reads (item 2 final edge)."""

import unittest

from xauby.engine.brokers.live_broker import LiveBroker


class _FakeClient:
    def __init__(self, balances):
        self._balances = balances

    def get_balances(self):
        return self._balances


class TestLiveBrokerQuoteBalance(unittest.TestCase):
    def test_reads_configured_quote_asset(self):
        client = _FakeClient({"THB": {"available": 500.0, "reserved": 10.0}})
        broker = LiveBroker(client, lambda *a, **k: None, quote_asset="THB")
        self.assertEqual(broker.get_quote_balance(), 500.0)

    def test_default_quote_is_usdt(self):
        client = _FakeClient({"USDT": {"available": 250.0}})
        broker = LiveBroker(client, lambda *a, **k: None)
        self.assertEqual(broker.get_quote_balance(), 250.0)
        self.assertEqual(broker.quote_asset, "USDT")

    def test_usdt_alias_matches_quote_balance(self):
        client = _FakeClient({"USDC": {"available": 42.0}})
        broker = LiveBroker(client, lambda *a, **k: None, quote_asset="usdc")
        self.assertEqual(broker.get_usdt_balance(), broker.get_quote_balance())
        self.assertEqual(broker.get_usdt_balance(), 42.0)

    def test_missing_quote_returns_zero(self):
        client = _FakeClient({"BTC": {"available": 1.0}})
        broker = LiveBroker(client, lambda *a, **k: None, quote_asset="THB")
        self.assertEqual(broker.get_quote_balance(), 0.0)

    def test_debit_checks_quote_balance(self):
        client = _FakeClient({"THB": {"available": 500.0}})
        broker = LiveBroker(client, lambda *a, **k: None, quote_asset="THB")
        self.assertTrue(broker.debit_usdt(400.0))
        self.assertFalse(broker.debit_usdt(600.0))


class TestEngineQuoteAssetResolver(unittest.TestCase):
    """_quote_asset() prefers the whitelist registry, then config, default USDT."""

    def _resolve(self, config, registry_quote):
        from xauby.engine.base import BaseEngine

        stub = type("E", (), {})()
        stub.config = config
        stub._pair_registry = type("R", (), {"quote_asset": registry_quote})()
        return BaseEngine._quote_asset(stub)

    def test_registry_quote_wins(self):
        self.assertEqual(self._resolve({"exchange": {"quote_asset": "USDT"}}, "THB"), "THB")

    def test_config_fallback(self):
        self.assertEqual(self._resolve({"exchange": {"quote_asset": "thb"}}, None), "THB")

    def test_default_usdt(self):
        self.assertEqual(self._resolve({}, None), "USDT")


if __name__ == "__main__":
    unittest.main()
