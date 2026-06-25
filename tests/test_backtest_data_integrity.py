"""Backtest data-integrity guards: forming-bar drop + gap warnings."""
from __future__ import annotations

import logging
import time
import unittest

import pandas as pd

from xauby.backtest.data import _finalize_candles, _warn_on_gaps

TF = "4h"
STEP = 4 * 3600


def _frame(n: int, *, last_open: int) -> pd.DataFrame:
    """Build a contiguous 4h frame whose last bar opens at ``last_open``."""
    rows = []
    for i in range(n):
        ts = last_open - (n - 1 - i) * STEP
        rows.append(
            {
                "open_time": ts * 1000,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 10.0,
                "timestamp": ts,
            }
        )
    return pd.DataFrame(rows)


class TestFormingBarDrop(unittest.TestCase):
    def test_drops_still_forming_last_bar(self):
        # Last bar opened 1h ago on a 4h timeframe -> still forming.
        now = int(time.time())
        last_open = now - 3600
        df = _frame(50, last_open=last_open)
        out = _finalize_candles(df, TF, "BTCUSDT")
        self.assertEqual(len(out), 49)
        self.assertLess(int(out["timestamp"].iloc[-1]), last_open)

    def test_keeps_closed_last_bar(self):
        # Last bar opened well over a timeframe ago -> already closed.
        last_open = int(time.time()) - 10 * STEP
        df = _frame(50, last_open=last_open)
        out = _finalize_candles(df, TF, "BTCUSDT")
        self.assertEqual(len(out), 50)


class TestGapWarning(unittest.TestCase):
    def test_warns_on_missing_candle(self):
        last_open = int(time.time()) - 10 * STEP
        df = _frame(20, last_open=last_open)
        # Punch a hole: shift one timestamp so two intervals are irregular.
        df.loc[10, "timestamp"] = int(df.loc[10, "timestamp"]) + STEP // 2
        with self.assertLogs("xauby.backtest.data", level="WARNING") as cm:
            _warn_on_gaps(df, TF, "BTCUSDT")
        self.assertTrue(any("DATA GAPS" in m for m in cm.output))

    def test_silent_on_contiguous(self):
        last_open = int(time.time()) - 10 * STEP
        df = _frame(20, last_open=last_open)
        logger = logging.getLogger("xauby.backtest.data")
        with self.assertRaises(AssertionError):
            with self.assertLogs(logger, level="WARNING"):
                _warn_on_gaps(df, TF, "BTCUSDT")


if __name__ == "__main__":
    unittest.main()
