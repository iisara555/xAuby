import unittest
from pathlib import Path

import pandas as pd
import yaml

from scripts.evaluate_okx_xau_migration import evaluate
from scripts.fetch_okx_xau_history import candles_to_cache_df, normalize_symbol, okx_bar
from xauby.runtime.pair_registry import PairRegistry


ROOT = Path(__file__).resolve().parent.parent


class TestOkxXauMigrationTools(unittest.TestCase):
    def test_okx_timeframe_and_symbol_helpers(self):
        self.assertEqual(okx_bar("4h"), "4H")
        self.assertEqual(okx_bar("1d"), "1D")
        self.assertEqual(normalize_symbol("XAU-USDT-SWAP"), "XAUUSDT")

    def test_candles_to_cache_df_drops_unconfirmed_bar(self):
        rows = [
            ["1000", "10", "11", "9", "10.5", "100", "0.01", "10.5", "1"],
            ["2000", "10.5", "12", "10", "11", "200", "0.02", "22", "0"],
        ]

        df = candles_to_cache_df(rows, "1m")

        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["open_time"], 1000)
        self.assertIn("quote_volume", df.columns)

    def test_evaluate_passes_when_short_adds_profit_edge(self):
        # Build enough 4h bars for CDC warm-up. The first tradable section flips
        # from green to red and then trends down, making long_short beat long_only.
        closes = []
        closes.extend([100 + i * 0.1 for i in range(120)])
        closes.extend([112 - i * 0.5 for i in range(80)])
        rows = []
        for i, close in enumerate(closes):
            rows.append(
                {
                    "open_time": i * 4 * 3600 * 1000,
                    "open": close,
                    "high": close + 1,
                    "low": close - 1,
                    "close": close,
                    "volume": 1.0,
                }
            )
        df = pd.DataFrame(rows)

        decision = evaluate(
            df,
            fee_pct=0.0,
            slippage_bps=0.0,
            funding_rate_8h=0.0,
            min_profit_edge_pp=1.0,
        )

        self.assertGreater(decision.modes["long_short"].net_pct, decision.modes["long_only"].net_pct)
        self.assertTrue(decision.passed)

    def test_okx_profile_loads_xau_as_paper_long_only(self):
        with open(ROOT / "configs" / "okx_xau_paper.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        registry = PairRegistry(cfg, project_root=str(ROOT))
        registry.load()
        spec = registry.get("XAUUSDT")

        self.assertIsNotNone(spec)
        self.assertEqual(spec.execution_mode, "sim")
        self.assertEqual(spec.allowed_sides, ("long",))
        self.assertEqual(spec.leverage, 1.0)
        self.assertFalse(spec.short_live_enabled)


if __name__ == "__main__":
    unittest.main()
