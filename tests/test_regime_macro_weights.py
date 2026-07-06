"""Tests for config-driven macro weights and asset-neutral defaults (item 3)."""

import json
import os
import tempfile
import unittest

from xauby.regime.classifier import (
    DEFAULT_MACRO_BIAS_THRESHOLD,
    DEFAULT_MACRO_WEIGHTS,
    classify_market,
    resolve_macro_bias_threshold,
    resolve_macro_weights,
)
from xauby.ui.state_view import default_symbol_from_whitelist


def _flat_prices(n=250, price=2000.0):
    return [
        {"close": price, "high": price + 10, "low": price - 10, "volume": 8.0}
        for _ in range(n)
    ]


class TestResolveMacroWeights(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(resolve_macro_weights({}), DEFAULT_MACRO_WEIGHTS)
        self.assertEqual(resolve_macro_weights(None), DEFAULT_MACRO_WEIGHTS)

    def test_override(self):
        cfg = {"macro_sentiment_guard": {"macro_weights": {"dxy": 1.0, "fred": 0.5}}}
        w = resolve_macro_weights(cfg)
        self.assertEqual(w["dxy"], 1.0)
        self.assertEqual(w["fred"], 0.5)
        self.assertEqual(w["news"], DEFAULT_MACRO_WEIGHTS["news"])

    def test_invalid_value_ignored(self):
        cfg = {"macro_sentiment_guard": {"macro_weights": {"dxy": "nope"}}}
        self.assertEqual(resolve_macro_weights(cfg)["dxy"], DEFAULT_MACRO_WEIGHTS["dxy"])


class TestMacroBiasRespondsToWeights(unittest.TestCase):
    """The sentiment guard emits asset-oriented scores (positive = bullish), so
    a strong USD shows up as a NEGATIVE dxy_score. With the default +1.0 weight
    that resolves to RISK-OFF; flipping the weight inverts the bias, which is
    what makes the classifier generic across asset classes."""

    def test_default_weights_usd_strength_is_risk_off(self):
        # Strong USD -> producer emits negative dxy_score -> bearish for gold.
        macro = {"dxy_score": -1.0, "fred_score": 0.0, "news_score": 0.0}
        regime = classify_market(_flat_prices(), macro_state=macro)
        self.assertEqual(regime.macro_bias, "RISK-OFF")

    def test_default_weights_usd_weakness_is_risk_on(self):
        # Weak USD -> producer emits positive dxy_score -> bullish for gold.
        macro = {"dxy_score": 1.0, "fred_score": 0.0, "news_score": 0.0}
        regime = classify_market(_flat_prices(), macro_state=macro)
        self.assertEqual(regime.macro_bias, "RISK-ON")

    def test_flipped_dxy_weight_inverts_bias(self):
        # An asset positively correlated to the dollar flips the dxy sign, so the
        # same strong-USD input becomes bullish (RISK-ON) for that asset.
        macro = {"dxy_score": -1.0, "fred_score": 0.0, "news_score": 0.0}
        regime = classify_market(
            _flat_prices(),
            macro_state=macro,
            macro_weights={"dxy": -1.0},
        )
        self.assertEqual(regime.macro_bias, "RISK-ON")

    def test_reasons_checklist_agrees_with_macro_bias(self):
        # Guard against re-introducing a sign inversion on either side: the
        # user-facing DXY reason and macro_bias must point the same direction.
        weak_usd = classify_market(
            _flat_prices(),
            macro_state={"dxy_score": 1.0, "fred_score": 0.0, "news_score": 0.0},
        )
        weak_labels = {r["label"]: r["supportive"] for r in weak_usd.reasons}
        self.assertTrue(weak_labels.get("DXY Weak"))  # supportive for gold
        self.assertEqual(weak_usd.macro_bias, "RISK-ON")

        strong_usd = classify_market(
            _flat_prices(),
            macro_state={"dxy_score": -1.0, "fred_score": 0.0, "news_score": 0.0},
        )
        strong_labels = {r["label"]: r["supportive"] for r in strong_usd.reasons}
        self.assertIn("DXY Strong", strong_labels)
        self.assertFalse(strong_labels["DXY Strong"])  # not supportive
        self.assertEqual(strong_usd.macro_bias, "RISK-OFF")


class TestResolveMacroBiasThreshold(unittest.TestCase):
    def test_default(self):
        self.assertEqual(resolve_macro_bias_threshold({}), DEFAULT_MACRO_BIAS_THRESHOLD)
        self.assertEqual(resolve_macro_bias_threshold(None), DEFAULT_MACRO_BIAS_THRESHOLD)

    def test_override(self):
        cfg = {"macro_sentiment_guard": {"regime_bias_threshold": 0.6}}
        self.assertEqual(resolve_macro_bias_threshold(cfg), 0.6)

    def test_invalid_or_nonpositive_falls_back(self):
        for bad in ("nope", 0, -0.2, None):
            cfg = {"macro_sentiment_guard": {"regime_bias_threshold": bad}}
            self.assertEqual(
                resolve_macro_bias_threshold(cfg), DEFAULT_MACRO_BIAS_THRESHOLD
            )


class TestMacroInputsAreNormalised(unittest.TestCase):
    """Inputs are clamped to [-1, 1] before combining so a single unbounded
    input (news_score comes straight from an LLM and is NOT clamped upstream)
    cannot dominate the macro bias by magnitude."""

    def test_unbounded_news_is_clamped(self):
        # A misbehaving model returns 9.0; clamped to 1.0 it is RISK-ON but does
        # not blow up macro_conviction beyond the single-input contribution.
        regime = classify_market(
            _flat_prices(),
            macro_state={"dxy_score": 0.0, "fred_score": 0.0, "news_score": 9.0},
        )
        self.assertEqual(regime.macro_bias, "RISK-ON")
        # Same clamped magnitude as a well-behaved news_score of 1.0.
        ref = classify_market(
            _flat_prices(),
            macro_state={"dxy_score": 0.0, "fred_score": 0.0, "news_score": 1.0},
        )
        self.assertEqual(regime.features["macro_conviction"], ref.features["macro_conviction"])

    def test_clamped_news_cannot_flip_two_opposing_inputs(self):
        # dxy + fred = -2 (clamped) should stay RISK-OFF even against a runaway
        # bullish news score, because news is capped at +1.
        regime = classify_market(
            _flat_prices(),
            macro_state={"dxy_score": -1.0, "fred_score": -1.0, "news_score": 50.0},
        )
        self.assertEqual(regime.macro_bias, "RISK-OFF")


class TestMacroBiasThresholdControlsCutoff(unittest.TestCase):
    def test_higher_threshold_makes_weak_signal_neutral(self):
        macro = {"dxy_score": 0.4, "fred_score": 0.0, "news_score": 0.0}
        # Default 0.3 -> 0.4 clears it -> RISK-ON.
        self.assertEqual(classify_market(_flat_prices(), macro_state=macro).macro_bias, "RISK-ON")
        # Raise the cutoff above 0.4 -> same input is NEUTRAL.
        self.assertEqual(
            classify_market(_flat_prices(), macro_state=macro, macro_bias_threshold=0.5).macro_bias,
            "NEUTRAL",
        )

    def test_nonpositive_threshold_falls_back_to_default(self):
        macro = {"dxy_score": 0.4, "fred_score": 0.0, "news_score": 0.0}
        self.assertEqual(
            classify_market(_flat_prices(), macro_state=macro, macro_bias_threshold=0.0).macro_bias,
            "RISK-ON",
        )


class TestDefaultSymbolResolver(unittest.TestCase):
    def _write_whitelist(self, root, data):
        with open(os.path.join(root, "coin_whitelist.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_prefers_enabled_pair(self):
        with tempfile.TemporaryDirectory() as root:
            self._write_whitelist(
                root,
                {
                    "quote_asset": "USDT",
                    "assets": [
                        {"symbol": "BTC", "enabled": False},
                        {"symbol": "ETH", "enabled": True},
                    ],
                },
            )
            self.assertEqual(default_symbol_from_whitelist(root), "ETHUSDT")

    def test_falls_back_to_any_pair_when_none_enabled(self):
        with tempfile.TemporaryDirectory() as root:
            self._write_whitelist(
                root,
                {"quote_asset": "USDT", "assets": [{"symbol": "SOL", "enabled": False}]},
            )
            self.assertEqual(default_symbol_from_whitelist(root), "SOLUSDT")

    def test_neutral_default_when_no_whitelist(self):
        with tempfile.TemporaryDirectory() as root:
            # No whitelist file -> env-overridable neutral default (not hardcoded gold).
            old = os.environ.get("XAUBY_DEFAULT_SYMBOL")
            os.environ["XAUBY_DEFAULT_SYMBOL"] = "BTCUSDT"
            try:
                import importlib
                import xauby.ui.state_view as sv

                importlib.reload(sv)
                self.assertEqual(sv.default_symbol_from_whitelist(root), "BTCUSDT")
            finally:
                if old is None:
                    os.environ.pop("XAUBY_DEFAULT_SYMBOL", None)
                else:
                    os.environ["XAUBY_DEFAULT_SYMBOL"] = old
                import importlib
                import xauby.ui.state_view as sv

                importlib.reload(sv)


if __name__ == "__main__":
    unittest.main()
