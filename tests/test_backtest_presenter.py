"""Unit tests for backtest presenter and max drawdown stats."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.backtest.presenter import (
    BacktestViewModel,
    build_backtest_view_model,
    build_parity_checks,
    confidence_score,
    format_parity_lines,
    format_summary_lines,
)
from xauby.backtest.service import BacktestRunMeta, BacktestRunResult
from xauby.observability.replay import ReplayEngine, SimTrade


class TestMaxDrawdown(unittest.TestCase):
    def test_max_drawdown_from_trades(self):
        trades = [
            SimTrade(0, 1, 100.0, 90.0, -50.0, -5.0, "SL"),
            SimTrade(2, 3, 90.0, 100.0, 30.0, 3.0, "TP"),
        ]
        stats = ReplayEngine.stats(trades, 1000.0, 980.0)
        self.assertIn("max_drawdown_pct", stats)
        self.assertAlmostEqual(stats["max_drawdown_pct"], 5.0, places=2)

    def test_zero_trades_zero_drawdown(self):
        stats = ReplayEngine.stats([], 1000.0, 1000.0)
        self.assertEqual(stats["max_drawdown_pct"], 0.0)


class TestParityScoring(unittest.TestCase):
    def _meta(self, **kwargs) -> BacktestRunMeta:
        defaults = dict(
            symbol="BTCUSDT",
            strategy_name="cdc_action_zone",
            primary_tf="4h",
            regime_tf="1d",
            period_start="2023-01",
            period_end="2026-06",
            bars=500,
            use_d1_regime_filter=False,
            fee_pct=0.001,
            regime_bars=0,
            run_ok=True,
        )
        defaults.update(kwargs)
        return BacktestRunMeta(**defaults)

    def test_four_pass_gives_100(self):
        meta = self._meta()
        stats = {"total_trades": 10, "net_profit_pct": 5.0}
        checks = build_parity_checks(meta, stats)
        self.assertEqual(confidence_score(checks), 100)

    def test_one_fail_reduces_confidence(self):
        meta = self._meta(fee_pct=0.0)
        stats = {"total_trades": 10}
        checks = build_parity_checks(meta, stats)
        self.assertEqual(confidence_score(checks), 80)
        fees = next(c for c in checks if c.name == "Fees")
        self.assertFalse(fees.passed)

    def test_checklist_parity_uses_strategy_gates(self):
        meta = self._meta(
            config_used={
                "checklist_gates": {
                    "rsi_min": 50.0,
                    "rsi_max": 65.0,
                    "vol_min_ratio": 1.5,
                    "require_fresh_zone": True,
                    "fresh_zone_window": 2,
                    "ap_smoothing": 1,
                },
                "last_checklist": [
                    {"label": "RSI(14)", "ok": True, "value": 55.0},
                    {"label": "Vol Ratio", "ok": False, "value": "1.2x"},
                ],
            }
        )
        checks = build_parity_checks(meta, {"total_trades": 3})
        chk = next(c for c in checks if c.name == "Checklist")
        self.assertTrue(chk.passed)
        self.assertIn("RSI 50-65", chk.detail)
        self.assertIn("1/2 OK", chk.detail)

    def test_d1_fail_when_filter_on_no_data(self):
        meta = self._meta(use_d1_regime_filter=True, regime_bars=0)
        checks = build_parity_checks(meta, {"total_trades": 0})
        d1 = next(c for c in checks if c.name == "D1 Filter")
        self.assertFalse(d1.passed)


class TestFormatting(unittest.TestCase):
    def test_summary_contains_key_fields(self):
        meta = BacktestRunMeta(
            symbol="BTCUSDT",
            strategy_name="cdc_action_zone",
            primary_tf="4h",
            regime_tf="1d",
            period_start="2023-01",
            period_end="2026-06",
            bars=500,
            use_d1_regime_filter=False,
            fee_pct=0.001,
            run_ok=True,
        )
        stats = {
            "total_trades": 42,
            "net_profit_pct": 18.4,
            "profit_factor": 1.72,
            "win_rate": 47.0,
            "max_drawdown_pct": 7.8,
        }
        result = BacktestRunResult(stats=stats, meta=meta)
        vm = build_backtest_view_model(result, status="done")
        lines = format_summary_lines(vm, 80)
        text = "\n".join(lines)
        self.assertIn("xauby_actionzone", text)
        self.assertIn("BTCUSDT", text)
        self.assertIn("2023-01", text)
        self.assertIn("42", text)

    def test_summary_shows_config_used_and_timeframes(self):
        meta = BacktestRunMeta(
            symbol="XAUTUSDT",
            strategy_name="cdc_action_zone",
            primary_tf="1h",
            regime_tf=None,
            period_start="2023-01",
            period_end="2026-06",
            bars=100,
            use_d1_regime_filter=False,
            fee_pct=0.001,
            run_ok=True,
            config_used={
                "sl_atr_mult": 2.5,
                "trailing_atr_mult": 1.5,
                "rsi_min": 40.0,
                "rsi_max": 75.0,
                "vol_min_ratio": 1.0,
                "net_profit_pct": 12.0,
            },
        )
        result = BacktestRunResult(
            stats={"total_trades": 5, "net_profit_pct": 12.0, "profit_factor": 1.2, "win_rate": 50.0},
            meta=meta,
        )
        vm = build_backtest_view_model(result, status="done")
        text = "\n".join(format_summary_lines(vm, 80))
        self.assertIn("1h / off", text)
        self.assertIn("Config Used (This Run)", text)
        self.assertIn("2.5", text)
        self.assertIn("Fresh Zone", text)

    def test_summary_best_params_labeled_per_symbol(self):
        vm = BacktestViewModel(
            symbol="PAXGUSDT",
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
            best_params={"sl_atr_mult": 3.0, "rsi_min": 45.0, "rsi_max": 70.0, "vol_min_ratio": 1.0},
        )
        text = "\n".join(format_summary_lines(vm, 80))
        self.assertIn("PAXGUSDT", text)
        self.assertIn("Recommended Config (Best Profit — PAXGUSDT)", text)

    def test_parity_contains_confidence(self):
        meta = BacktestRunMeta(
            symbol="BTCUSDT",
            strategy_name="cdc_action_zone",
            primary_tf="4h",
            regime_tf=None,
            period_start="2023-01",
            period_end="2026-06",
            bars=100,
            use_d1_regime_filter=False,
            fee_pct=0.001,
            run_ok=True,
        )
        result = BacktestRunResult(stats={"total_trades": 5}, meta=meta)
        vm = build_backtest_view_model(result, status="done")
        lines = format_parity_lines(vm, 80)
        text = "\n".join(lines)
        self.assertIn("Analyze()", text)
        self.assertIn("Confidence", text)
        self.assertIn("100/100", text)


if __name__ == "__main__":
    unittest.main()
