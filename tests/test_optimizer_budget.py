"""Tests for low-CPU optimizer budgets."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from unittest.mock import patch

from xauby.backtest.optimizer import (
    _budgeted_combos,
    _max_grid_runs,
    _optimization_grid,
    optimize_grid_for_symbol,
)


class TestOptimizerBudget(unittest.TestCase):
    def test_budget_caps_full_grid(self):
        cfg = {
            "sl_atr_mult": 2.0,
            "trailing_atr_mult": 1.5,
            "rsi_min": 0.0,
            "rsi_max": 100.0,
            "vol_min_ratio": 0.0,
            "breakeven_sl_enabled": True,
            "breakeven_activation_atr_mult": 1.5,
            "breakeven_buffer_atr_mult": 0.1,
        }
        grid = _optimization_grid(cfg, compact=False)
        keys = list(grid.keys())
        combos = _budgeted_combos(keys, grid, cfg, 4)
        self.assertEqual(len(combos), 4)

    def test_max_runs_from_config(self):
        cfg = {"backtest": {"optimizer": {"max_runs": 12, "compact_max_runs": 7}}}
        self.assertEqual(_max_grid_runs(cfg, compact=False), 12)
        self.assertEqual(_max_grid_runs(cfg, compact=True), 7)

    def test_optimize_does_not_stringify_none_strategy(self):
        seen = {}

        class Bundle:
            strat_cfg = {
                "sl_atr_mult": 2.0,
                "trailing_atr_mult": 1.5,
                "rsi_min": 0.0,
                "rsi_max": 100.0,
                "vol_min_ratio": 0.0,
            }
            merged_cfg = {"backtest": {"optimizer": {"max_runs": 1, "max_bars": 0}}}

        def fake_bundle(symbol, *, strategy_name=None, **kwargs):
            seen["strategy_name"] = strategy_name
            return Bundle()

        class Meta:
            run_ok = True
            config_used = {}
            data_symbol = "BTCUSDT"
            symbol = "BTCUSDT"
            used_data_proxy = False

        class Result:
            meta = Meta()
            stats = {
                "net_profit_pct": 1.0,
                "profit_factor": 1.2,
                "win_rate": 50.0,
                "total_trades": 2,
                "max_drawdown_pct": 1.0,
            }

        with patch("xauby.backtest.optimizer.load_backtest_replay_bundle", fake_bundle), \
             patch("xauby.backtest.optimizer.run_replay_from_bundle", return_value=Result()), \
             patch("xauby.backtest.optimizer._save_best"):
            optimize_grid_for_symbol("BTCUSDT", strategy_name=None)

        self.assertIsNone(seen["strategy_name"])


if __name__ == "__main__":
    unittest.main()
