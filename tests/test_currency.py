import json
import os
import tempfile
import time
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

    def tearDown(self):
        self.tmp.cleanup()

    def test_prefers_binance_th_midpoint_and_persists_metadata(self):
        now = time.time()
        payload = {
            "symbol": "USDTTHB",
            "lastPrice": "32.91",
            "bidPrice": "32.90",
            "askPrice": "32.92",
            "closeTime": int(now * 1000),
        }
        with patch(
            "urllib.request.urlopen", return_value=_Response(payload)
        ) as urlopen:
            quote = currency.get_thb_rate_quote(cache_file=self.cache_file, now=now)

        self.assertAlmostEqual(quote["rate"], 32.91)
        self.assertEqual(quote["pair"], "USDT/THB")
        self.assertEqual(quote["source_label"], "Binance TH")
        self.assertFalse(quote["stale"])
        self.assertIn("api.binance.th", urlopen.call_args.args[0].full_url)
        with open(self.cache_file, encoding="utf-8") as handle:
            cached = json.load(handle)
        self.assertEqual(cached["rate"], 32.91)
        self.assertEqual(cached["base"], "USDT")
        self.assertEqual(cached["source_id"], "binance_th")
        self.assertIn("binance.th/en/trade", cached["source_url"])
        self.assertIn("api.binance.th", cached["endpoint_url"])

    def test_falls_back_to_ecb_usd_reference_with_explicit_pair(self):
        now = time.time()
        response = {"date": "2026-08-11", "base": "USD", "quote": "THB", "rate": 33.105}
        with patch(
            "urllib.request.urlopen", side_effect=[OSError("offline"), _Response(response)]
        ):
            quote = currency.get_thb_rate_quote(cache_file=self.cache_file, now=now)

        self.assertEqual(quote["rate"], 33.105)
        self.assertEqual(quote["pair"], "USD/THB")
        self.assertEqual(quote["source"], "frankfurter_ecb")

    def test_uses_stale_observed_rate_for_at_most_24_hours(self):
        now = time.time()
        with open(self.cache_file, "w", encoding="utf-8") as handle:
            json.dump({
                "rate": 33.12,
                "ts": now - 601,
                "base": "USDT",
                "source_id": "binance_th",
                "source_label": "Binance TH",
                "source_url": "https://api.binance.th/example",
            }, handle)
        with patch(
            "urllib.request.urlopen", side_effect=OSError("offline")
        ):
            quote = currency.get_thb_rate_quote(cache_file=self.cache_file, now=now)

        self.assertEqual(quote["rate"], 33.12)
        self.assertTrue(quote["stale"])
        self.assertEqual(quote["age_sec"], 601)

    def test_rejects_cache_older_than_24_hours(self):
        now = time.time()
        with open(self.cache_file, "w", encoding="utf-8") as handle:
            json.dump({"rate": 33.12, "ts": now - 86_401}, handle)
        with patch(
            "urllib.request.urlopen", side_effect=OSError("offline")
        ):
            with self.assertRaises(currency.CurrencyRateUnavailable):
                currency.get_thb_rate_quote(cache_file=self.cache_file, now=now)

    def test_does_not_return_a_hardcoded_rate_without_observed_data(self):
        with patch("urllib.request.urlopen", side_effect=OSError("offline")):
            with self.assertRaises(currency.CurrencyRateUnavailable):
                currency.get_thb_rate_quote(cache_file=self.cache_file)


if __name__ == "__main__":
    unittest.main()
