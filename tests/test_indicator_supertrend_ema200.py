import unittest

import numpy as np
import pandas as pd

from xauby.strategies.indicators.indicator_ema200 import EMA200Indicator, _ema_np
from xauby.strategies.indicators.indicator_supertrend import SuperTrendIndicator
from xauby.strategies.supertrend_ema200.strategy import SuperTrendEMA200Strategy
from xauby.strategies.context import MarketContext


class TestIndicatorSupertrendEma200(unittest.TestCase):
    def _sample_df(self, n: int = 260) -> pd.DataFrame:
        rows = []
        price = 90000.0
        for i in range(n):
            price += 20.0 if i % 5 else -10.0
            rows.append(
                {
                    "timestamp": 1_700_000_000 + i * 3600,
                    "open": price - 50,
                    "high": price + 80,
                    "low": price - 80,
                    "close": price,
                    "volume": 100.0,
                }
            )
        return pd.DataFrame(rows)

    def test_ema_np_matches_strategy_helper(self):
        close = np.array([1.0, 2.0, 3.0, 4.0, 5.0], dtype="float64")
        self.assertTrue(np.allclose(_ema_np(close, 3), SuperTrendEMA200Strategy._ema_np(close, 3)))

    def test_supertrend_plugin_outputs_columns(self):
        df = self._sample_df()
        ind = SuperTrendIndicator()
        out = ind.compute(df, {"atr_period": 10, "supertrend_mult": 3.5})
        self.assertIn("supertrend", out.columns)
        self.assertIn("supertrend_bull", out.columns)

    def test_supertrend_plugin_matches_strategy_helper(self):
        df = self._sample_df()
        cfg = {"atr_period": 10, "supertrend_mult": 3.5}

        out = SuperTrendIndicator().compute(df, cfg)
        high = df["high"].to_numpy(dtype="float64", copy=False)
        low = df["low"].to_numpy(dtype="float64", copy=False)
        close = df["close"].to_numpy(dtype="float64", copy=False)
        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]
        tr = np.maximum.reduce([high - low, np.abs(high - prev_close), np.abs(low - prev_close)])
        atr = SuperTrendEMA200Strategy._rolling_mean_np(tr, 10)
        trend, st_line = SuperTrendEMA200Strategy._supertrend_np(high, low, close, atr, 3.5)

        self.assertEqual(bool(out["supertrend_bull"].iloc[-1]), bool(trend[-1]))
        self.assertAlmostEqual(float(out["supertrend"].iloc[-1]), float(st_line[-1]), places=8)

    def test_ema200_plugin_outputs_column(self):
        df = self._sample_df()
        ind = EMA200Indicator()
        out = ind.compute(df, {"ema_period": 200})
        self.assertIn("ema200", out.columns)

    def test_strategy_bull_state_buys_when_flip_gate_disabled(self):
        df = self._sample_df()
        strategy = SuperTrendEMA200Strategy(
            {
                "entry_on_flip_only": False,
                "confirm_bars": 1,
                "rsi_min": 0.0,
                "rsi_max": 100.0,
                "vol_min_ratio": 0.0,
            }
        )
        signal = strategy.analyze(
            MarketContext(
                symbol="BTCUSDT",
                timeframe_primary="1h",
                df_primary=df,
                current_price=float(df["close"].iloc[-1]),
            )
        )
        self.assertEqual(signal.action, "BUY")
        self.assertGreater(signal.stop_loss_distance or 0.0, 0.0)

    def test_strategy_confirmed_stop_exits(self):
        df = self._sample_df()
        signal = SuperTrendEMA200Strategy().analyze(
            MarketContext(
                symbol="BTCUSDT",
                timeframe_primary="1h",
                df_primary=df,
                current_price=float(df["close"].iloc[-1]),
                has_position=True,
                sl_confirmed=True,
            )
        )
        self.assertEqual(signal.action, "SELL")
