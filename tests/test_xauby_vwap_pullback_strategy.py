"""Tests for xAuby EMA/VWAP pullback strategy and indicator."""
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from xauby.runtime.pair_registry import PairRegistry
from xauby.runtime.trading_config import strategy_name_for_symbol
from xauby.strategies.context import MarketContext
from xauby.strategies.indicators.registry import indicators_for_strategy, load_indicator
from xauby.strategies.registry import available_strategies
from xauby.strategies.xauby_vwap_pullback.strategy import XAubyVWAPPullbackStrategy, annotate


def _entry_frame(n: int = 240) -> pd.DataFrame:
    close = 100.0 + np.arange(n, dtype=float) * 0.12
    close[-5:-2] -= np.array([0.35, 0.55, 0.4])
    close[-2] = close[-3] + 0.08
    close[-1] = close[-2] + 0.75
    open_ = close - 0.08
    open_[-1] = close[-1] - 0.45
    high = close + 0.18
    low = close - 0.24
    volume = np.full(n, 1000.0)
    volume[-1] = 1350.0
    start = datetime(2026, 6, 24, 0, 0, tzinfo=timezone.utc)
    timestamp = [int((start + timedelta(minutes=15 * i)).timestamp()) for i in range(n)]
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "timestamp": timestamp,
        }
    )


class TestXAubyVWAPPullbackStrategy(unittest.TestCase):
    def _strategy(self) -> XAubyVWAPPullbackStrategy:
        return XAubyVWAPPullbackStrategy(
            {
                "ema_fast": 10,
                "ema_slow": 20,
                "ema_trend": 50,
                "pullback_atr_mult": 2.0,
                "pullback_lookback": 8,
                "reclaim_lookback": 2,
                "volume_min_ratio": 1.05,
                "min_body_atr": 0.0,
            }
        )

    def test_registered_and_buys_confirmed_vwap_pullback(self):
        self.assertIn("xauby_vwap_pullback", available_strategies())
        df = _entry_frame()
        signal = self._strategy().analyze(
            MarketContext(
                symbol="BTCTHB",
                timeframe_primary="15m",
                df_primary=df,
                current_price=float(df["close"].iloc[-1]),
            )
        )
        self.assertEqual(signal.action, "BUY")
        self.assertGreater(signal.stop_loss_distance or 0.0, 0.0)
        self.assertTrue(all(item["ok"] for item in signal.checklist))
        self.assertEqual(signal.metadata["risk_reward"], 2.0)

    def test_sl_confirmed_exits_position(self):
        df = _entry_frame()
        signal = self._strategy().analyze(
            MarketContext(
                symbol="BTCTHB",
                timeframe_primary="15m",
                df_primary=df,
                current_price=float(df["close"].iloc[-1]),
                has_position=True,
                sl_confirmed=True,
            )
        )
        self.assertEqual(signal.action, "SELL")
        self.assertIn("SL confirmed", signal.reason)

    def test_indicator_appends_vwap_pullback_columns(self):
        ind = load_indicator("xauby_vwap_pullback", {})
        out = ind.compute(_entry_frame(), self._strategy().config)
        for col in ("ema_fast", "ema_slow", "ema_trend", "vwap", "vwap_pullback_zone"):
            self.assertIn(col, out.columns)
        self.assertEqual(annotate(_entry_frame(), XAubyVWAPPullbackStrategy.default_config()).shape[0], 240)
        self.assertEqual([item.name for item in indicators_for_strategy("xauby_vwap_pullback")], ["xauby_vwap_pullback"])

    def test_short_replay_slices_do_not_raise_on_nullable_vwap(self):
        cfg = {**XAubyVWAPPullbackStrategy.default_config(), "ema_trend": 5}
        out = annotate(_entry_frame(30), cfg)
        self.assertEqual(len(out["vwap_ok"]), 30)
        self.assertFalse(out["vwap_ok"].isna().any())

    def test_full_pair_symbol_is_not_quote_appended(self):
        cfg = {
            "architecture": {"whitelist_strict": True},
            "exchange": {"quote_asset": "USDT"},
            "data": {"hybrid_dynamic_coin_config": {"require_supported_market": False}},
            "strategy": {"active": "cdc_action_zone"},
        }
        import json
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "coin_whitelist.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "quote_asset": "USDT",
                        "assets": [
                            {
                                "symbol": "BTCTHB",
                                "enabled": True,
                                "strategy": "xauby_vwap_pullback",
                                "primary_timeframe": "15m",
                                "confirm_timeframe": "",
                                "mode": "sim",
                            }
                        ],
                    },
                    f,
                )

            self.assertEqual(
                strategy_name_for_symbol(cfg, "BTCTHB", project_root=tmp, whitelist_path=path),
                "xauby_vwap_pullback",
            )
            reg = PairRegistry(cfg, project_root=tmp, whitelist_path=path)
            active = reg.load(client=None)

        self.assertEqual([spec.symbol for spec in active], ["BTCTHB"])
        self.assertEqual(active[0].primary_timeframe, "15m")


if __name__ == "__main__":
    unittest.main()
