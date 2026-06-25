"""Tests for per-pair strategy_params in whitelist."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.runtime.strategy_pair_config import (
    merge_strategy_config,
    pair_strategy_params,
    set_pair_strategy_params,
)


class TestStrategyPairConfig(unittest.TestCase):
    def test_merge_overrides_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            wl = {
                "version": 1,
                "quote_asset": "USDT",
                "assets": [
                    {
                        "symbol": "BTC",
                        "enabled": True,
                        "strategy_params": {
                            "sl_atr_mult": 3.5,
                            "rsi_min": 50,
                            "backtest_data_proxy": "IGNORED",
                        },
                    }
                ],
            }
            path = os.path.join(tmp, "coin_whitelist.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(wl, f)

            base = {"sl_atr_mult": 2.5, "rsi_min": 40, "ap_smoothing": 2}
            merged_live = merge_strategy_config(base, "BTCUSDT", project_root=tmp, for_live=True)
            merged_bt = merge_strategy_config(base, "BTCUSDT", project_root=tmp, for_live=False)

            self.assertEqual(merged_live["sl_atr_mult"], 3.5)
            self.assertEqual(merged_live["rsi_min"], 50)
            self.assertNotIn("backtest_data_proxy", merged_live)
            self.assertEqual(merged_bt["backtest_data_proxy"], "IGNORED")

    def test_set_pair_strategy_params(self):
        with tempfile.TemporaryDirectory() as tmp:
            wl = {
                "version": 1,
                "quote_asset": "USDT",
                "assets": [{"symbol": "XAUT", "enabled": True}],
            }
            path = os.path.join(tmp, "coin_whitelist.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(wl, f)
            set_pair_strategy_params(
                "XAUT",
                {"ap_smoothing": 1, "backtest_data_proxy": "PAXGUSDT"},
                project_root=tmp,
                path=path,
            )
            params = pair_strategy_params("XAUTUSDT", tmp)
            self.assertEqual(params["ap_smoothing"], 1)


if __name__ == "__main__":
    unittest.main()
