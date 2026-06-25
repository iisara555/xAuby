"""Execution-quality slippage reporting (#1)."""
from __future__ import annotations

import unittest

from xauby.engine.orders import OrderMixin


class _Stub(OrderMixin):
    def __init__(self, config=None):
        self.config = config or {}
        self.alerts = []

    def send_telegram_alert(self, msg, level=None):
        self.alerts.append((msg, level))


class TestSlippageReport(unittest.TestCase):
    def test_buy_above_quote_is_adverse_positive(self):
        s = _Stub()
        bps = s._report_slippage(side="BUY", ref_price=100.0, fill_price=100.5, symbol="BTCUSDT")
        self.assertAlmostEqual(bps, 50.0)  # paid 0.5% more

    def test_sell_below_quote_is_adverse_positive(self):
        s = _Stub()
        bps = s._report_slippage(side="SELL", ref_price=100.0, fill_price=99.5, symbol="BTCUSDT")
        self.assertAlmostEqual(bps, 50.0)  # received 0.5% less

    def test_favorable_fill_is_negative(self):
        s = _Stub()
        bps = s._report_slippage(side="BUY", ref_price=100.0, fill_price=99.5, symbol="BTCUSDT")
        self.assertAlmostEqual(bps, -50.0)  # bought cheaper than quoted

    def test_alert_fires_above_threshold(self):
        s = _Stub({"execution": {"max_slippage_bps": 10.0}})
        s._report_slippage(side="BUY", ref_price=100.0, fill_price=100.5, symbol="BTCUSDT")
        self.assertEqual(len(s.alerts), 1)
        self.assertIn("High Slippage", s.alerts[0][0])

    def test_no_alert_within_threshold(self):
        s = _Stub({"execution": {"max_slippage_bps": 100.0}})
        s._report_slippage(side="BUY", ref_price=100.0, fill_price=100.5, symbol="BTCUSDT")
        self.assertEqual(s.alerts, [])

    def test_favorable_never_alerts(self):
        s = _Stub({"execution": {"max_slippage_bps": 10.0}})
        s._report_slippage(side="SELL", ref_price=100.0, fill_price=101.0, symbol="BTCUSDT")
        self.assertEqual(s.alerts, [])  # sold higher than quoted = good

    def test_zero_prices_safe(self):
        s = _Stub()
        self.assertEqual(s._report_slippage(side="BUY", ref_price=0.0, fill_price=100.0, symbol="X"), 0.0)


if __name__ == "__main__":
    unittest.main()
