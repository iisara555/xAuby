"""Tests for exchange-neutral config resolution (fee / quote / provider)."""

import os
import unittest
from unittest import mock

from xauby.runtime.exchange_config import (
    DEFAULT_FEE_PCT,
    DEFAULT_PROVIDER,
    DEFAULT_QUOTE_ASSET,
    credential_env_names,
    resolve_exchange_credentials,
    resolve_fee_pct,
    resolve_provider,
    resolve_quote_asset,
)


class TestCredentialEnvNames(unittest.TestCase):
    def test_default_binance(self):
        cfg = {"exchange": {"provider": "binance", "name": "binance.th"}}
        self.assertEqual(
            credential_env_names(cfg),
            ("BINANCE_API_KEY", "BINANCE_API_SECRET", "BINANCE_BASE_URL"),
        )

    def test_empty_config_defaults_binance(self):
        self.assertEqual(
            credential_env_names({})[0], "BINANCE_API_KEY"
        )

    def test_ccxt_id_drives_prefix(self):
        cfg = {"exchange": {"provider": "ccxt", "ccxt_id": "kraken"}}
        self.assertEqual(
            credential_env_names(cfg),
            ("KRAKEN_API_KEY", "KRAKEN_API_SECRET", "KRAKEN_BASE_URL"),
        )

    def test_explicit_env_names_win(self):
        cfg = {"exchange": {"ccxt_id": "kraken", "api_key_env": "MY_KEY"}}
        names = credential_env_names(cfg)
        self.assertEqual(names[0], "MY_KEY")
        self.assertEqual(names[1], "KRAKEN_API_SECRET")

    def test_non_alnum_sanitized(self):
        cfg = {"exchange": {"ccxt_id": "binance-us"}}
        self.assertEqual(credential_env_names(cfg)[0], "BINANCE_US_API_KEY")


class TestResolveExchangeCredentials(unittest.TestCase):
    def test_reads_exchange_specific_env(self):
        cfg = {"exchange": {"provider": "ccxt", "ccxt_id": "kraken"}}
        with mock.patch.dict(
            os.environ,
            {"KRAKEN_API_KEY": "k1", "KRAKEN_API_SECRET": "s1"},
            clear=False,
        ):
            os.environ.pop("KRAKEN_BASE_URL", None)
            key, secret, base_url = resolve_exchange_credentials(cfg)
        self.assertEqual((key, secret, base_url), ("k1", "s1", None))

    def test_base_url_precedence_config_over_env(self):
        cfg = {"exchange": {"provider": "binance", "base_url": "https://cfg.example"}}
        with mock.patch.dict(os.environ, {"BINANCE_BASE_URL": "https://env.example"}, clear=False):
            _, _, base_url = resolve_exchange_credentials(cfg)
        self.assertEqual(base_url, "https://cfg.example")

    def test_binance_default_env(self):
        cfg = {"exchange": {"provider": "binance"}}
        with mock.patch.dict(os.environ, {"BINANCE_API_KEY": "bk"}, clear=False):
            key, _, _ = resolve_exchange_credentials(cfg)
        self.assertEqual(key, "bk")


class TestResolveFeePct(unittest.TestCase):
    def test_default_when_unconfigured(self):
        self.assertEqual(resolve_fee_pct({}), DEFAULT_FEE_PCT)
        self.assertEqual(resolve_fee_pct(None), DEFAULT_FEE_PCT)

    def test_exchange_block_wins(self):
        cfg = {
            "exchange": {"fee_pct": 0.0015},
            "backtest": {"fee_pct": 0.002},
            "trading": {"fee_pct": 0.003},
        }
        self.assertEqual(resolve_fee_pct(cfg), 0.0015)

    def test_backtest_fallback(self):
        cfg = {"backtest": {"fee_pct": 0.002}, "trading": {"fee_pct": 0.003}}
        self.assertEqual(resolve_fee_pct(cfg), 0.002)

    def test_trading_fallback(self):
        cfg = {"trading": {"fee_pct": 0.003}}
        self.assertEqual(resolve_fee_pct(cfg), 0.003)

    def test_zero_fee_is_honored(self):
        self.assertEqual(resolve_fee_pct({"exchange": {"fee_pct": 0}}), 0.0)

    def test_invalid_value_falls_through(self):
        cfg = {"exchange": {"fee_pct": "abc"}, "trading": {"fee_pct": 0.004}}
        self.assertEqual(resolve_fee_pct(cfg), 0.004)

    def test_negative_value_ignored(self):
        cfg = {"exchange": {"fee_pct": -0.1}, "trading": {"fee_pct": 0.004}}
        self.assertEqual(resolve_fee_pct(cfg), 0.004)


class TestResolveQuoteAsset(unittest.TestCase):
    def test_default(self):
        self.assertEqual(resolve_quote_asset({}), DEFAULT_QUOTE_ASSET)
        self.assertEqual(resolve_quote_asset(None), DEFAULT_QUOTE_ASSET)

    def test_configured_uppercased(self):
        self.assertEqual(
            resolve_quote_asset({"exchange": {"quote_asset": "thb"}}), "THB"
        )


class TestResolveProvider(unittest.TestCase):
    def test_default(self):
        self.assertEqual(resolve_provider({}), DEFAULT_PROVIDER)

    def test_provider_lowercased(self):
        self.assertEqual(resolve_provider({"exchange": {"provider": "CCXT"}}), "ccxt")

    def test_ccxt_id_fallback(self):
        self.assertEqual(
            resolve_provider({"exchange": {"ccxt_id": "Kraken"}}), "kraken"
        )


class _Spec:
    def __init__(self, sim_fee_pct):
        self.sim_fee_pct = sim_fee_pct


class _Registry:
    def __init__(self, specs):
        self._specs = specs

    def get(self, symbol):
        return self._specs.get(symbol)


class TestEngineFeeResolution(unittest.TestCase):
    """The engine fee method must honor config and per-symbol overrides so the
    live/sim paths stay in parity with the backtest resolver."""

    def _resolve(self, config, specs, symbol):
        from xauby.engine.base import BaseEngine

        stub = type("E", (), {})()
        stub.config = config
        stub._pair_registry = _Registry(specs)
        stub._exchange_fee_pct_by_symbol = {}
        return BaseEngine._symbol_fee_pct(stub, symbol)

    def test_uses_config_exchange_fee(self):
        fee = self._resolve({"exchange": {"fee_pct": 0.0015}}, {}, "BTCUSDT")
        self.assertEqual(fee, 0.0015)

    def test_per_symbol_override_wins(self):
        fee = self._resolve(
            {"exchange": {"fee_pct": 0.0015}},
            {"BTCUSDT": _Spec(0.0005)},
            "BTCUSDT",
        )
        self.assertEqual(fee, 0.0005)

    def test_live_exchange_fee_wins_over_config_and_sim_override(self):
        from xauby.engine.base import BaseEngine

        stub = type("E", (), {})()
        stub.config = {"exchange": {"fee_pct": 0.001}}
        stub._pair_registry = _Registry({"BTCUSDT": _Spec(0.0008)})
        stub._exchange_fee_pct_by_symbol = {"BTCUSDT": 0.0005}
        self.assertEqual(BaseEngine._symbol_fee_pct(stub, "BTCUSDT"), 0.0005)

    def test_default_when_unconfigured(self):
        fee = self._resolve({}, {}, "BTCUSDT")
        self.assertEqual(fee, DEFAULT_FEE_PCT)


if __name__ == "__main__":
    unittest.main()
