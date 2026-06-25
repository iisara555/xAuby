"""Direct entry/exit behavior tests for BTC EMA pullback."""
import unittest

import numpy as np
import pandas as pd

from xauby.strategies.btc_ema_pullback.strategy import BTCEMAPullbackStrategy
from xauby.strategies.context import MarketContext


class TestBTCEMAPullbackStrategy(unittest.TestCase):
    @staticmethod
    def _df() -> pd.DataFrame:
        close = 100.0 + np.arange(120, dtype=float) * 0.2
        close[-1] += 3.0
        return pd.DataFrame(
            {
                "open": close - 0.2,
                "high": close + 0.3,
                "low": close - 0.4,
                "close": close,
                "volume": np.full(len(close), 1000.0),
            }
        )

    def _signal(self, *, has_position=False, sl_confirmed=False):
        df = self._df()
        strategy = BTCEMAPullbackStrategy(
            {
                "ema_fast": 5,
                "ema_slow": 10,
                "ema_trend": 20,
                "pullback_lookback": 5,
                "pullback_atr_mult": 10.0,
                "breakout_lookback": 3,
                "min_body_atr": 0.0,
                "rsi_min": 0.0,
                "rsi_max": 100.0,
                "vol_min_ratio": 0.0,
            }
        )
        return strategy.analyze(
            MarketContext(
                symbol="BTCUSDT",
                timeframe_primary="1h",
                df_primary=df,
                current_price=float(df["close"].iloc[-1]),
                has_position=has_position,
                sl_confirmed=sl_confirmed,
            )
        )

    def test_pullback_reclaim_buys(self):
        signal = self._signal()
        self.assertEqual(signal.action, "BUY")
        self.assertGreater(signal.stop_loss_distance or 0.0, 0.0)
        self.assertTrue(all(item["ok"] for item in signal.checklist))

    def test_confirmed_stop_exits(self):
        signal = self._signal(has_position=True, sl_confirmed=True)
        self.assertEqual(signal.action, "SELL")
        self.assertIn("SL confirmed", signal.reason)
