"""Per-side D1 gating in xauby_actionzone.

`use_d1_regime_filter` gated both directions with one flag. The daily zone lags,
so gating shorts on it enters declines late, while gating longs on it is what
suppresses bull whipsaw — the two sides do not want the same treatment. These
tests pin the split and, most importantly, that an unset per-side key still
follows the shared flag.
"""
from __future__ import annotations

import unittest

from xauby.runtime.pair_registry import _needs_d1
from xauby.strategies.cdc_action_zone.strategy import CDCActionZoneStrategy


class TestDefaultsUnchanged(unittest.TestCase):
    def test_per_side_keys_default_to_none(self):
        cfg = CDCActionZoneStrategy.default_config()
        self.assertIsNone(cfg["use_d1_regime_filter_long"])
        self.assertIsNone(cfg["use_d1_regime_filter_short"])
        self.assertFalse(cfg["use_d1_regime_filter"])

    def test_schema_advertises_both_overrides(self):
        schema = CDCActionZoneStrategy({}, )._config_schema()
        self.assertIn("use_d1_regime_filter_long", schema)
        self.assertIn("use_d1_regime_filter_short", schema)


class TestNeedsD1(unittest.TestCase):
    """Whether the engine must load 1d candles at all."""

    def test_shared_flag_on(self):
        self.assertTrue(_needs_d1({"use_d1_regime_filter": True}))

    def test_all_off(self):
        self.assertFalse(_needs_d1({"use_d1_regime_filter": False}))
        self.assertFalse(_needs_d1({}))

    def test_long_only_override_still_loads_d1(self):
        # The regression this guards: gating only longs must still fetch the
        # regime frame, or the gated side reads UNKNOWN and blocks everything.
        self.assertTrue(_needs_d1({"use_d1_regime_filter_long": True}))

    def test_short_only_override_still_loads_d1(self):
        self.assertTrue(_needs_d1({"use_d1_regime_filter_short": True}))

    def test_asymmetric_long_gated_short_free(self):
        cfg = {
            "use_d1_regime_filter": True,
            "use_d1_regime_filter_short": False,
        }
        self.assertTrue(_needs_d1(cfg))

    def test_shared_off_but_side_opted_in(self):
        cfg = {
            "use_d1_regime_filter": False,
            "use_d1_regime_filter_short": True,
        }
        self.assertTrue(_needs_d1(cfg))

    def test_none_values_are_not_truthy(self):
        cfg = {
            "use_d1_regime_filter": False,
            "use_d1_regime_filter_long": None,
            "use_d1_regime_filter_short": None,
        }
        self.assertFalse(_needs_d1(cfg))

    def test_string_booleans_accepted(self):
        self.assertTrue(_needs_d1({"use_d1_regime_filter_long": "true"}))
        self.assertFalse(_needs_d1({"use_d1_regime_filter_long": "false"}))


class TestPerSideResolution(unittest.TestCase):
    """Resolve the effective per-side gates the way analyze() does."""

    @staticmethod
    def _resolve(cfg):
        # Mirrors cfg_optional_bool: None means "not set, follow the shared flag".
        merged = {**CDCActionZoneStrategy.default_config(), **cfg}
        shared = bool(merged.get("use_d1_regime_filter", False))

        def pick(key):
            v = merged.get(key)
            if v is None:
                return shared
            return v if isinstance(v, bool) else str(v).lower() in ("true", "1", "yes", "on")

        return pick("use_d1_regime_filter_long"), pick("use_d1_regime_filter_short")

    def test_unset_sides_follow_shared_flag_on(self):
        # The bug this pins: default_config supplies None for both per-side keys,
        # so a naive `cfg.get(key, shared)` returns None and coerces to False,
        # silently disabling D1 on BOTH sides of every existing config.
        self.assertEqual(self._resolve({"use_d1_regime_filter": True}), (True, True))

    def test_unset_sides_follow_shared_flag_off(self):
        self.assertEqual(self._resolve({"use_d1_regime_filter": False}), (False, False))

    def test_requested_variant_long_gated_short_free(self):
        cfg = {"use_d1_regime_filter": True, "use_d1_regime_filter_short": False}
        self.assertEqual(self._resolve(cfg), (True, False))

    def test_inverse_variant_short_gated_long_free(self):
        cfg = {"use_d1_regime_filter": True, "use_d1_regime_filter_long": False}
        self.assertEqual(self._resolve(cfg), (False, True))

    def test_side_opt_in_from_shared_off(self):
        cfg = {"use_d1_regime_filter": False, "use_d1_regime_filter_long": True}
        self.assertEqual(self._resolve(cfg), (True, False))


class TestChecklistRowFollowsTheActiveSide(unittest.TestCase):
    """An ungated side must not show a 'D1 Regime' blocker it does not have."""

    def _items(self, cfg, *, short_bias: bool):
        strat = CDCActionZoneStrategy({**CDCActionZoneStrategy.default_config(), **cfg})
        indicators = {
            "cdc_zone_4h": "RED" if short_bias else "GREEN",
            "cdc_zone_d1": "GREEN",
            "rsi_4h": 50.0,
            "volume_ratio_4h": 1.0,
            "green_streak": 1,
            "red_streak": 1,
        }

        class Ctx:
            df_primary = None
            df_regime = None
            config = {"enable_short": short_bias}
            extras: dict = {}
            has_position = False
            position_side = None

        shared = bool(cfg.get("use_d1_regime_filter", False))

        def eff(key):
            v = cfg.get(key)
            return shared if v is None else bool(v)

        return strat._build_checklist(
            ctx=Ctx(),
            indicators=indicators,
            rsi_min=0.0,
            rsi_max=100.0,
            vol_min_ratio=0.0,
            use_d1_regime=eff("use_d1_regime_filter_long") or eff("use_d1_regime_filter_short"),
            use_d1_long=eff("use_d1_regime_filter_long"),
            use_d1_short=eff("use_d1_regime_filter_short"),
        )

    def _labels(self, items):
        return {str(i.get("label")) for i in items}

    def test_long_gated_shows_d1_row_on_long_side(self):
        items = self._items({"use_d1_regime_filter": True}, short_bias=False)
        self.assertIn("D1 Regime", self._labels(items))

    def test_short_ungated_hides_d1_row_on_short_side(self):
        cfg = {"use_d1_regime_filter": True, "use_d1_regime_filter_short": False}
        items = self._items(cfg, short_bias=True)
        self.assertNotIn("D1 Regime", self._labels(items))

    def test_short_gated_shows_d1_row_on_short_side(self):
        items = self._items({"use_d1_regime_filter": True}, short_bias=True)
        self.assertIn("D1 Regime", self._labels(items))


if __name__ == "__main__":
    unittest.main()
