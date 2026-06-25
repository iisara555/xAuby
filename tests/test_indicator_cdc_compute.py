import unittest

import pandas as pd

from xauby.strategies.indicators.registry import load_indicator


class TestIndicatorCDCCompute(unittest.TestCase):
    def _sample_df(self, n: int = 120) -> pd.DataFrame:
        rows = []
        price = 2000.0
        for i in range(n):
            price += 0.5 if i % 3 else -0.2
            rows.append(
                {
                    "timestamp": 1_700_000_000 + i * 14_400,
                    "open": price - 1,
                    "high": price + 2,
                    "low": price - 2,
                    "close": price,
                    "volume": 10.0 + i,
                }
            )
        return pd.DataFrame(rows)

    def test_compute_adds_expected_columns(self):
        df = self._sample_df()
        ind = load_indicator("cdc_action_zone")
        out = ind.compute(df, {"ap_smoothing": 1})
        for col in ("ap", "ema12", "ema26", "zone"):
            self.assertIn(col, out.columns)
        self.assertTrue(str(out["zone"].iloc[-1]))
