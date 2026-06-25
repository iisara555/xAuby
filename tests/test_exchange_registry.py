import unittest
from unittest.mock import MagicMock, patch

from xauby.api import create_exchange_client, create_exchange_websocket
from xauby.api.client import LiteBinanceClient
from xauby.api.exchange_registry import available_exchanges, build_gateway


class DummyGateway:
    def __init__(self, api_key, api_secret, base_url, **params):
        self.api_key = api_key
        self.params = params


def _flag_on(exchange):
    return {
        "architecture": {"exchange_plugin_registry_enabled": True},
        "exchange": exchange,
    }


class TestExchangeRegistry(unittest.TestCase):
    def test_builtin_gateways_discovered(self):
        self.assertIn("binance", available_exchanges())
        self.assertIn("ccxt", available_exchanges())

    def test_build_gateway_unknown_raises(self):
        with self.assertRaises(KeyError):
            build_gateway("nope", {})

    # --- registry mode (flag on) -------------------------------------------------

    def test_registry_binance_builds_native_client(self):
        client = create_exchange_client(_flag_on({"provider": "binance"}))
        self.assertIsInstance(client, LiteBinanceClient)

    def test_registry_ccxt_builds_ccxt_client(self):
        with patch("xauby.api.exchanges.exchange_ccxt.CCXTExchangeClient") as ccxt_cls:
            create_exchange_client(
                _flag_on({"provider": "ccxt", "ccxt_id": "kraken"}),
                api_key="k",
                api_secret="s",
            )
        ccxt_cls.assert_called_once()

    def test_registry_unknown_provider_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "refusing cross-exchange fallback"):
            create_exchange_client(_flag_on({"provider": "totally_unknown"}))

    def test_gateway_class_override_wins_in_registry_mode(self):
        cfg = _flag_on({"provider": "binance", "gateway_class": "tests.test_exchange_registry.DummyGateway"})
        client = create_exchange_client(cfg, api_key="abc")
        # Assert by name + attribute: under `unittest discover` this module is
        # imported both as `test_exchange_registry` and `tests.test_exchange_registry`,
        # so isinstance across the two identities is unreliable.
        self.assertEqual(type(client).__name__, "DummyGateway")
        self.assertEqual(client.api_key, "abc")

    # --- registry is the sole path: routing is flag-independent ------------------

    def test_binance_builds_native_client_without_flag(self):
        # The exchange_plugin_registry_enabled flag is now vestigial; the
        # registry routes regardless of it.
        client = create_exchange_client({"exchange": {"provider": "binance"}})
        self.assertIsInstance(client, LiteBinanceClient)

    def test_ccxt_uses_ccxt_client_without_flag(self):
        with patch("xauby.api.exchanges.exchange_ccxt.CCXTExchangeClient") as ccxt_cls:
            create_exchange_client({"exchange": {"provider": "ccxt", "ccxt_id": "kraken"}})
        ccxt_cls.assert_called_once()

    # --- websocket dispatch ------------------------------------------------------

    def test_registry_binance_websocket_is_built(self):
        ws = create_exchange_websocket(
            _flag_on({"provider": "binance"}),
            ["BTCUSDT"],
            lambda tick: None,
        )
        self.assertIsNotNone(ws)
        self.assertEqual(type(ws).__name__, "BinanceWebSocket")

    def test_registry_ccxt_websocket_uses_ccxtpro(self):
        fake_module = MagicMock()
        fake_module.binance = MagicMock()
        import xauby.api.exchanges.exchange_ccxt as mod

        with patch.object(mod, "_import_ccxtpro", return_value=fake_module):
            ws = create_exchange_websocket(
                _flag_on({"provider": "ccxt", "ccxt_id": "binance"}),
                ["BTCUSDT"],
                lambda tick: None,
            )
        self.assertIsNotNone(ws)
        self.assertEqual(type(ws).__name__, "CCXTProWebSocket")

    def test_ccxt_websocket_returns_none_without_ccxtpro(self):
        # ccxt provider with ccxt.pro unavailable -> None so the engine uses
        # REST polling (no flag needed).
        ws = create_exchange_websocket(
            {"exchange": {"provider": "ccxt", "ccxt_id": "kraken"}},
            ["BTCUSDT"],
            lambda tick: None,
        )
        self.assertIsNone(ws)


if __name__ == "__main__":
    unittest.main()
