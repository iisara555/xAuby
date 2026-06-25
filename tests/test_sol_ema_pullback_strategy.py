"""Tests for SOL EMA20/50 pullback strategy and chart indicator."""
import unittest

import numpy as np
import pandas as pd

from xauby.strategies.context import MarketContext
from xauby.strategies.indicators.registry import load_indicator
from xauby.strategies.registry import available_strategies, load_strategy
from xauby.strategies.sol_ema_pullback.strategy import SOLEMAPullbackStrategy, annotate


def _pullback_frame(n: int = 120) -> pd.DataFrame:
    close = 40.0 + np.arange(n, dtype=float) * 0.15
    close[-6:-2] -= np.array([0.15, 0.35, 0.55, 0.4])
    close[-2] = close[-3] + 0.1
    close[-1] = close[-2] + 0.85
    open_ = close - 0.12
    open_[-1] = close[-1] - 0.55
    high = close + 0.18
    low = close - 0.22
    volume = np.full(n, 1000.0)
    volume[-1] = 1450.0
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})


class TestSOLEMAPullbackStrategy(unittest.TestCase):
    def _strategy(self) -> SOLEMAPullbackStrategy:
        return SOLEMAPullbackStrategy(
            {
                "pullback_atr_mult": 2.0,
                "pullback_lookback": 8,
                "min_body_atr": 0.0,
                "volume_min_ratio": 1.05,
                "confirm_lookback": 2,
                "swing_lookback": 8,
            }
        )

    def _signal(self, *, has_position: bool = False, sl_confirmed: bool = False):
        df = _pullback_frame()
        return self._strategy().analyze(
            MarketContext(
                symbol="SOLUSDT",
                timeframe_primary="15m",
                df_primary=df,
                current_price=float(df["close"].iloc[-1]),
                has_position=has_position,
                sl_confirmed=sl_confirmed,
            )
        )

    def test_registered_and_buys_confirmed_pullback(self):
        self.assertIn("sol_ema_pullback", available_strategies())
        signal = self._signal()
        self.assertEqual(signal.action, "BUY")
        self.assertGreater(signal.stop_loss_distance or 0.0, 0.0)
        self.assertGreater(signal.metadata.get("take_profit_price") or 0.0, signal.indicators["swing_low"])
        self.assertTrue(all(item["ok"] for item in signal.checklist))

    def test_sl_confirmed_exits_position(self):
        signal = self._signal(has_position=True, sl_confirmed=True)
        self.assertEqual(signal.action, "SELL")
        self.assertIn("SL confirmed", signal.reason)

    def test_indicator_appends_chart_columns(self):
        ind = load_indicator("sol_ema_pullback", {})
        out = ind.compute(_pullback_frame(), self._strategy().config)
        for col in ("ema20", "ema50", "swing_low", "ema_pullback_zone"):
            self.assertIn(col, out.columns)
        self.assertEqual(annotate(_pullback_frame(), SOLEMAPullbackStrategy.default_config()).shape[0], 120)

    def test_loader_rejects_rr_below_two_in_strict_mode(self):
        with self.assertRaises(Exception):
            load_strategy("sol_ema_pullback", {"risk_reward": 1.5}, strict=True)


if __name__ == "__main__":
    unittest.main()
