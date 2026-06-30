"""Spot orphan reconcile guard, symmetric with the swap path (#2)."""
from __future__ import annotations

import types
import unittest
from unittest.mock import MagicMock, patch

from xauby.engine.reconcile import ReconcileMixin
from xauby.engine.symbol_context import SymbolContext


def _sc():
    return SymbolContext(symbol="BTCUSDT")


class _Eng(ReconcileMixin):
    def __init__(self, client, sc):
        self.client = client
        self.config = {"portfolio": {"min_order_amount": 10.0}}
        self._sc_obj = sc
        self.alerts = []

    def _get_base_asset(self, sym):
        return "BTC"

    def _strategy_name_for_symbol(self, sym):
        return "cdc_action_zone"

    def _sc(self, sym=None):
        return self._sc_obj

    def send_telegram_alert(self, msg, level=None):
        self.alerts.append(msg)


def _client(*, available=0.0, reserved=0.0, last=100.0, min_qty=0.0001, step=0.0001):
    c = MagicMock()
    c.get_balances.return_value = {"BTC": {"available": available, "reserved": reserved}}
    c.get_symbol_filters.return_value = {"minQty": min_qty, "stepSize": step}
    c.get_ticker.return_value = {"last": last}
    return c


_EFF = types.SimpleNamespace(portfolio={"min_order_amount": 10.0})


class TestSpotOrphan(unittest.TestCase):
    @patch("xauby.runtime.trading_config.resolve_trading_config", return_value=_EFF)
    def test_real_orphan_halts_and_alerts(self, _rt):
        sc = _sc()
        eng = _Eng(_client(available=0.5, last=100.0), sc)  # 0.5 BTC ~ $50
        eng._detect_spot_orphan("BTCUSDT", {"state": "idle"})
        snap = sc.feed_snapshot()
        self.assertTrue(snap["trading_halted"])
        self.assertIn("BTC", snap["halt_reason"])
        self.assertEqual(len(eng.alerts), 1)
        self.assertIn("RECONCILE NEEDED", eng.alerts[0])

    @patch("xauby.runtime.trading_config.resolve_trading_config", return_value=_EFF)
    def test_dust_balance_is_ignored(self, _rt):
        sc = _sc()
        eng = _Eng(_client(available=0.00001, last=100.0), sc)  # below stepSize
        eng._detect_spot_orphan("BTCUSDT", {"state": "idle"})
        self.assertFalse(sc.feed_snapshot()["trading_halted"])
        self.assertEqual(eng.alerts, [])

    @patch("xauby.runtime.trading_config.resolve_trading_config", return_value=_EFF)
    def test_below_min_order_value_is_ignored(self, _rt):
        sc = _sc()
        # 0.05 BTC * $100 = $5 < $10 min_order -> not a tradeable position.
        eng = _Eng(_client(available=0.05, last=100.0), sc)
        eng._detect_spot_orphan("BTCUSDT", {"state": "idle"})
        self.assertFalse(sc.feed_snapshot()["trading_halted"])

    @patch("xauby.runtime.trading_config.resolve_trading_config", return_value=_EFF)
    def test_no_balance_no_halt(self, _rt):
        sc = _sc()
        eng = _Eng(_client(available=0.0), sc)
        eng._detect_spot_orphan("BTCUSDT", {"state": "idle"})
        self.assertFalse(sc.feed_snapshot()["trading_halted"])


if __name__ == "__main__":
    unittest.main()
