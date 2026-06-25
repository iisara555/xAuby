import json
import os
import tempfile
import unittest
from unittest.mock import patch

from xauby.utils import currency


class _Response:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._payload


class CurrencyRateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_file = os.path.join(self.tmp.name, "usd_thb_rate.json")
        currency._rate_mem = None
        currency._rate_ts = 0.0

    def tearDown(self):
        self.tmp.cleanup()

    def test_prefers_live_binance_th_usdt_market_and_persists_source(self):
        payload = {"symbol": "USDTTHB", "lastPrice": "32.91"}
        with patch.object(currency, "_CACHE_FILE", self.cache_file), patch(
            "urllib.request.urlopen", return_value=_Response(payload)
        ) as urlopen:
            rate = currency.get_usd_thb_rate()

        self.assertEqual(rate, 32.91)
        self.assertIn("api.binance.th", urlopen.call_args.args[0].full_url)
        with open(self.cache_file, encoding="utf-8") as handle:
            cached = json.load(handle)
        self.assertEqual(cached["rate"], 32.91)
        self.assertIn("api.binance.th", cached["source"])

    def test_uses_last_observed_rate_when_refresh_sources_fail(self):
        with open(self.cache_file, "w", encoding="utf-8") as handle:
            json.dump({"rate": 33.12, "ts": 1.0}, handle)
        with patch.object(currency, "_CACHE_FILE", self.cache_file), patch(
            "urllib.request.urlopen", side_effect=OSError("offline")
        ):
            self.assertEqual(currency.get_usd_thb_rate(), 33.12)

    def test_does_not_return_a_hardcoded_rate_without_observed_data(self):
        with patch.object(currency, "_CACHE_FILE", self.cache_file), patch(
            "urllib.request.urlopen", side_effect=OSError("offline")
        ):
            with self.assertRaises(currency.CurrencyRateUnavailable):
                currency.get_usd_thb_rate()


if __name__ == "__main__":
    unittest.main()
