import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from xauby.api import create_exchange_client, create_exchange_websocket
from xauby.api.ccxt_client import CCXTAPIError, CCXTExchangeClient


class FakeCCXTExchange:
    def __init__(self):
        self.markets = {
            "BTC/USDT": {
                "id": "BTCUSDT",
                "symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
                "active": True,
                "precision": {"amount": 8, "price": 2},
                "limits": {"amount": {"min": 0.0001}, "cost": {"min": 5}},
            }
        }
        self.markets_by_id = {"BTCUSDT": {"symbol": "BTC/USDT"}}
        self.created_orders = []

    def load_markets(self):
        return self.markets

    def fetch_ticker(self, symbol):
        return {
            "symbol": symbol,
            "last": 50000,
            "bid": 49990,
            "ask": 50010,
            "baseVolume": 12.5,
            "high": 51000,
            "low": 49000,
            "percentage": 1.25,
        }

    def fetch_ohlcv(self, symbol, timeframe="1h", limit=250):
        return [[1710000000000, 1, 2, 0.5, 1.5, 10]][:limit]

    def fetch_balance(self):
        return {
            "free": {"USDT": 1000, "BTC": 0.1},
            "used": {"USDT": 50},
            "total": {"USDT": 1050, "BTC": 0.1},
        }

    def fetch_open_orders(self, symbol=None):
        return [
            {
                "id": "o1",
                "symbol": symbol or "BTC/USDT",
                "side": "sell",
                "type": "limit",
                "status": "open",
                "amount": 0.01,
                "filled": 0,
                "price": 51000,
            }
        ]

    def fetch_order(self, order_id, symbol):
        return {
            "id": order_id,
            "symbol": symbol,
            "side": "buy",
            "type": "limit",
            "status": "closed",
            "amount": 0.01,
            "filled": 0.01,
            "price": 50000,
        }

    def cancel_order(self, order_id, symbol):
        return {"id": order_id, "symbol": symbol, "status": "canceled"}

    def create_order(self, symbol, order_type, side, amount, price=None, params=None):
        order = {
            "id": "created-1",
            "symbol": symbol,
            "type": order_type,
            "side": side,
            "amount": amount,
            "filled": 0,
            "price": price or 0,
            "status": "open",
            "clientOrderId": (params or {}).get("clientOrderId", ""),
        }
        self.created_orders.append(order)
        return order


class TestCCXTClient(unittest.TestCase):
    def setUp(self):
        self.exchange = FakeCCXTExchange()
        self.client = CCXTExchangeClient(
            config={"exchange": {"provider": "ccxt", "ccxt_id": "binance"}},
            exchange_instance=self.exchange,
        )

    def test_ticker_and_candles_are_normalized(self):
        ticker = self.client.get_ticker("BTCUSDT")
        candles = self.client.fetch_candles("BTCUSDT", "1h", limit=1)

        self.assertEqual(ticker["symbol"], "BTCUSDT")
        self.assertEqual(ticker["last"], 50000)
        self.assertEqual(candles[0].timestamp, 1710000000)
        self.assertEqual(candles[0].close, 1.5)

    def test_balances_and_orders_are_binance_shaped(self):
        balances = self.client.get_balances()
        orders = self.client.get_open_orders("BTCUSDT")
        fetched = self.client.get_order("BTCUSDT", "o2")

        self.assertEqual(balances["USDT"]["available"], 1000)
        self.assertEqual(balances["USDT"]["reserved"], 50)
        self.assertEqual(orders[0]["orderId"], "o1")
        self.assertEqual(orders[0]["status"], "NEW")
        self.assertEqual(fetched["status"], "FILLED")

    def test_exchange_info_converts_ccxt_precision_to_steps(self):
        info = self.client.get_exchange_info("BTCUSDT")
        filters = {f["filterType"]: f for f in info["symbols"][0]["filters"]}

        self.assertEqual(info["symbols"][0]["baseAssetPrecision"], 8)
        self.assertEqual(filters["LOT_SIZE"]["stepSize"], "0.00000001")
        self.assertEqual(filters["PRICE_FILTER"]["tickSize"], "0.01")

    def test_limit_buy_converts_quote_amount_to_base_amount(self):
        order = self.client.place_order(
            "BTCUSDT",
            "BUY",
            "LIMIT",
            amount=1000,
            price=50000,
            client_id="cid-1",
        )

        self.assertEqual(order["orderId"], "created-1")
        self.assertAlmostEqual(self.exchange.created_orders[0]["amount"], 0.02)
        self.assertEqual(self.exchange.created_orders[0]["clientOrderId"], "cid-1")

    def test_okx_client_order_id_is_alphanumeric_max_32(self):
        okx_client = CCXTExchangeClient(
            config={"exchange": {"provider": "ccxt", "ccxt_id": "okx"}},
            exchange_instance=self.exchange,
        )

        order = okx_client.place_order(
            "BTCUSDT",
            "BUY",
            "LIMIT",
            amount=1000,
            price=50000,
            client_id="xauby-buy-1783008033123-63584f05",
        )

        client_id = self.exchange.created_orders[-1]["clientOrderId"]
        self.assertEqual(client_id, "xaubybuy178300803312363584f05")
        self.assertLessEqual(len(client_id), 32)
        self.assertTrue(client_id.isalnum())
        self.assertEqual(order.client_id, client_id)

    def test_stop_loss_limit_is_explicitly_unsupported_by_default(self):
        with self.assertRaises(CCXTAPIError):
            self.client.place_order(
                "BTCUSDT",
                "SELL",
                "STOP_LOSS_LIMIT",
                amount=0.01,
                price=49000,
                stop_price=49500,
            )

    def test_base_url_override_preserves_okx_endpoint_map(self):
        self.exchange.urls = {
            "api": {"public": "https://old-public", "private": "https://old-private"}
        }
        self.client._apply_base_url_override("https://www.okx.com/")
        self.assertEqual(
            self.exchange.urls["api"],
            {"public": "https://www.okx.com", "private": "https://www.okx.com"},
        )

    def test_factory_selects_ccxt_provider(self):
        with patch("xauby.api.exchanges.exchange_ccxt.CCXTExchangeClient") as client_cls:
            create_exchange_client(
                {"exchange": {"provider": "ccxt", "ccxt_id": "kraken"}},
                api_key="k",
                api_secret="s",
            )

        client_cls.assert_called_once()
        # registry builder: CCXTExchangeClient(api_key, api_secret, base_url, config=...)
        self.assertEqual(client_cls.call_args.args[0], "k")

    def test_ccxt_websocket_uses_ccxtpro_when_available(self):
        fake_module = MagicMock()
        fake_module.kraken = MagicMock()
        fake_module.binance = MagicMock()
        import xauby.api.exchanges.exchange_ccxt as mod

        with patch.object(mod, "_import_ccxtpro", return_value=fake_module):
            kraken_ws = create_exchange_websocket(
                {"exchange": {"provider": "ccxt", "ccxt_id": "kraken", "name": "kraken"}},
                ["BTCUSDT"],
                lambda tick: None,
            )
            binance_ws = create_exchange_websocket(
                {"exchange": {"provider": "ccxt", "ccxt_id": "binance"}},
                ["BTCUSDT"],
                lambda tick: None,
            )

        self.assertEqual(type(kraken_ws).__name__, "CCXTProWebSocket")
        self.assertEqual(type(binance_ws).__name__, "CCXTProWebSocket")
        self.assertEqual(kraken_ws.exchange_id, "kraken")
        self.assertEqual(binance_ws.exchange_id, "binance")


class TestCCXTProWebSocket(unittest.TestCase):
    def _make(self, on_status=None):
        from xauby.api.exchanges.exchange_ccxt import CCXTProWebSocket

        fake_exchange = FakeCCXTExchange()
        ws = CCXTProWebSocket(
            symbols=["BTCUSDT"],
            on_tick=lambda tick: None,
            on_status=on_status,
            config={"exchange": {"provider": "ccxt", "ccxt_id": "binance"}},
            exchange_instance=fake_exchange,
        )
        return ws

    def test_ticker_to_tick_shape(self):
        ws = self._make()
        tick = ws._ticker_to_tick(
            "BTCUSDT",
            {"last": 50000, "bid": 49990, "ask": 50010, "percentage": 1.25},
        )
        self.assertEqual(tick["symbol"], "BTCUSDT")
        self.assertEqual(tick["last"], 50000.0)
        self.assertEqual(tick["bid"], 49990.0)
        self.assertEqual(tick["ask"], 50010.0)
        self.assertEqual(tick["percent_change_24h"], 1.25)
        self.assertIn("monotonic_ts", tick)

    def test_to_ccxt_symbol_uses_markets(self):
        ws = self._make()
        self.assertEqual(ws._to_ccxt_symbol("BTCUSDT"), "BTC/USDT")

    def test_store_and_emit_caches_price(self):
        ws = self._make()
        tick = ws._ticker_to_tick("BTCUSDT", {"last": 42000})
        ws._store_and_emit("BTCUSDT", tick)
        self.assertEqual(ws.get_latest_price("BTCUSDT"), 42000.0)

    def test_zero_price_ticker_is_skipped(self):
        ws = self._make()
        self.assertIsNone(ws._ticker_to_tick("BTCUSDT", {"last": 0}))

    def test_okx_uses_public_order_book_depth_by_default(self):
        from xauby.api.exchanges.exchange_ccxt import CCXTProWebSocket

        ws = CCXTProWebSocket(
            symbols=["BTCUSDT"],
            on_tick=lambda tick: None,
            config={"exchange": {"provider": "ccxt", "ccxt_id": "okx"}},
            exchange_instance=FakeCCXTExchange(),
        )
        ws._watch_channel = AsyncMock()
        asyncio.run(ws._watch_order_book("BTCUSDT"))
        ws._watch_channel.assert_awaited_once_with(
            "BTCUSDT", "order_book", "watch_order_book", 5
        )

    def test_unavailable_without_ccxtpro(self):
        import xauby.api.exchanges.exchange_ccxt as mod
        from xauby.api.ws_registry import _AdapterUnavailable

        original = mod._import_ccxtpro
        mod._import_ccxtpro = lambda: (_ for _ in ()).throw(ImportError("no ccxt.pro"))
        try:
            with self.assertRaises(_AdapterUnavailable):
                mod.CCXTProWebSocket(
                    symbols=["BTCUSDT"],
                    on_tick=lambda tick: None,
                    config={"exchange": {"provider": "ccxt", "ccxt_id": "binance"}},
                )
        finally:
            mod._import_ccxtpro = original


class _AsyncFakeProExchange:
    """Minimal ccxt.pro-shaped async exchange for shutdown tests."""

    def __init__(self):
        self.markets = {"BTC/USDT": {"id": "BTCUSDT", "symbol": "BTC/USDT"}}
        self.markets_by_id = {"BTCUSDT": {"symbol": "BTC/USDT"}}
        self.closed = False

    async def load_markets(self):
        return self.markets

    async def watch_ticker(self, symbol):
        await asyncio.sleep(3600)  # block until cancelled
        return {}

    async def close(self):
        self.closed = True


class TestCCXTProWebSocketLifecycle(unittest.TestCase):
    def _make(self):
        from xauby.api.exchanges.exchange_ccxt import CCXTProWebSocket

        ex = _AsyncFakeProExchange()
        ws = CCXTProWebSocket(
            symbols=["BTCUSDT"],
            on_tick=lambda tick: None,
            config={"exchange": {"provider": "ccxt", "ccxt_id": "binance"}},
            exchange_instance=ex,
        )
        return ws, ex

    def test_start_then_stop_terminates_cleanly(self):
        ws, ex = self._make()
        ws.start()
        ws.stop()
        self.assertFalse(ws._thread.is_alive())
        self.assertTrue(ex.closed)

    def test_stop_immediately_after_start_no_thread_leak(self):
        # Race: stop() called before the worker thread's event loop is running.
        ws, _ = self._make()
        ws.start()
        ws.stop()  # immediate — must still join cleanly
        self.assertFalse(ws._thread.is_alive())

    def test_double_start_is_idempotent(self):
        ws, _ = self._make()
        ws.start()
        first = ws._thread
        ws.start()  # must not spawn a second thread
        self.assertIs(ws._thread, first)
        ws.stop()
        self.assertFalse(ws._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
