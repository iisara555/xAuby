"""ta_fallback: sanity checks plus parity with pandas_ta where installed.

The parity class is the real contract: on any machine with pandas_ta (VPS,
CI with the git dependency), the fallback must match it to float precision.
Environments without pandas_ta still run the sanity checks and the CDC
integration test, which exercises the fallback through compute_indicators.
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from xauby.utils import ta_fallback

try:
    import pandas_ta  # noqa: F401
    HAS_PANDAS_TA = True
except ImportError:
    HAS_PANDAS_TA = False


def _walk(n: int = 300, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 2000.0 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.004, n)))
    return pd.DataFrame({
        "open": np.roll(close, 1),
        "high": high,
        "low": low,
        "close": close,
        "volume": rng.uniform(10, 100, n),
        "timestamp": np.arange(n) * 14400,
    })


class TestSanity(unittest.TestCase):
    def test_ema_constant_series_is_constant(self):
        s = pd.Series([50.0] * 60)
        out = ta_fallback.ema(s, length=12)
        self.assertAlmostEqual(float(out.iloc[-1]), 50.0)
        self.assertTrue(out.iloc[: 11].isna().all())

    def test_ema_short_series_is_all_nan(self):
        out = ta_fallback.ema(pd.Series([1.0, 2.0, 3.0]), length=12)
        self.assertTrue(out.isna().all())

    def test_rsi_bounds_and_direction(self):
        up = pd.Series(np.linspace(100, 200, 60))
        rsi_up = ta_fallback.rsi(up, length=14)
        self.assertGreater(float(rsi_up.iloc[-1]), 99.0)
        down = pd.Series(np.linspace(200, 100, 60))
        rsi_down = ta_fallback.rsi(down, length=14)
        self.assertLess(float(rsi_down.iloc[-1]), 1.0)

    def test_atr_positive_on_volatile_series(self):
        df = _walk()
        atr = ta_fallback.atr(df["high"], df["low"], df["close"], length=14)
        self.assertGreater(float(atr.iloc[-1]), 0.0)


@unittest.skipUnless(HAS_PANDAS_TA, "pandas_ta not installed; parity runs where it is")
class TestParityWithPandasTa(unittest.TestCase):
    def test_ema_matches(self):
        import pandas_ta as ta
        close = _walk()["close"]
        for length in (2, 12, 26):
            expected = ta.ema(close, length=length)
            actual = ta_fallback.ema(close, length=length)
            pd.testing.assert_series_equal(
                actual.tail(250), expected.tail(250), check_names=False, rtol=1e-9
            )

    def test_rsi_matches(self):
        import pandas_ta as ta
        close = _walk()["close"]
        expected = ta.rsi(close, length=14)
        actual = ta_fallback.rsi(close, length=14)
        pd.testing.assert_series_equal(
            actual.tail(250), expected.tail(250), check_names=False, rtol=1e-9
        )

    def test_atr_matches(self):
        import pandas_ta as ta
        df = _walk()
        expected = ta.atr(df["high"], df["low"], df["close"], length=14)
        actual = ta_fallback.atr(df["high"], df["low"], df["close"], length=14)
        pd.testing.assert_series_equal(
            actual.tail(250), expected.tail(250), check_names=False, rtol=1e-9
        )


class TestCdcIntegration(unittest.TestCase):
    def test_compute_indicators_produces_zones_and_slope(self):
        from xauby.strategies.cdc_action_zone.indicators import compute_indicators

        df = _walk()
        res = compute_indicators(df, None, ap_smoothing=2, slope_bars=3)
        self.assertIn(res["cdc_zone_4h"], ("GREEN", "RED", "BLUE", "LBLUE", "YELLOW", "ORANGE"))
        self.assertGreater(res["ema_fast_4h"], 0.0)
        self.assertGreater(res["ema_slow_4h"], 0.0)
        self.assertGreater(res["atr_4h"], 0.0)
        self.assertIsNotNone(res["ema_slow_4h_slope"])


if __name__ == "__main__":
    unittest.main()
