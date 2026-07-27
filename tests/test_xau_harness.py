"""The shared XAU harness must not let a variant table rot or leak again.

Two failures are already in the record and both are cheap to test for:

* a variant spec that sets part of the `d1_gate` group inherits the rest from
  the base config, so six variants became two when `bot_config.yaml` gained
  `use_d1_regime_filter_long`;
* a document labelled one row "the deployed config" and kept saying it for a day
  after production had moved.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.xau_harness import (  # noqa: E402
    D1_KEYS,
    GATE_BASELINE,
    VARIANTS,
    bootstrap,
    d1_gate,
    deployed_variant_name,
    gated_sides,
    prepare,
)
from xauby.backtest.walkforward import VariantSpecError, resolve_variant  # noqa: E402
from xauby.observability.replay_validation import load_bot_config  # noqa: E402


class TestVariantCatalog(unittest.TestCase):
    def test_every_variant_states_the_whole_d1_group(self):
        for name, spec in VARIANTS.items():
            with self.subTest(variant=name):
                for key in D1_KEYS:
                    self.assertIn(key, spec,
                                  f"{name} leaves {key} to be inherited")

    def test_variants_survive_a_base_that_disagrees_on_every_d1_key(self):
        # The exact shape that broke the originals: a base config carrying a
        # per-side key the variant table never mentioned.
        base = {"enable_short": True, "use_d1_regime_filter": True,
                "use_d1_regime_filter_long": False,
                "use_d1_regime_filter_short": None}
        for name, spec in VARIANTS.items():
            with self.subTest(variant=name):
                resolved = resolve_variant(base, spec)
                for key in D1_KEYS:
                    self.assertEqual(resolved[key], spec[key])
                self.assertEqual(resolved["enable_short"], spec["enable_short"])

    def test_variants_are_pairwise_distinct(self):
        seen = {}
        for name, spec in VARIANTS.items():
            key = (bool(spec["enable_short"]), gated_sides(spec))
            self.assertNotIn(key, seen,
                             f"{name} is indistinguishable from {seen.get(key)}")
            seen[key] = name

    def test_a_partial_spec_is_still_rejected(self):
        with self.assertRaises(VariantSpecError):
            resolve_variant({"use_d1_regime_filter": False},
                            {"use_d1_regime_filter": True})


class TestGatedSides(unittest.TestCase):
    def test_none_follows_the_shared_flag(self):
        self.assertEqual(gated_sides({"use_d1_regime_filter": True}), (True, True))
        self.assertEqual(gated_sides({"use_d1_regime_filter": False}), (False, False))

    def test_explicit_side_overrides_the_shared_flag(self):
        spec = {"use_d1_regime_filter": True, "use_d1_regime_filter_long": False}
        self.assertEqual(gated_sides(spec), (False, True))

    def test_d1_gate_helper_writes_all_three_keys(self):
        self.assertEqual(d1_gate(True, False, True),
                         {"use_d1_regime_filter": True,
                          "use_d1_regime_filter_long": False,
                          "use_d1_regime_filter_short": True})


class TestGateBaselines(unittest.TestCase):
    def test_every_short_bearing_variant_has_a_baseline(self):
        short_bearing = {n for n, s in VARIANTS.items() if s["enable_short"]}
        self.assertEqual(set(GATE_BASELINE), short_bearing)

    def test_baselines_are_long_only_and_gate_longs_identically(self):
        # The acceptance edge must isolate the short side. A baseline that gates
        # longs differently smuggles a second change into the comparison.
        for candidate, baseline in GATE_BASELINE.items():
            with self.subTest(candidate=candidate):
                self.assertFalse(VARIANTS[baseline]["enable_short"])
                self.assertEqual(gated_sides(VARIANTS[candidate])[0],
                                 gated_sides(VARIANTS[baseline])[0])


class TestDeployedResolution(unittest.TestCase):
    def test_resolves_from_config_not_from_a_label(self):
        live = {"enable_short": True, "use_d1_regime_filter": True,
                "use_d1_regime_filter_long": False}
        self.assertEqual(deployed_variant_name(live), "L:D1off S:D1on")

    def test_long_only_shapes_resolve(self):
        self.assertEqual(
            deployed_variant_name({"enable_short": False,
                                   "use_d1_regime_filter": True}),
            "long-only D1 on")
        self.assertEqual(
            deployed_variant_name({"enable_short": False,
                                   "use_d1_regime_filter": False}),
            "long-only D1 off")

    def test_matching_is_behavioural_not_literal(self):
        # shared=True with both sides off gates nothing, so it IS "D1 off" —
        # matching on the keys as written would report a spurious mismatch.
        self.assertEqual(
            deployed_variant_name({"enable_short": True,
                                   "use_d1_regime_filter": True,
                                   "use_d1_regime_filter_long": False,
                                   "use_d1_regime_filter_short": False}),
            "long+short D1 off")

    def test_a_long_only_config_ignores_its_meaningless_short_gate(self):
        self.assertEqual(
            deployed_variant_name({"enable_short": False,
                                   "use_d1_regime_filter": True,
                                   "use_d1_regime_filter_short": False}),
            "long-only D1 on")

    def test_a_shape_the_catalog_does_not_cover_reports_none(self):
        # Not a crash and not a guess: the caller prints that production is
        # outside the documented set.
        partial = {"long-only D1 on": VARIANTS["long-only D1 on"]}
        self.assertIsNone(
            deployed_variant_name(VARIANTS["L:D1off S:D1on"], catalog=partial))

    def test_the_repo_config_is_a_variant_the_catalog_describes(self):
        # If this fails, production moved and the research documents' "deployed"
        # rows now describe something else.
        prep = prepare(load_bot_config("bot_config.yaml"))
        self.assertIsNotNone(deployed_variant_name(prep.base_strategy_config),
                             "XAU config matches no catalog variant")


class TestBootstrap(unittest.TestCase):
    def test_refuses_to_resample_a_handful_of_windows(self):
        self.assertEqual(bootstrap([1.0, 2.0, 3.0])["samples"], 0)

    def test_is_deterministic(self):
        nets = [1.0, -2.0, 3.5, 0.0, -0.5, 4.0, 2.5]
        self.assertEqual(bootstrap(nets, samples=500),
                         bootstrap(nets, samples=500))

    def test_reports_the_window_count_it_resampled(self):
        nets = [1.0, -2.0, 3.5, 0.0, -0.5, 4.0]
        self.assertEqual(bootstrap(nets, samples=200)["windows_resampled"], 6)


if __name__ == "__main__":
    unittest.main()
