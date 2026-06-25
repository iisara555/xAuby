"""Tests for per-symbol backtest best-params storage."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.backtest import best_params as bp


class TestBestParamsStorage(unittest.TestCase):
    def test_save_and_load_per_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "backtest_best_params.json")
            with patch.object(bp, "BEST_PARAMS_FILE", path):
                bp.save_best_parameters(
                    "XAUTUSDT",
                    {"sl_atr_mult": 2.5, "net_profit_pct": 10.0},
                )
                bp.save_best_parameters(
                    "PAXGUSDT",
                    {"sl_atr_mult": 3.0, "net_profit_pct": 15.0},
                )
                xaut = bp.load_best_params("XAUTUSDT")
                paxg = bp.load_best_params("PAXGUSDT")
                self.assertEqual(xaut["sl_atr_mult"], 2.5)
                self.assertEqual(paxg["sl_atr_mult"], 3.0)

                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self.assertEqual(raw.get("schema_version"), 2)
                self.assertIn("XAUTUSDT", raw["by_symbol"])
                self.assertIn("PAXGUSDT", raw["by_symbol"])

    def test_legacy_flat_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "backtest_best_params.json")
            legacy = {"sl_atr_mult": 2.0, "rsi_min": 45.0, "net_profit_pct": 8.0}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(legacy, f)
            with patch.object(bp, "BEST_PARAMS_FILE", path):
                loaded = bp.load_best_params("ANYPAIR")
                self.assertEqual(loaded["sl_atr_mult"], 2.0)

    def test_v2_returns_empty_for_unknown_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "backtest_best_params.json")
            with patch.object(bp, "BEST_PARAMS_FILE", path):
                bp.save_best_parameters("BTCUSDT", {"sl_atr_mult": 2.0})
                self.assertEqual(bp.load_best_params("ETHUSDT"), {})


if __name__ == "__main__":
    unittest.main()
