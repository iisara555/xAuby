import unittest

from xauby.api.client import LiteBinanceClient


class TestExchangeFilterFallbacks(unittest.TestCase):
    def test_zero_lot_step_falls_back_to_min_qty_increment(self):
        entry = {
            "baseAssetPrecision": 8,
            "quoteAssetPrecision": 8,
            "filters": [
                {
                    "filterType": "LOT_SIZE",
                    "minQty": "0.00010",
                    "stepSize": "0.0",
                },
            ],
        }

        filters = LiteBinanceClient._parse_exchange_filters(
            object.__new__(LiteBinanceClient),
            entry,
        )

        self.assertEqual(filters["stepSizeStr"], "0.00010")
        self.assertAlmostEqual(filters["stepSize"], 0.00010)

    def test_zero_price_tick_falls_back_to_min_price_increment(self):
        entry = {
            "baseAssetPrecision": 8,
            "quoteAssetPrecision": 8,
            "filters": [
                {
                    "filterType": "PRICE_FILTER",
                    "minPrice": "0.01",
                    "tickSize": "0.0",
                },
            ],
        }

        filters = LiteBinanceClient._parse_exchange_filters(
            object.__new__(LiteBinanceClient),
            entry,
        )

        self.assertEqual(filters["tickSizeStr"], "0.01")
        self.assertAlmostEqual(filters["tickSize"], 0.01)

    def test_limit_buy_uses_min_qty_fallback_step(self):
        client = object.__new__(LiteBinanceClient)
        client._get_symbol_filters = lambda symbol: {
            "stepSize": 0.00010,
            "tickSize": 0.01,
            "baseAssetPrecision": 8,
            "quoteAssetPrecision": 8,
            "minQty": 0.00010,
            "minNotional": 5.0,
        }
        captured = {}

        def fake_request(method, path, signed=False, params=None):
            captured.update(params or {})
            return {
                "orderId": "1",
                "clientOrderId": captured.get("newClientOrderId", ""),
                "symbol": captured.get("symbol", ""),
                "side": captured.get("side", ""),
                "type": captured.get("type", ""),
                "price": captured.get("price", "0"),
                "origQty": captured.get("quantity", "0"),
                "status": "NEW",
            }

        client._request = fake_request

        client.place_order(
            symbol="XAUTUSDT",
            side="BUY",
            order_type="LIMIT",
            amount=48.09,
            price=4217.34,
        )

        self.assertEqual(captured["price"], "4217.34")
        self.assertEqual(captured["quantity"], "0.0114")


if __name__ == "__main__":
    unittest.main()
