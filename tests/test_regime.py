import unittest
from xauby.regime.classifier import classify_market

class TestGoldRegimeClassifier(unittest.TestCase):
    def test_empty_prices(self):
        regime = classify_market([])
        self.assertEqual(regime.regime, "UNKNOWN")
        self.assertEqual(regime.trend, "NEUTRAL")

    def test_bullish_market(self):
        # Bullish prices going up
        prices = [{"close": float(1000 + i * 5), "high": float(1005 + i * 5), "low": float(995 + i * 5), "volume": 10.0} for i in range(250)]
        
        # EMA relationships simulated
        indicators = {
            "ema_12_4h": 2200.0,
            "ema_26_4h": 2100.0,
            "ema_200_4h": 1500.0,
            "atr_4h": 10.0,
            "volume_ratio_4h": 1.2,
        }
        
        regime = classify_market(prices, indicators=indicators)
        self.assertEqual(regime.trend, "BULLISH")
        self.assertIn(regime.regime, {"BULL_TREND_WEAK", "BULL_TREND_STRONG", "BULL_BREAKOUT"})
        self.assertGreater(regime.gold_score, 0)
        self.assertIn(regime.phase, {"MARKUP", "RECOVERY_RALLY"})
        self.assertIn(regime.trend_strength, {"WEAK_UP", "STRONG_UP"})
        self.assertIn(regime.strategy_bias["family"], {"trend_following", "chop_filter"})
        self.assertIn("ema200_distance_pct", regime.features)
        self.assertEqual(len(regime.reasons), 4)
        self.assertTrue(any(r["label"] == "Trend Bullish" and r["supportive"] for r in regime.reasons))

    def test_user_reasons_with_macro(self):
        prices = [{"close": 2000.0, "high": 2010.0, "low": 1990.0, "volume": 8.0} for _ in range(250)]
        indicators = {"volume_ratio_4h": 0.6}
        macro = {"dxy_score": 0.52, "fred_score": 0.0, "fred_rate": 3.64, "news_score": 0.0}
        regime = classify_market(prices, indicators=indicators, macro_state=macro)
        labels = {r["label"]: r["supportive"] for r in regime.reasons}
        self.assertTrue(labels.get("DXY Weak"))
        self.assertTrue(labels.get("Inflation Supportive"))
        self.assertFalse(labels.get("Trend Neutral"))
        self.assertFalse(labels.get("Volume Weak"))

    def test_panic_sell_regime(self):
        prices = [
            {
                "close": float(2000 - i * 8),
                "high": float(2025 - i * 8),
                "low": float(1940 - i * 9),
                "volume": 10.0 + i,
            }
            for i in range(250)
        ]
        indicators = {
            "ema_12_4h": 1200.0,
            "ema_26_4h": 1300.0,
            "ema_200_4h": 1700.0,
            "atr_4h": 80.0,
            "volume_ratio_4h": 2.1,
        }
        macro = {"dxy_score": 0.5, "fred_score": 0.2, "news_score": -0.2}

        regime = classify_market(prices, indicators=indicators, macro_state=macro)

        self.assertEqual(regime.regime, "PANIC_SELL")
        self.assertEqual(regime.phase, "CAPITULATION")
        self.assertEqual(regime.risk_state, "PANIC_RISK")
        self.assertEqual(regime.transition_risk, "EXTREME")
        self.assertEqual(regime.strategy_bias["family"], "defensive")
        self.assertNotIn("BUY", regime.strategy_bias["allowed_actions"])

    def test_low_vol_accumulation_regime(self):
        prices = [
            {
                "close": 1000.0 + ((i % 5) - 2) * 0.5,
                "high": 1002.0,
                "low": 998.0,
                "volume": 7.0,
            }
            for i in range(250)
        ]
        indicators = {
            "ema_12_4h": 1000.2,
            "ema_26_4h": 1000.1,
            "ema_200_4h": 1000.0,
            "atr_4h": 1.0,
            "volume_ratio_4h": 0.5,
        }

        regime = classify_market(prices, indicators=indicators)

        self.assertIn(regime.regime, {"LOW_VOL_ACCUMULATION", "LOW_VOL_RANGE", "SIDEWAYS_CHOP"})
        self.assertIn(regime.phase, {"ACCUMULATION", "RANGE"})
        self.assertIn(regime.risk_state, {"QUIET_RISK", "CHOP_RISK"})
        self.assertIn(regime.strategy_bias["family"], {"range_or_accumulation", "chop_filter"})

    def test_institutional_features_expose_quality_and_percentiles(self):
        prices = [
            {
                "close": float(1000 + i * 4),
                "high": float(1010 + i * 4),
                "low": float(990 + i * 4),
                "volume": 10.0 + (i % 10),
            }
            for i in range(260)
        ]
        indicators = {
            "ema_12_4h": 2025.0,
            "ema_26_4h": 1970.0,
            "ema_200_4h": 1520.0,
            "atr_4h": 28.0,
            "volume_ratio_4h": 1.6,
        }
        macro = {"dxy_score": 0.6, "fred_score": 0.2, "news_score": 0.1}

        regime = classify_market(prices, indicators=indicators, macro_state=macro)

        self.assertEqual(regime.trend, "BULLISH")
        self.assertGreater(regime.features["trend_quality"], 0.65)
        self.assertGreaterEqual(regime.features["atr_percentile"], 0.0)
        self.assertLessEqual(regime.features["atr_percentile"], 1.0)
        self.assertGreater(regime.features["liquidity_confirmation"], 0.7)
        self.assertIn("institutional_confidence", regime.features)

    def test_macro_conflict_and_chop_reduce_confidence(self):
        trend_prices = [
            {"close": float(1000 + i * 4), "high": float(1010 + i * 4), "low": float(990 + i * 4), "volume": 12.0}
            for i in range(260)
        ]
        chop_prices = [
            {
                "close": 1200.0 + (8.0 if i % 2 else -8.0),
                "high": 1214.0,
                "low": 1186.0,
                "volume": 5.0,
            }
            for i in range(260)
        ]
        aligned = classify_market(
            trend_prices,
            indicators={
                "ema_12_4h": 2025.0,
                "ema_26_4h": 1970.0,
                "ema_200_4h": 1520.0,
                "atr_4h": 28.0,
                "volume_ratio_4h": 1.5,
            },
            macro_state={"dxy_score": 0.7, "fred_score": 0.2, "news_score": 0.0},
        )
        conflicted_chop = classify_market(
            chop_prices,
            indicators={
                "ema_12_4h": 1201.0,
                "ema_26_4h": 1200.0,
                "ema_200_4h": 1199.5,
                "atr_4h": 3.0,
                "volume_ratio_4h": 0.5,
            },
            macro_state={"dxy_score": -0.7, "fred_score": -0.2, "news_score": 0.0},
        )

        self.assertGreater(aligned.confidence, conflicted_chop.confidence)
        self.assertLess(conflicted_chop.features["trend_quality"], 0.4)
        self.assertIn(conflicted_chop.regime, {"LOW_VOL_ACCUMULATION", "LOW_VOL_RANGE", "SIDEWAYS_CHOP"})

if __name__ == "__main__":
    unittest.main()
