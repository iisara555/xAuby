"""Tests for backtest-only symbols and XAU→PAXG data proxy."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.backtest.engine import get_or_load_data, resolve_backtest_timeframes
from xauby.backtest.symbols import (
    backtest_timeframes_for_symbol,
    data_proxy_map,
    min_bars_for_proxy,
    resolve_data_proxy,
)


class TestBacktestSymbols(unittest.TestCase):
    def test_whitelist_is_okx_xau_only(self):
        # OKX migration: the gold slot trades XAU (OKX gold perpetual XAUUSDT =
        # XAU/USDT:USDT). XAUT is the separate spot market; PAXG is only the
        # backtest data proxy — neither is a live whitelist asset.
        import json
        import os

        path = os.path.join(ROOT, "coin_whitelist.json")
        with open(path, "r", encoding="utf-8") as f:
            wl = json.load(f)
        bases = {str(a.get("symbol", "")).upper() for a in wl.get("assets") or []}
        self.assertNotIn("DOGE", bases)
        self.assertNotIn("XAUT", bases)
        self.assertNotIn("PAXG", bases)
        self.assertIn("XAU", bases)
        self.assertNotIn("BTC", bases)
        self.assertNotIn("SOL", bases)

    def test_paxg_backtest_only_timeframes(self):
        primary, confirm = backtest_timeframes_for_symbol("PAXGUSDT")
        self.assertEqual(primary, "4h")
        self.assertEqual(confirm, "1d")

    def test_resolve_data_proxy_when_bars_low(self):
        thresh = min_bars_for_proxy("4h")
        self.assertGreater(thresh, 426)
        # XAU declares backtest_data_proxy=PAXGUSDT, so a short XAU history
        # proxies to PAXG; a full history does not.
        self.assertEqual(
            resolve_data_proxy("XAUUSDT", 426, primary_timeframe="4h"),
            "PAXGUSDT",
        )
        self.assertIsNone(
            resolve_data_proxy("XAUUSDT", thresh, primary_timeframe="4h")
        )
        self.assertIsNone(resolve_data_proxy("BTCUSDT", 50, primary_timeframe="4h"))

    def test_force_proxy_overrides_bar_count(self):
        self.assertEqual(
            resolve_data_proxy(
                "XAUUSDT",
                5000,
                primary_timeframe="4h",
                force_proxy="PAXGUSDT",
            ),
            "PAXGUSDT",
        )

    def test_data_proxy_map(self):
        proxies = data_proxy_map(str(ROOT))
        self.assertEqual(proxies.get("XAUUSDT"), "PAXGUSDT")

    @patch("xauby.backtest.data._load_candle_pair")
    def test_get_or_load_data_switches_to_proxy(self, mock_load):
        # XAU history is thin → swap to its configured proxy (PAXGUSDT).
        thin_df = pd.DataFrame({"open_time": list(range(50)), "open": [1.0] * 50})
        proxy_df = pd.DataFrame({"open_time": list(range(500)), "open": [1.0] * 500})

        def side_effect(symbol, *args, **kwargs):
            if symbol == "XAUUSDT":
                return thin_df, None
            return proxy_df, None

        mock_load.side_effect = side_effect

        primary, regime, data_sym, used_proxy = get_or_load_data("XAUUSDT")
        self.assertTrue(used_proxy)
        self.assertEqual(data_sym, "PAXGUSDT")
        self.assertEqual(len(primary), 500)
        self.assertEqual(mock_load.call_count, 2)

    @patch("xauby.backtest.engine._whitelist_timeframes_for_symbol")
    def test_resolve_tf_for_paxg_from_backtest_registry(self, mock_wl):
        mock_wl.return_value = ("4h", "1d")
        primary, regime = resolve_backtest_timeframes("PAXGUSDT", "cdc_action_zone", {})
        self.assertEqual(primary, "4h")
        self.assertEqual(regime, "1d")


if __name__ == "__main__":
    unittest.main()
