import os
import re
import tempfile
import unittest
from unittest.mock import MagicMock

import yaml

from xauby.ui.chart import get_bar_chart_lines, get_chart_lines, get_zone_distribution
from xauby.ui.chart_registry import chart_display_metadata, compute_chart_dataframe
from xauby.ui.textual_tui.chart_legend import format_chart_legend
from xauby.runtime.architecture_config import strategy_chart_indicators


class TestChartRegistryPath(unittest.TestCase):
    def _mock_db(self):
        db = MagicMock()
        candles = []
        price = 2000.0
        for i in range(40):
            price += 0.3
            candles.append(
                {
                    "timestamp": 1_700_000_000 + i * 14_400,
                    "open": price - 1,
                    "high": price + 2,
                    "low": price - 2,
                    "close": price,
                    "volume": 12.0,
                }
            )
        db.get_candles.return_value = candles
        db.get_closed_trades.return_value = []
        return db

    def test_registry_flag_off_renders_lines(self):
        db = self._mock_db()
        lines = get_chart_lines(db, "XAUTUSDT", width=20, height=12, include_footer_legend=False)
        self.assertTrue(lines)
        joined = "\n".join(lines)
        self.assertNotIn("Waiting for candles", joined)

    def test_registry_flag_on_renders_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig = os.getcwd()
            try:
                os.chdir(tmp)
                cfg = {
                    "architecture": {"indicator_registry_enabled": True},
                    "strategy": {"active": "cdc_action_zone"},
                    "strategies": {"cdc_action_zone": {"ap_smoothing": 1}},
                    "cli_ui": {"chart_candle_cols": 1},
                }
                with open("bot_config.yaml", "w", encoding="utf-8") as f:
                    yaml.dump(cfg, f)
                db = self._mock_db()
                lines = get_chart_lines(
                    db,
                    "XAUTUSDT",
                    width=20,
                    height=12,
                    include_footer_legend=False,
                    strategy_name="cdc_action_zone",
                )
                self.assertTrue(lines)
            finally:
                os.chdir(orig)

    def test_supertrend_chart_uses_strategy_zones_and_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig = os.getcwd()
            try:
                os.chdir(tmp)
                cfg = {
                    "architecture": {
                        "indicator_registry_enabled": True,
                        "tui_indicator_registry": True,
                        "strategy_chart_indicators": {
                            "supertrend_ema200": ["supertrend", "ema200"],
                        },
                    },
                    "strategy": {
                        "active": "supertrend_ema200",
                        "config": {
                            "supertrend_ema200": {
                                "ema_period": 50,
                                "atr_period": 10,
                                "supertrend_mult": 3.5,
                            }
                        },
                    },
                    "cli_ui": {"chart_candle_cols": 1},
                }
                with open("bot_config.yaml", "w", encoding="utf-8") as f:
                    yaml.dump(cfg, f)
                db = self._mock_db()
                df = compute_chart_dataframe(
                    __import__("pandas").DataFrame(db.get_candles.return_value),
                    "BTCUSDT",
                    strategy_name="supertrend_ema200",
                    config=cfg,
                )
                self.assertIn("supertrend_zone", df.columns)
                self.assertIn("supertrend", df.columns)
                self.assertIn("ema200", df.columns)
                self.assertNotIn("ema12", df.columns)
                meta = chart_display_metadata("supertrend_ema200", cfg)
                self.assertEqual(meta["zone_column"], "supertrend_zone")
                self.assertEqual([x["key"] for x in meta["lines"]], ["supertrend", "ema200"])

                legend = re.sub(r"\x1b\[[0-9;]*m", "", format_chart_legend(100, strategy_name="supertrend_ema200"))
                self.assertIn("ST Bull", legend)
                self.assertIn("SuperTrend", legend)
                self.assertIn("EMA200", legend)
                self.assertNotIn("EMA12", legend)

                lines = get_chart_lines(
                    db,
                    "BTCUSDT",
                    width=20,
                    height=12,
                    include_footer_legend=True,
                    strategy_name="supertrend_ema200",
                )
                rendered = re.sub(r"\x1b\[[0-9;]*m", "", "\n".join(lines))
                self.assertIn("SuperTrend", rendered)
                self.assertIn("EMA200", rendered)
                self.assertNotIn("EMA12", rendered)
                counts = get_zone_distribution(db, "BTCUSDT", limit=20)
                self.assertTrue(set(counts).issubset({"BULL", "BEAR"}))
                bars = "\n".join(get_bar_chart_lines(db, "BTCUSDT", width=80))
                self.assertIn("ST", re.sub(r"\x1b\[[0-9;]*m", "", bars))
            finally:
                os.chdir(orig)

    def test_compute_chart_dataframe_resolves_strategy_from_passed_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig = os.getcwd()
            try:
                os.chdir(tmp)
                cfg = {
                    "architecture": {
                        "indicator_registry_enabled": True,
                        "strategy_chart_indicators": {
                            "supertrend_ema200": ["supertrend", "ema200"],
                        },
                    },
                    "strategy": {"active": "supertrend_ema200"},
                }
                df = compute_chart_dataframe(
                    __import__("pandas").DataFrame(self._mock_db().get_candles.return_value),
                    "BTCUSDT",
                    config=cfg,
                )
                self.assertIn("supertrend_zone", df.columns)
                self.assertNotIn("ema12", df.columns)
            finally:
                os.chdir(orig)

    def test_all_strategy_legends_are_strategy_specific(self):
        cfg = {
            "architecture": {
                "indicator_registry_enabled": True,
                "tui_indicator_registry": True,
                "strategy_chart_indicators": {
                    "cdc_action_zone": ["cdc_action_zone"],
                    "supertrend_ema200": ["supertrend", "ema200"],
                    "btc_ema_pullback": ["btc_ema_pullback"],
                    "ict_lite_strategy": ["ict_lite"],
                    "bbkc_squeeze": ["bbkc_squeeze"],
                    "bbrsi_mean_reversion": ["bbrsi_mean_reversion"],
                    "rsi2_meanrev": ["rsi2_meanrev"],
                    "vol_breakout": ["vol_breakout"],
                },
            }
        }
        expected = {
            "cdc_action_zone": ("zone", ["ema12", "ema26"], ["Buy zone", "EMA12"]),
            "supertrend_ema200": ("supertrend_zone", ["supertrend", "ema200"], ["ST Bull", "SuperTrend", "EMA200"]),
            "btc_ema_pullback": ("ema_pullback_zone", ["ema_fast", "ema_slow", "ema_trend"], ["Trend", "EMA Fast", "EMA Trend"]),
            "ict_lite_strategy": ("ict_zone", ["ema_fast", "ema_slow", "recent_high", "recent_low"], ["Sweep Low", "MSS", "Recent High"]),
            "bbkc_squeeze": ("zone", ["bb_upper", "bb_mid", "bb_lower", "kc_upper", "kc_lower"], ["Compressed", "KC Upper"]),
            "bbrsi_mean_reversion": ("zone", ["bb_upper", "bb_mid", "bb_lower"], ["Oversold", "BB Upper"]),
            "simple_scalp_plus": ("scalp_zone", ["hull", "vwap", "ema_fast", "ema_slow"], ["Buy zone", "Hull MA", "VWAP"]),
            "rsi2_meanrev": ("rsi2_zone", ["ema200", "sma5"], ["Buy Setup", "EMA200", "SMA5"]),
            "vol_breakout": ("vol_breakout_zone", ["range_high", "exit_ema"], ["Breakout", "Range High", "Exit EMA"]),
        }
        for strat, (zone_col, line_keys, legend_tokens) in expected.items():
            meta = chart_display_metadata(strat, cfg)
            self.assertEqual(meta["zone_column"], zone_col, strat)
            self.assertEqual([x["key"] for x in meta["lines"]], line_keys, strat)
            legend = re.sub(r"\x1b\[[0-9;]*m", "", format_chart_legend(100, strategy_name=strat))
            for token in legend_tokens:
                self.assertIn(token, legend, strat)
        for strat in ("btc_ema_pullback", "ict_lite_strategy", "supertrend_ema200"):
            legend = re.sub(r"\x1b\[[0-9;]*m", "", format_chart_legend(100, strategy_name=strat))
            self.assertNotIn("EMA12", legend, strat)
            self.assertNotIn("Buy zone", legend, strat)

    def test_strategy_chart_map_falls_back_to_same_name_indicator(self):
        cfg = {
            "architecture": {
                "indicator_registry_enabled": True,
                "strategy_chart_indicators": {
                    "simple_scalp_plus": [],
                },
            }
        }
        self.assertEqual(strategy_chart_indicators(cfg, "simple_scalp_plus"), ["simple_scalp_plus"])
        meta = chart_display_metadata("simple_scalp_plus", cfg)
        self.assertEqual(meta["zone_column"], "scalp_zone")
        self.assertEqual([x["key"] for x in meta["lines"]], ["hull", "vwap", "ema_fast", "ema_slow"])

    def test_compact_legend_keeps_zone_labels_distinct(self):
        """Narrow widths must not collapse multi-word zones into duplicates.

        e.g. "ST Bull"/"ST Bear" must not both render as "ST", and
        "PreBuy 2"/"PreBuy 1" must not both render as "PreBuy".
        """
        for strat, must_have in {
            "supertrend_ema200": ["ST Bull", "ST Bear"],
            "cdc_action_zone": ["PreBuy 2", "PreBuy 1", "PreSell 1", "PreSell 2"],
        }.items():
            legend = re.sub(
                r"\x1b\[[0-9;]*m",
                "",
                format_chart_legend(60, compact=True, strategy_name=strat),
            )
            for token in must_have:
                self.assertIn(token, legend, f"{strat} compact legend missing {token!r}")
