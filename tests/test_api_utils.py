"""Tests for exchange-neutral gateway helpers and the gateway contract."""

import unittest

from xauby.api.interface import IExchangeGateway
from xauby.api.utils import (
    DEFAULT_SYMBOL_FILTERS,
    find_symbol_entry,
    make_client_id,
    parse_exchange_filters,
    round_step,
)


_EXCHANGE_INFO = {
    "symbols": [
        {
            "symbol": "BTCUSDT",
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "baseAssetPrecision": 6,
            "quoteAssetPrecision": 2,
            "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                {"filterType": "NOTIONAL", "minNotional": "5"},
            ],
        }
    ]
}


class TestMakeClientId(unittest.TestCase):
    def test_prefix_sanitized_and_unique(self):
        a = make_client_id("buy!!")
        b = make_client_id("buy!!")
        self.assertTrue(a.startswith("xauby-buy-"))
        self.assertNotEqual(a, b)

    def test_interface_static_matches_module(self):
        cid = IExchangeGateway.make_client_id("sell")
        self.assertTrue(cid.startswith("xauby-sell-"))


class TestRoundStep(unittest.TestCase):
    def test_rounds_down_to_step(self):
        self.assertEqual(round_step(0.011288, "0.0001"), 0.0112)

    def test_none_step_returns_value(self):
        self.assertEqual(round_step(1.2345, None), 1.2345)

    def test_zero_step_returns_value(self):
        self.assertEqual(round_step(1.2345, 0), 1.2345)


class TestFindSymbolEntry(unittest.TestCase):
    def test_finds_normalized(self):
        entry = find_symbol_entry(_EXCHANGE_INFO["symbols"], "btc_usdt")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["symbol"], "BTCUSDT")

    def test_missing_returns_none(self):
        self.assertIsNone(find_symbol_entry(_EXCHANGE_INFO["symbols"], "ETHUSDT"))


class TestParseExchangeFilters(unittest.TestCase):
    def test_parses_lot_price_notional(self):
        f = parse_exchange_filters(_EXCHANGE_INFO["symbols"][0])
        self.assertEqual(f["stepSize"], 0.001)
        self.assertEqual(f["tickSize"], 0.01)
        self.assertEqual(f["minNotional"], 5.0)
        self.assertEqual(f["minQty"], 0.001)
        self.assertEqual(f["baseAssetPrecision"], 6)

    def test_empty_entry_uses_defaults(self):
        f = parse_exchange_filters({})
        self.assertEqual(f["stepSize"], DEFAULT_SYMBOL_FILTERS["stepSize"])


class _FakeGateway(IExchangeGateway):
    """Minimal gateway that only exposes get_exchange_info."""

    def __init__(self, info):
        self._info = info

    def fetch_ticker(self, symbol):  # pragma: no cover - not exercised
        return {}

    def fetch_candles(self, symbol, timeframe, limit=500):  # pragma: no cover
        return []

    def place_order(self, *a, **k):  # pragma: no cover
        return {}

    def cancel_order(self, symbol, order_id):  # pragma: no cover
        return None

    def fetch_balances(self):  # pragma: no cover
        return {}

    def get_exchange_info(self, symbol=None):
        return self._info


class TestGatewaySymbolFiltersDefault(unittest.TestCase):
    """The ABC's get_symbol_filters must work for any adapter that returns a
    normalized exchangeInfo — this is what makes CCXT a drop-in."""

    def test_derives_from_exchange_info(self):
        gw = _FakeGateway(_EXCHANGE_INFO)
        f = gw.get_symbol_filters("BTCUSDT")
        self.assertEqual(f["stepSize"], 0.001)
        self.assertEqual(f["tickSize"], 0.01)

    def test_unknown_symbol_returns_defaults(self):
        gw = _FakeGateway({"symbols": []})
        f = gw.get_symbol_filters("BTCUSDT")
        self.assertEqual(f["stepSize"], DEFAULT_SYMBOL_FILTERS["stepSize"])

    def test_broken_exchange_info_returns_defaults(self):
        class Broken(_FakeGateway):
            def get_exchange_info(self, symbol=None):
                raise RuntimeError("boom")

        f = Broken({}).get_symbol_filters("BTCUSDT")
        self.assertEqual(f, DEFAULT_SYMBOL_FILTERS)


if __name__ == "__main__":
    unittest.main()
