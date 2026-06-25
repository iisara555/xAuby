"""P1-1/P1-2 acceptance tests: closed-candle trimming and risk_pct validation."""
import time
import unittest

import numpy as np
import pandas as pd

from xauby.runtime.candle_utils import drop_forming_bar, timeframe_seconds, use_closed_candles
from xauby.runtime.trading_config import validate_risk_config
from xauby.strategies.cdc_action_zone.indicators import compute_indicators


def _make_df(bars: int, tf_seconds: int = 14400, end_open_ts: float = None) -> pd.DataFrame:
    """Synthetic OHLCV frame whose last bar opens at end_open_ts."""
    if end_open_ts is None:
        end_open_ts = time.time() - (time.time() % tf_seconds)
    ts = np.array([end_open_ts - (bars - 1 - i) * tf_seconds for i in range(bars)])
    rng = np.random.default_rng(42)
    close = 2000 + np.cumsum(rng.normal(0, 5, bars))
    df = pd.DataFrame(
        {
            "timestamp": ts,
            "open": close - 1,
            "high": close + 3,
            "low": close - 3,
            "close": close,
            "volume": rng.uniform(100, 200, bars),
        }
    )
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="s")
    return df.set_index("datetime")


class TestDropFormingBar(unittest.TestCase):
    def test_timeframe_seconds(self):
        self.assertEqual(timeframe_seconds("4h"), 14400)
        self.assertEqual(timeframe_seconds("1d"), 86400)
        self.assertEqual(timeframe_seconds("15m"), 900)
        self.assertEqual(timeframe_seconds("bogus", default=123), 123)

    def test_drops_forming_last_bar(self):
        df = _make_df(120)  # last bar opened within the current 4h window
        trimmed, dropped = drop_forming_bar(df, "4h")
        self.assertTrue(dropped)
        self.assertEqual(len(trimmed), len(df) - 1)

    def test_keeps_closed_last_bar(self):
        # Last bar opened two full timeframes ago -> already closed.
        end_open = time.time() - 2 * 14400
        df = _make_df(120, end_open_ts=end_open)
        trimmed, dropped = drop_forming_bar(df, "4h")
        self.assertFalse(dropped)
        self.assertEqual(len(trimmed), len(df))

    def test_empty_df(self):
        df = pd.DataFrame()
        trimmed, dropped = drop_forming_bar(df, "4h")
        self.assertFalse(dropped)
        self.assertEqual(len(trimmed), 0)

    def test_use_closed_candles_default_true(self):
        self.assertTrue(use_closed_candles({}))
        self.assertTrue(use_closed_candles({"strategy": {}}))
        self.assertFalse(use_closed_candles({"strategy": {"use_closed_candles": False}}))


class TestIndicatorsClosedCandles(unittest.TestCase):
    def test_trimmed_df_matches_forming_df(self):
        """Indicators from a trimmed df (flag False) must equal indicators the
        legacy path produced from the same df with the forming bar attached
        only for keys that previously already excluded the forming bar."""
        df_full = _make_df(150)
        df_trimmed = df_full.iloc[:-1]

        legacy = compute_indicators(df_full, None, 2, last_bar_is_forming=True)
        closed = compute_indicators(df_trimmed, None, 2, last_bar_is_forming=False)

        # volume ratio and sparkline excluded the forming bar before, so they
        # must be identical — proving the engine trim does not double-drop.
        self.assertEqual(legacy["volume_ratio_4h"], closed["volume_ratio_4h"])
        self.assertEqual(legacy["recent_closes_4h"], closed["recent_closes_4h"])

    def test_closed_only_indicators_use_last_closed_bar(self):
        df_full = _make_df(150)
        df_trimmed = df_full.iloc[:-1]
        closed = compute_indicators(df_trimmed, None, 2, last_bar_is_forming=False)
        self.assertAlmostEqual(
            closed["close_4h"], float(df_trimmed["close"].iloc[-1]), places=9
        )


class TestRiskPctValidation(unittest.TestCase):
    def test_rejects_percent_style_value(self):
        cfg = {"portfolio": {"position_sizing": {"risk_pct": 0.45}}}
        with self.assertRaises(ValueError):
            validate_risk_config(cfg)

    def test_rejects_legacy_percent_block(self):
        cfg = {"risk": {"max_risk_per_trade_pct": 45.0}}
        with self.assertRaises(ValueError):
            validate_risk_config(cfg)

    def test_accepts_fraction(self):
        cfg = {
            "trading": {"risk_pct": 0.02},
            "portfolio": {
                "position_sizing": {"risk_pct": 0.02},
                "symbols": {
                    "XAUTUSDT": {"position_sizing": {"risk_pct": 0.02}},
                    "BTCUSDT": {"position_sizing": {"risk_pct": 0.02}},
                },
            },
            "risk": {"max_risk_per_trade_pct": 2.0},
            "strategies": {"cdc_action_zone": {"risk_pct": 0.02}},
        }
        validate_risk_config(cfg)  # must not raise


if __name__ == "__main__":
    unittest.main()
