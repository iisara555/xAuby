import unittest

from xauby.api.ws_registry import available_ws_adapters, create_ws_adapter


class TestWsRegistry(unittest.TestCase):
    def test_builtin_adapters_discovered(self):
        adapters = available_ws_adapters()
        self.assertIn("binance", adapters)
        self.assertIn("ccxt", adapters)

    def test_unknown_provider_returns_none(self):
        ws = create_ws_adapter(
            "no_such_exchange",
            symbols=["BTCUSDT"],
            on_tick=lambda tick: None,
            config={},
        )
        self.assertIsNone(ws)

    def test_binance_adapter_built(self):
        ws = create_ws_adapter(
            "binance",
            symbols=["BTCUSDT"],
            on_tick=lambda tick: None,
            config={},
        )
        self.assertIsNotNone(ws)
        # Public ExchangeWebSocket surface.
        for attr in ("start", "stop", "trim_price_cache", "get_latest_price", "tick_age_seconds"):
            self.assertTrue(hasattr(ws, attr))

    def test_ccxt_adapter_unavailable_falls_back_to_none(self):
        # Force the optional ccxt.pro import to fail; adapter should decline and
        # create_ws_adapter must return None (engine then polls via REST).
        import xauby.api.exchanges.exchange_ccxt as mod

        original = mod._import_ccxtpro
        mod._import_ccxtpro = lambda: (_ for _ in ()).throw(ImportError("no ccxt.pro"))
        try:
            ws = create_ws_adapter(
                "ccxt",
                symbols=["BTCUSDT"],
                on_tick=lambda tick: None,
                config={"exchange": {"provider": "ccxt", "ccxt_id": "kraken"}},
            )
            self.assertIsNone(ws)
        finally:
            mod._import_ccxtpro = original


if __name__ == "__main__":
    unittest.main()
