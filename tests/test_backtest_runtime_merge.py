"""Tests for runtime config merge into backtest replay."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.backtest.runtime_config import (
    build_config_used_snapshot,
    extract_checklist_config,
    merge_runtime_into_backtest_config,
    runtime_config_fingerprint,
)
from xauby.backtest.service import run_focused_backtest


class TestRuntimeConfigMerge(unittest.TestCase):
    def test_merge_overlays_risk_and_timeframes(self):
        strat_cfg = {"rsi_min": 45.0, "rsi_max": 70.0, "sl_atr_mult": 2.0}
        trading_cfg = {"risk_pct": 0.01}
        bot_state = {
            "primary_timeframe": "1h",
            "confirm_timeframe": "4h",
            "risk": {
                "risk_pct": 0.32,
                "sl_atr_mult": 2.5,
                "rsi_min": 40.0,
                "rsi_max": 75.0,
                "vol_min_ratio": 1.2,
                "use_d1_regime_filter": False,
            },
        }
        merged_strat, merged_trading, primary, regime = merge_runtime_into_backtest_config(
            bot_state, strat_cfg, trading_cfg
        )
        self.assertEqual(primary, "1h")
        self.assertEqual(regime, "4h")
        self.assertEqual(merged_strat["rsi_min"], 40.0)
        self.assertEqual(merged_strat["rsi_max"], 75.0)
        self.assertEqual(merged_trading["risk_pct"], 0.32)
        self.assertEqual(merged_trading["sl_atr_mult"], 2.5)

    def test_fingerprint_changes_with_risk(self):
        base = {"primary_timeframe": "4h", "risk": {"rsi_min": 40.0}}
        other = {"primary_timeframe": "4h", "risk": {"rsi_min": 45.0}}
        self.assertNotEqual(
            runtime_config_fingerprint(base),
            runtime_config_fingerprint(other),
        )

    def test_build_config_used_snapshot(self):
        snap = build_config_used_snapshot(
            {"rsi_min": 40.0, "rsi_max": 75.0, "vol_min_ratio": 1.0},
            {"risk_pct": 0.32, "sl_atr_mult": 2.5},
            primary_tf="1h",
            regime_tf="4h",
        )
        self.assertEqual(snap["primary_timeframe"], "1h")
        self.assertEqual(snap["confirm_timeframe"], "4h")
        self.assertEqual(snap["sl_atr_mult"], 2.5)
        self.assertEqual(snap["risk_pct"], 0.32)
        self.assertEqual(snap["checklist_gates"]["rsi_min"], 40.0)

    def test_extract_checklist_config(self):
        gates = extract_checklist_config(
            {
                "rsi_min": 50.0,
                "rsi_max": 65.0,
                "vol_min_ratio": 1.5,
                "require_fresh_zone": True,
                "fresh_zone_window": 2,
                "ap_smoothing": 1,
            }
        )
        self.assertEqual(gates["rsi_min"], 50.0)
        self.assertEqual(gates["fresh_zone_window"], 2)
        self.assertEqual(gates["ap_smoothing"], 1)

    def test_merge_ap_smoothing_from_runtime(self):
        strat_cfg = {"ap_smoothing": 2}
        bot_state = {"risk": {"ap_smoothing": 1}}
        merged_strat, _, _, _ = merge_runtime_into_backtest_config(
            bot_state, strat_cfg, {}
        )
        self.assertEqual(merged_strat["ap_smoothing"], 1)

    @patch("xauby.backtest.service.run_plugin_replay")
    @patch("xauby.backtest.service.get_or_load_data")
    @patch("xauby.backtest.service.prepare_raw_dataframe")
    @patch("xauby.backtest.service.load_bot_config")
    def test_run_focused_backtest_uses_runtime_tf(
        self, mock_cfg, mock_prepare, mock_load_data, mock_replay
    ):
        import pandas as pd

        mock_cfg.return_value = {
            "strategies": {"cdc_action_zone": {"use_d1_regime_filter": False}},
            "trading": {},
        }
        mock_load_data.return_value = (
            pd.DataFrame(
                {
                    "open_time": list(range(500)),
                    "open": [1.0] * 500,
                    "high": [1.0] * 500,
                    "low": [1.0] * 500,
                    "close": [1.0] * 500,
                    "volume": [1.0] * 500,
                }
            ),
            None,
            "XAUTUSDT",
            False,
        )
        mock_prepare.return_value = pd.DataFrame(
            {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0], "timestamp": [1]}
        )
        mock_replay.return_value = {
            "net_profit_pct": 5.0,
            "profit_factor": 1.5,
            "total_trades": 3,
            "trades": [],
        }

        bot_state = {
            "strategy_name": "cdc_action_zone",
            "primary_timeframe": "1h",
            "confirm_timeframe": "4h",
            "risk": {"rsi_min": 40.0, "rsi_max": 75.0, "sl_atr_mult": 2.5},
        }
        result = run_focused_backtest("XAUTUSDT", bot_state=bot_state)

        self.assertTrue(result.meta.run_ok)
        self.assertEqual(result.meta.primary_tf, "1h")
        self.assertIsNone(result.meta.regime_tf)
        self.assertEqual(result.meta.config_used.get("rsi_min"), 40.0)
        mock_replay.assert_called_once()
        call_kwargs = mock_replay.call_args.kwargs
        self.assertEqual(call_kwargs["primary_timeframe"], "1h")


if __name__ == "__main__":
    unittest.main()
