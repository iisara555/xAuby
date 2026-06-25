"""P1.1 — exchange-side stop-loss capability probe + startup degradation guard.

Covers:
  * CCXTExchangeClient.supports_exchange_stop_loss() — explicit config wins,
    otherwise probe the ccxt ``has`` map.
  * BaseEngine._warn_if_stop_loss_unsupported() — warns/alerts only when a live,
    SL-dependent strategy runs on a gateway that cannot place STOP_LOSS_LIMIT.
"""
import logging
import types
import unittest

from xauby.api.ccxt_client import CCXTExchangeClient
from xauby.api.client import LiteBinanceClient
from xauby.engine.base import BaseEngine


class _FakeEx:
    def __init__(self, has):
        self.has = has


def _ccxt(has=None, capabilities=None):
    exchange_cfg = {"provider": "ccxt", "ccxt_id": "testex"}
    if capabilities is not None:
        exchange_cfg["capabilities"] = capabilities
    return CCXTExchangeClient(
        config={"exchange": exchange_cfg},
        exchange_instance=_FakeEx(has or {}),
    )


class TestCcxtStopLossProbe(unittest.TestCase):
    def test_probe_detects_stop_limit_support(self):
        self.assertTrue(_ccxt({"createStopLimitOrder": True}).supports_exchange_stop_loss())
        self.assertTrue(_ccxt({"createStopOrder": True}).supports_exchange_stop_loss())
        self.assertTrue(_ccxt({"createStopMarketOrder": True}).supports_exchange_stop_loss())

    def test_probe_false_when_no_stop_orders(self):
        self.assertFalse(_ccxt({"createOrder": True}).supports_exchange_stop_loss())
        self.assertFalse(_ccxt({}).supports_exchange_stop_loss())

    def test_explicit_config_overrides_probe(self):
        # explicit False beats a capable venue
        self.assertFalse(
            _ccxt({"createStopLimitOrder": True}, capabilities={"stop_loss_limit": False})
            .supports_exchange_stop_loss()
        )
        # explicit True beats an incapable venue
        self.assertTrue(
            _ccxt({}, capabilities={"stop_loss_limit": True}).supports_exchange_stop_loss()
        )

    def test_native_binance_supports_exchange_sl(self):
        client = LiteBinanceClient.__new__(LiteBinanceClient)
        self.assertTrue(client.supports_exchange_stop_loss())


class _StubGateway:
    def __init__(self, supported):
        self._supported = supported

    def supports_exchange_stop_loss(self):
        return self._supported


class TestStartupStopLossGuard(unittest.TestCase):
    def _engine(self, gateway, alert_sink=None):
        eng = BaseEngine.__new__(BaseEngine)
        eng.client = gateway
        eng.config = {"exchange": {"provider": "ccxt"}}
        if alert_sink is not None:
            eng.send_telegram_alert = lambda msg, *a, **k: alert_sink.append(msg)
        return eng

    @staticmethod
    def _eff(disable_sl):
        return types.SimpleNamespace(strategy={"disable_stop_loss": disable_sl})

    def test_warns_when_live_sl_strategy_on_incapable_venue(self):
        alerts = []
        eng = self._engine(_StubGateway(False), alert_sink=alerts)
        with self.assertLogs("lite_bot", level="WARNING") as cm:
            eng._warn_if_stop_loss_unsupported("BTCUSDT", "donchian_trend", self._eff(False), True)
        self.assertTrue(any("LOCAL stop-loss monitoring only" in m for m in cm.output))
        self.assertEqual(len(alerts), 1)
        self.assertIn("Degraded Stop-Loss", alerts[0])

    def test_silent_when_venue_supports_sl(self):
        alerts = []
        eng = self._engine(_StubGateway(True), alert_sink=alerts)
        with _no_warning(self):
            eng._warn_if_stop_loss_unsupported("BTCUSDT", "donchian_trend", self._eff(False), True)
        self.assertEqual(alerts, [])

    def test_silent_when_strategy_disables_stop_loss(self):
        # CDC (disable_stop_loss=True) never needs exchange-side SL.
        alerts = []
        eng = self._engine(_StubGateway(False), alert_sink=alerts)
        with _no_warning(self):
            eng._warn_if_stop_loss_unsupported("XAUTUSDT", "cdc_action_zone", self._eff(True), True)
        self.assertEqual(alerts, [])

    def test_silent_when_sim_pair(self):
        alerts = []
        eng = self._engine(_StubGateway(False), alert_sink=alerts)
        with _no_warning(self):
            eng._warn_if_stop_loss_unsupported("BTCUSDT", "donchian_trend", self._eff(False), False)
        self.assertEqual(alerts, [])

    def test_silent_when_gateway_lacks_probe_method(self):
        # Unknown adapter / test double without the method -> assume supported.
        alerts = []
        eng = self._engine(object(), alert_sink=alerts)
        with _no_warning(self):
            eng._warn_if_stop_loss_unsupported("BTCUSDT", "donchian_trend", self._eff(False), True)
        self.assertEqual(alerts, [])


class _no_warning:
    """assert that no WARNING is logged on the lite_bot logger within the block."""

    def __init__(self, test):
        self.test = test

    def __enter__(self):
        self.handler = _ListHandler()
        self.logger = logging.getLogger("lite_bot")
        self.logger.addHandler(self.handler)
        self._prev_level = self.logger.level
        self.logger.setLevel(logging.WARNING)
        return self

    def __exit__(self, *exc):
        self.logger.removeHandler(self.handler)
        self.logger.setLevel(self._prev_level)
        self.test.assertEqual(
            [r for r in self.handler.records if r.levelno >= logging.WARNING],
            [],
            "expected no WARNING logs",
        )
        return False


class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


if __name__ == "__main__":
    unittest.main()
