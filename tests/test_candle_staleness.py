"""Candle-staleness guard (#4)."""
from __future__ import annotations

import unittest

from xauby.runtime.candle_utils import candle_is_stale

TF = "4h"
STEP = 4 * 3600
NOW = 1_700_000_000


class TestCandleIsStale(unittest.TestCase):
    def test_fresh_candle_not_stale(self):
        # Last candle opened ~1 timeframe ago — normal.
        self.assertFalse(candle_is_stale(NOW - STEP, TF, now=NOW))

    def test_at_two_timeframes_not_stale(self):
        # ~2x is the normal peak between closed candles (default factor 2.5).
        self.assertFalse(candle_is_stale(NOW - 2 * STEP, TF, now=NOW))

    def test_missed_candle_is_stale(self):
        # 3x the timeframe -> a candle was missed.
        self.assertTrue(candle_is_stale(NOW - 3 * STEP, TF, now=NOW))

    def test_custom_factor(self):
        # Tighter factor flags sooner.
        self.assertTrue(candle_is_stale(NOW - int(1.6 * STEP), TF, now=NOW, factor=1.5))

    def test_no_candle_is_not_stale(self):
        self.assertFalse(candle_is_stale(0, TF, now=NOW))

    def test_unknown_timeframe_is_not_stale(self):
        self.assertFalse(candle_is_stale(NOW - 10 * STEP, "bogus", now=NOW))


if __name__ == "__main__":
    unittest.main()
