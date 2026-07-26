"""Backtest data layer must serve OKX, and must never fail silently.

The bug this pins: bot_config.yaml has set backtest.data_source/data_base_url to
OKX since the swap migration, but download_klines could only build Binance kline
URLs. An unknown host fell through to /api/v1/klines, the request failed, and
`except Exception: break` turned it into an empty DataFrame — so backtests ran on
whatever stale cache existed (Binance spot) with no error anywhere.
"""
from __future__ import annotations

import unittest
from unittest import mock

import pandas as pd

from xauby.backtest import okx_data
from xauby.backtest.data import _source_tag_from_url, download_klines, is_okx_url


# One confirmed 4h candle in OKX v5 order:
# [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
def _row(ts_ms: int, close: float, *, confirm: str = "1"):
    return [str(ts_ms), "100", "110", "90", str(close), "5", "6", "700", confirm]


class TestUrlClassification(unittest.TestCase):
    def test_recognises_okx(self):
        self.assertTrue(is_okx_url("https://www.okx.com"))
        self.assertTrue(is_okx_url("https://WWW.OKX.COM/"))
        self.assertTrue(is_okx_url("www.okx.com"))  # scheme-less
        self.assertFalse(is_okx_url("https://api.binance.com"))
        self.assertFalse(is_okx_url("https://api.binance.th"))

    def test_matches_the_host_not_a_substring(self):
        # "okx.com.example.net" contains "okx.com" but is somebody else's host;
        # routing to the OKX client would send instIds to whoever owns it.
        self.assertFalse(is_okx_url("https://okx.com.example.net"))
        self.assertFalse(is_okx_url("https://notokx.com"))
        self.assertFalse(is_okx_url("https://binance.com.okx.com.evil.test"))
        # Real subdomains still count.
        self.assertTrue(is_okx_url("https://aws.okx.com"))

    def test_binance_host_matching_is_equally_strict(self):
        from xauby.backtest.data import is_binance_url

        self.assertTrue(is_binance_url("https://api.binance.com"))
        self.assertTrue(is_binance_url("https://api.binance.th"))
        self.assertFalse(is_binance_url("https://binance.com.example.net"))
        self.assertFalse(is_binance_url("https://www.okx.com"))

    def test_cache_tag_separates_okx_from_binance_th(self):
        # Before the fix OKX fell into the "th" bucket, so an OKX-configured run
        # could be served a cached Binance.TH frame for the same symbol/timeframe.
        self.assertEqual(_source_tag_from_url("https://www.okx.com"), "okx")
        self.assertEqual(_source_tag_from_url("https://api.binance.th"), "th")
        self.assertEqual(_source_tag_from_url("https://api.binance.com"), "global")
        self.assertNotEqual(
            _source_tag_from_url("https://www.okx.com"),
            _source_tag_from_url("https://api.binance.th"),
        )


class TestUnknownHostFailsLoudly(unittest.TestCase):
    def test_unknown_host_raises_instead_of_empty_frame(self):
        with self.assertRaises(ValueError) as ctx:
            download_klines("XAUUSDT", "4h", limit=10, base_url="https://example.invalid")
        self.assertIn("unsupported backtest data host", str(ctx.exception))

    def test_binance_hosts_still_route(self):
        seen = {}

        class EmptyOk:
            status_code = 200

            def json(self):
                return []  # valid "no candles", so the call returns quietly

        def fake_get(url, params=None, timeout=None, **kw):
            seen["url"] = url
            return EmptyOk()

        with mock.patch("xauby.backtest.data.requests.get", fake_get):
            download_klines("BTCUSDT", "4h", limit=1, base_url="https://api.binance.com")
        self.assertTrue(seen["url"].endswith("/api/v3/klines"))

        with mock.patch("xauby.backtest.data.requests.get", fake_get):
            download_klines("BTCUSDT", "4h", limit=1, base_url="https://api.binance.th")
        self.assertTrue(seen["url"].endswith("/api/v1/klines"))


class TestBinanceFailuresAreNotSilent(unittest.TestCase):
    """A venue that refuses to serve us must not look like an empty symbol."""

    def _resp(self, status=200, payload=None):
        class R:
            status_code = status

            def json(self):
                return payload

        return R()

    def test_geo_block_error_object_raises(self):
        # Binance answers a restricted region with HTTP 200 and a JSON object.
        blocked = {"code": 0, "msg": "Service unavailable from a restricted location"}
        with mock.patch(
            "xauby.backtest.data.requests.get",
            lambda *a, **k: self._resp(200, blocked),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                download_klines("PAXGUSDT", "4h", limit=10,
                                base_url="https://api.binance.com")
        self.assertIn("restricted location", str(ctx.exception))

    def test_http_error_status_raises(self):
        with mock.patch(
            "xauby.backtest.data.requests.get",
            lambda *a, **k: self._resp(451, None),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                download_klines("BTCUSDT", "4h", limit=10,
                                base_url="https://api.binance.com")
        self.assertIn("451", str(ctx.exception))

    def test_genuinely_empty_history_still_returns_empty(self):
        # An empty *list* is a real answer: the symbol has no candles. That must
        # stay a quiet empty frame, not an exception.
        with mock.patch(
            "xauby.backtest.data.requests.get",
            lambda *a, **k: self._resp(200, []),
        ):
            df = download_klines("NEWCOINUSDT", "4h", limit=10,
                                 base_url="https://api.binance.com")
        self.assertTrue(df.empty)

    def test_partial_pagination_keeps_what_it_got(self):
        # First page succeeds, second fails: return page one rather than raising,
        # since a mid-walk error is just an early stop.
        calls = {"n": 0}
        page = [
            [1_600_000_000_000 + i * 14_400_000, "1", "2", "0.5", "1.5", "10",
             1_600_000_000_000 + i * 14_400_000 + 14_399_999, "15", 3, "5", "7", "0"]
            for i in range(1000)
        ]

        def fake_get(*a, **k):
            calls["n"] += 1
            return self._resp(200, page if calls["n"] == 1 else
                              {"code": -1, "msg": "rate limited"})

        with mock.patch("xauby.backtest.data.requests.get", fake_get):
            df = download_klines("BTCUSDT", "4h", limit=5000,
                                 base_url="https://api.binance.com")
        self.assertFalse(df.empty)
        self.assertEqual(len(df), 1000)


class TestOkxRouting(unittest.TestCase):
    def test_okx_base_url_hits_the_v5_candles_endpoint(self):
        calls = []

        def fake_get_json(path, params, *, base_url=None, **kw):
            calls.append((path, dict(params)))
            return {"code": "0", "data": [_row(1_600_000_000_000, 101.0)]}

        with mock.patch.object(okx_data, "get_json", fake_get_json):
            df = download_klines(
                "XAUUSDT", "4h", limit=1, base_url="https://www.okx.com"
            )

        self.assertTrue(calls, "OKX endpoint was never called")
        path, params = calls[0]
        self.assertEqual(path, "/api/v5/market/history-candles")
        self.assertEqual(params["instId"], "XAU-USDT-SWAP")
        self.assertEqual(params["bar"], "4H")
        self.assertFalse(df.empty)
        self.assertEqual(float(df.iloc[0]["close"]), 101.0)

    def test_frame_matches_the_binance_shaped_contract(self):
        expected = [
            "open_time", "open", "high", "low", "close", "volume", "close_time",
            "quote_volume", "count", "taker_buy_base", "taker_buy_quote",
            "ignore", "timestamp",
        ]
        with mock.patch.object(
            okx_data, "get_json",
            lambda *a, **k: {"code": "0", "data": [_row(1_600_000_000_000, 101.0)]},
        ):
            df = download_klines("XAUUSDT", "4h", limit=1, base_url="https://www.okx.com")
        self.assertEqual(list(df.columns), expected)
        # close_time must span exactly one bar minus a millisecond.
        row = df.iloc[0]
        self.assertEqual(int(row["close_time"]) - int(row["open_time"]), 4 * 3_600_000 - 1)
        self.assertEqual(int(row["timestamp"]), int(row["open_time"]) // 1000)


class TestOkxHelpers(unittest.TestCase):
    def test_bar_mapping_uses_okx_casing(self):
        self.assertEqual(okx_data.okx_bar("4h"), "4H")
        self.assertEqual(okx_data.okx_bar("1d"), "1D")
        self.assertEqual(okx_data.okx_bar("15m"), "15m")

    def test_inst_id_derivation(self):
        self.assertEqual(okx_data.to_inst_id("XAUUSDT"), "XAU-USDT-SWAP")
        self.assertEqual(okx_data.to_inst_id("BTCUSDT"), "BTC-USDT-SWAP")
        self.assertEqual(
            okx_data.to_inst_id("BTCUSDT", market_type="spot"), "BTC-USDT"
        )
        # Already-explicit instIds pass through untouched.
        self.assertEqual(okx_data.to_inst_id("XAU-USDT-SWAP"), "XAU-USDT-SWAP")

    def test_non_usdt_symbol_is_rejected_not_guessed(self):
        # A wrong instId returns an empty result that looks like "no history",
        # which is exactly the failure mode this whole change exists to remove.
        with self.assertRaises(ValueError):
            okx_data.to_inst_id("XAUBUSD")

    def test_normalize_symbol_round_trip(self):
        self.assertEqual(okx_data.normalize_symbol("XAU-USDT-SWAP"), "XAUUSDT")
        self.assertEqual(okx_data.normalize_symbol("BTC-USDT"), "BTCUSDT")

    def test_unconfirmed_candle_is_dropped(self):
        rows = [_row(1_600_000_000_000, 101.0), _row(1_600_014_400_000, 102.0, confirm="0")]
        df = okx_data.candles_to_cache_df(rows, "4h")
        self.assertEqual(len(df), 1)
        self.assertEqual(float(df.iloc[0]["close"]), 101.0)

    def test_rows_are_deduped_and_sorted_oldest_first(self):
        newer, older = 1_600_014_400_000, 1_600_000_000_000
        df = okx_data.candles_to_cache_df(
            [_row(newer, 102.0), _row(older, 101.0), _row(newer, 102.0)], "4h"
        )
        self.assertEqual(len(df), 2)
        self.assertEqual(list(df["open_time"]), [older, newer])

    def test_empty_payload_returns_empty_frame(self):
        self.assertTrue(okx_data.candles_to_cache_df([], "4h").empty)
        with mock.patch.object(okx_data, "get_json", lambda *a, **k: {"code": "0", "data": []}):
            self.assertTrue(
                okx_data.download_okx_klines("XAUUSDT", "4h", limit=5).empty
            )

    def test_pagination_walks_back_with_the_after_cursor(self):
        pages = [
            [_row(1_600_028_800_000, 103.0), _row(1_600_014_400_000, 102.0)],
            [_row(1_600_000_000_000, 101.0)],
            [],
        ]
        seen_cursors = []

        def fake_get_json(path, params, **kw):
            seen_cursors.append(params.get("after"))
            return {"code": "0", "data": pages.pop(0) if pages else []}

        with mock.patch.object(okx_data, "get_json", fake_get_json):
            rows = okx_data.fetch_candles("XAU-USDT-SWAP", "4h", limit=250)

        self.assertEqual(seen_cursors[0], None)
        self.assertEqual(seen_cursors[1], "1600014400000")
        self.assertEqual([int(r[0]) for r in rows],
                         [1_600_000_000_000, 1_600_014_400_000, 1_600_028_800_000])

    def test_non_zero_api_code_raises(self):
        class Resp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"code": "51001", "msg": "instrument does not exist"}

        with mock.patch.object(okx_data, "requests") as req:
            req.get.return_value = Resp()
            with mock.patch.object(okx_data.time, "sleep", lambda *_: None):
                with self.assertRaises(RuntimeError) as ctx:
                    okx_data.get_json("/api/v5/market/history-candles", {}, attempts=1)
        self.assertIn("OKX request failed", str(ctx.exception))


class TestScriptStillReExports(unittest.TestCase):
    """scripts/evaluate_okx_xau_migration.py and its test import these names."""

    def test_back_compat_names(self):
        from scripts import fetch_okx_xau_history as script

        self.assertIs(script.okx_bar, okx_data.okx_bar)
        self.assertIs(script.normalize_symbol, okx_data.normalize_symbol)
        self.assertIs(script.candles_to_cache_df, okx_data.candles_to_cache_df)
        self.assertIs(script.fetch_candles, okx_data.fetch_candles)
        self.assertEqual(script.OKX_BASE_URL, okx_data.OKX_BASE_URL)
        self.assertEqual(script.DEFAULT_INST_ID, "XAU-USDT-SWAP")
        self.assertTrue(
            script.candle_cache_path("XAU-USDT-SWAP", "4h").endswith(
                "backtest_candles_okx_4h_xauusdt.csv"
            )
        )


if __name__ == "__main__":
    unittest.main()
