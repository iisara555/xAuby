"""Tests for backtest mobile layout helpers and CPU-oriented optimizer paths."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.backtest.engine import _optimization_grid, optimize_grid_for_symbol
from xauby.backtest.presenter import format_summary_lines, BacktestViewModel
from xauby.ui.textual_tui.layout import layout_width_tier, sync_backtest_layout


class TestLayoutHelpers(unittest.TestCase):
    def test_layout_width_tier_buckets(self):
        self.assertEqual(layout_width_tier(60), "phone")
        self.assertEqual(layout_width_tier(90), "tablet")
        self.assertEqual(layout_width_tier(120), "wide")

    def test_sync_backtest_layout_phone_class(self):
        split = MagicMock()
        sync_backtest_layout(split, 60)
        split.add_class.assert_any_call("stacked")
        split.add_class.assert_any_call("phone-layout")


class TestCompactPresenter(unittest.TestCase):
    def test_phone_summary_is_single_block(self):
        vm = BacktestViewModel(
            symbol="XAUTUSDT",
            strategy_name="cdc_action_zone",
            period_label="2024-01 → 2024-06",
            total_trades=10,
            net_return_pct=5.0,
            profit_factor=1.1,
            win_rate_pct=40.0,
            max_drawdown_pct=3.0,
            status="done",
            primary_tf="4h",
            regime_tf="off",
            initial_balance=1000.0,
            final_balance=1050.0,
            wins=4,
            losses=6,
            config_used={"sl_atr_mult": 2.5, "rsi_min": 40, "rsi_max": 75, "vol_min_ratio": 1.0},
        )
        lines = format_summary_lines(vm, 60, compact=True)
        text = "\n".join(lines)
        self.assertIn("XAUTUSDT", text)
        self.assertNotIn("Gross Profit", text)
        self.assertIn("Cfg SL2.5", text)


class TestOptimizerGrid(unittest.TestCase):
    def test_compact_grid_smaller_than_full(self):
        cfg = {
            "sl_atr_mult": 2.5,
            "trailing_atr_mult": 1.5,
            "rsi_min": 40.0,
            "rsi_max": 75.0,
            "vol_min_ratio": 1.0,
            "require_fresh_zone": True,
            "fresh_zone_window": 3,
            "ap_smoothing": 2,
        }
        full = _optimization_grid(cfg, compact=False)
        compact = _optimization_grid(cfg, compact=True)
        full_runs = 1
        compact_runs = 1
        for values in full.values():
            full_runs *= len(values)
        for values in compact.values():
            compact_runs *= len(values)
        self.assertLess(compact_runs, full_runs)

    @patch("xauby.backtest.optimizer.run_replay_from_bundle")
    @patch("xauby.backtest.optimizer.load_backtest_replay_bundle")
    def test_optimize_loads_bundle_once(self, mock_load, mock_replay):
        bundle = MagicMock()
        bundle.strat_cfg = {
            "sl_atr_mult": 2.5,
            "trailing_atr_mult": 1.5,
            "rsi_min": 0.0,
            "rsi_max": 100.0,
            "vol_min_ratio": 0.0,
            "require_fresh_zone": False,
            "fresh_zone_window": 3,
            "ap_smoothing": 2,
        }
        mock_load.return_value = bundle
        mock_replay.return_value = MagicMock(
            meta=MagicMock(run_ok=True, config_used={}),
            stats={"net_profit_pct": 1.0, "win_rate": 50.0, "total_trades": 2, "max_drawdown_pct": 1.0, "profit_factor": 1.1},
        )

        with patch("xauby.backtest.optimizer._save_best"):
            optimize_grid_for_symbol("XAUTUSDT", compact=True)

        mock_load.assert_called_once()
        self.assertGreater(mock_replay.call_count, 0)


if __name__ == "__main__":
    unittest.main()
