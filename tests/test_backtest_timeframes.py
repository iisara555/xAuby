"""Tests for multi-timeframe backtest data + resolution."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from xauby.backtest.engine import (
    _cache_path,
    default_candle_limit,
    get_or_load_data,
    load_or_download_candles,
    resolve_backtest_timeframes,
    run_plugin_replay,
)


class TestCacheAndLimits(unittest.TestCase):
    def test_cache_path_includes_timeframe(self):
        path = _cache_path("XAUTUSDT", "1h")
        self.assertIn("backtest_candles_th_1h_xautusdt.csv", path)

    def test_default_candle_limit_1h(self):
        self.assertEqual(default_candle_limit("1h"), 9000)

    def test_default_candle_limit_1d(self):
        self.assertEqual(default_candle_limit("1d"), 1000)

    @patch("xauby.backtest.data.download_klines")
    def test_load_or_download_uses_tf_cache(self, mock_dl):
        import time
        mock_dl.return_value = pd.DataFrame(
            {
                "open_time": [int(time.time() * 1000)],
                "open": [1.0],
                "high": [1.0],
                "low": [1.0],
                "close": [1.0],
                "volume": [1.0],
                "close_time": [int(time.time() * 1000) + 3600000],
                "quote_volume": [1.0],
                "count": [1],
                "taker_buy_base": [0.0],
                "taker_buy_quote": [0.0],
                "ignore": [0],
                "timestamp": [int(time.time())],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            with patch("xauby.backtest.data.CACHE_DIR", tmp):
                path = _cache_path("BTCUSDT", "1h")
                df1 = load_or_download_candles("BTCUSDT", "1h", limit=1, force_download=True)
                self.assertTrue(os.path.exists(path))
                mock_dl.reset_mock()
                df2 = load_or_download_candles("BTCUSDT", "1h", limit=1, force_download=False)
                mock_dl.assert_not_called()
                self.assertEqual(len(df1), len(df2))


class TestResolveTimeframes(unittest.TestCase):
    @patch("xauby.backtest.data._whitelist_timeframes_for_symbol")
    def test_resolve_from_whitelist(self, mock_wl):
        mock_wl.return_value = ("1h", "4h")
        primary, regime = resolve_backtest_timeframes(
            "XAUTUSDT", "cdc_action_zone", {}
        )
        self.assertEqual(primary, "1h")
        self.assertEqual(regime, "4h")

    @patch("xauby.backtest.data._whitelist_timeframes_for_symbol")
    def test_override_beats_whitelist(self, mock_wl):
        mock_wl.return_value = ("4h", "1d")
        primary, regime = resolve_backtest_timeframes(
            "XAUTUSDT",
            "cdc_action_zone",
            {},
            primary_override="1h",
            regime_override="4h",
        )
        self.assertEqual(primary, "1h")
        self.assertEqual(regime, "4h")

    @patch("xauby.backtest.data._whitelist_timeframes_for_symbol")
    def test_config_fallback_when_no_whitelist(self, mock_wl):
        mock_wl.return_value = (None, None)
        cfg = {
            "strategy_mode": {
                "active": "trend_only",
                "trend_only": {
                    "primary_timeframe": "1h",
                    "confirm_timeframe": "4h",
                },
            },
            "strategies": {"cdc_action_zone": {}},
        }
        primary, regime = resolve_backtest_timeframes(
            "XAUTUSDT", "cdc_action_zone", cfg
        )
        self.assertEqual(primary, "1h")
        self.assertEqual(regime, "4h")


class TestGetOrLoadData(unittest.TestCase):
    @patch("xauby.backtest.data.resolve_data_proxy")
    @patch("xauby.backtest.data._load_candle_pair")
    def test_regime_none_when_not_requested(self, mock_load, mock_resolve_proxy):
        mock_resolve_proxy.return_value = None
        mock_load.return_value = (
            pd.DataFrame({"open": [1.0] * 500, "timestamp": list(range(500))}),
            None,
        )
        primary, regime, data_sym, used_proxy = get_or_load_data(
            "XAUTUSDT", primary_timeframe="1h", regime_timeframe=None
        )
        self.assertFalse(primary.empty)
        self.assertIsNone(regime)
        self.assertEqual(data_sym, "XAUTUSDT")
        self.assertFalse(used_proxy)
        mock_load.assert_called_once()


class TestRunPluginReplayTimeframes(unittest.TestCase):
    @patch("xauby.observability.replay.ReplayEngine")
    @patch("xauby.observability.replay.PositionSimulator")
    @patch("xauby.strategies.sandbox.StrategyRunner")
    @patch("xauby.strategies.load_strategy")
    def test_run_plugin_replay_sets_timeframe_primary(
        self, mock_load_strat, _runner, _sim, mock_engine_cls
    ):
        strategy = MagicMock()
        strategy.min_bars = 50
        mock_load_strat.return_value = strategy

        engine = MagicMock()
        engine.replay_bars.return_value = ([], [], 1000.0, [])
        mock_engine_cls.return_value = engine
        mock_engine_cls.stats.return_value = {
            "net_profit_pct": 0.0,
            "win_rate": 0.0,
            "total_trades": 0,
            "profit_factor": 0.0,
        }

        df = pd.DataFrame(
            {
                "open": [100.0] * 120,
                "high": [101.0] * 120,
                "low": [99.0] * 120,
                "close": [100.5] * 120,
                "volume": [1000.0] * 120,
                "timestamp": list(range(120)),
            }
        )

        run_plugin_replay(
            df,
            strategy_config={"use_d1_regime_filter": False},
            engine_config={},
            symbol="XAUTUSDT",
            strategy_name="cdc_action_zone",
            primary_timeframe="1h",
            regime_timeframe="4h",
        )

        _, kwargs = mock_engine_cls.call_args
        self.assertEqual(kwargs["timeframe_primary"], "1h")
        self.assertIsNone(kwargs["timeframe_regime"])
        engine.replay_bars.assert_called_once()
        self.assertEqual(engine.replay_bars.call_args.kwargs.get("min_bars"), 50)

    @patch("xauby.observability.replay.ReplayEngine")
    @patch("xauby.observability.replay.PositionSimulator")
    @patch("xauby.strategies.sandbox.StrategyRunner")
    @patch("xauby.strategies.load_strategy")
    def test_regime_passed_when_filter_on(
        self, mock_load_strat, _runner, _sim, mock_engine_cls
    ):
        strategy = MagicMock()
        strategy.min_bars = 100
        mock_load_strat.return_value = strategy

        engine = MagicMock()
        engine.replay_bars.return_value = ([], [], 1000.0, [])
        mock_engine_cls.return_value = engine
        mock_engine_cls.stats.return_value = {
            "net_profit_pct": 0.0,
            "win_rate": 0.0,
            "total_trades": 0,
            "profit_factor": 0.0,
        }

        df = pd.DataFrame(
            {
                "open": [1.0],
                "high": [1.0],
                "low": [1.0],
                "close": [1.0],
                "volume": [1.0],
                "timestamp": [1],
            }
        )
        df_regime = pd.DataFrame(
            {
                "open": [1.0],
                "high": [1.0],
                "low": [1.0],
                "close": [1.0],
                "volume": [1.0],
                "timestamp": [1],
            }
        )

        run_plugin_replay(
            df,
            strategy_config={"use_d1_regime_filter": True},
            engine_config={},
            symbol="XAUTUSDT",
            strategy_name="cdc_action_zone",
            df_regime=df_regime,
            primary_timeframe="1h",
            regime_timeframe="4h",
        )

        _, kwargs = mock_engine_cls.call_args
        self.assertEqual(kwargs["timeframe_primary"], "1h")
        self.assertEqual(kwargs["timeframe_regime"], "4h")


if __name__ == "__main__":
    unittest.main()
