"""Pin the shipped XAU D1 asymmetry.

XAU runs an asymmetric daily gate: SHORT entries require D1 confirmation, LONG
entries do not. That combination was chosen from measurement
(docs/research/xau_per_side_d1_test_2026-07-26.md) and is easy to invert by
accident, because the whitelist expresses it as `use_d1_regime_filter: true`
plus `use_d1_regime_filter_long: false` rather than naming the short side.

These tests fail loudly if the sides swap, if the gate silently turns off, or if
the engine stops loading the 1d frame the gate depends on.
"""
from __future__ import annotations

import json
import os
import unittest

from xauby.observability.replay_validation import load_bot_config
from xauby.backtest.service import _prepare_backtest_config, resolve_strategy_name
from xauby.runtime.pair_registry import _needs_d1
from xauby.strategies.cdc_action_zone.strategy import CDCActionZoneStrategy

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _xau_entry():
    with open(os.path.join(REPO_ROOT, "coin_whitelist.json")) as fh:
        data = json.load(fh)
    for entry in data.get("assets", []):
        if str(entry.get("symbol", "")).upper() == "XAU":
            return entry
    raise AssertionError("XAU entry missing from coin_whitelist.json")


def _effective_sides(cfg):
    """Resolve (long_gated, short_gated) the way analyze() does."""
    merged = {**CDCActionZoneStrategy.default_config(), **cfg}
    shared = bool(merged.get("use_d1_regime_filter", False))

    def pick(key):
        v = merged.get(key)
        if v is None:
            return shared
        return v if isinstance(v, bool) else str(v).lower() in ("true", "1", "yes", "on")

    return pick("use_d1_regime_filter_long"), pick("use_d1_regime_filter_short")


class TestWhitelistAsymmetry(unittest.TestCase):
    def test_shorts_gated_longs_free(self):
        params = _xau_entry()["strategy_params"]
        long_gated, short_gated = _effective_sides(params)
        self.assertFalse(long_gated, "XAU longs must NOT be D1-gated")
        self.assertTrue(short_gated, "XAU shorts MUST be D1-gated")

    def test_shorts_are_enabled_at_all(self):
        # A D1 gate on shorts is meaningless if shorts are off; that would make
        # the config silently equivalent to plain long-only D1-off.
        self.assertTrue(bool(_xau_entry()["strategy_params"].get("enable_short")))

    def test_confirm_timeframe_present(self):
        # The gate reads the D1 zone; without a confirm timeframe there is no
        # regime frame to read and every short would be blocked.
        self.assertEqual(str(_xau_entry().get("confirm_timeframe")), "1d")

    def test_engine_will_load_the_regime_frame(self):
        self.assertTrue(_needs_d1(_xau_entry()["strategy_params"]))

    def test_documentation_note_kept_out_of_strategy_params(self):
        # The note lives at asset level so it does not leak into the resolved
        # strategy config, config_used snapshots, or chart cache fingerprints.
        params = _xau_entry()["strategy_params"]
        self.assertFalse([k for k in params if k.startswith("_")])
        self.assertIn("_config_note", _xau_entry())


class TestResolvedConfigAgrees(unittest.TestCase):
    """bot_config.yaml and the whitelist must not disagree on this."""

    def test_resolver_produces_the_asymmetry(self):
        cfg = load_bot_config(os.path.join(REPO_ROOT, "bot_config.yaml"))
        name = resolve_strategy_name("XAUUSDT", cfg, None)
        _merged, strat, _ptf, regime_tf, use_d1, _used = _prepare_backtest_config(
            "XAUUSDT", name, cfg, None
        )
        long_gated, short_gated = _effective_sides(strat)
        self.assertFalse(long_gated)
        self.assertTrue(short_gated)
        self.assertEqual(regime_tf, "1d")
        self.assertTrue(use_d1)

    def test_bot_config_matches_the_whitelist(self):
        cfg = load_bot_config(os.path.join(REPO_ROOT, "bot_config.yaml"))
        block = ((cfg.get("strategy") or {}).get("config") or {}).get(
            "xauby_actionzone"
        ) or {}
        wl = _xau_entry()["strategy_params"]
        for key in ("use_d1_regime_filter", "use_d1_regime_filter_long"):
            self.assertEqual(
                block.get(key), wl.get(key),
                f"{key} disagrees between bot_config.yaml and coin_whitelist.json; "
                "the whitelist wins at runtime, so a mismatch silently misleads "
                "anyone reading the YAML",
            )

    def test_no_strategy_config_leak_into_resolved(self):
        cfg = load_bot_config(os.path.join(REPO_ROOT, "bot_config.yaml"))
        name = resolve_strategy_name("XAUUSDT", cfg, None)
        _m, strat, _p, _r, _u, _used = _prepare_backtest_config("XAUUSDT", name, cfg, None)
        self.assertFalse([k for k in strat if k.startswith("_")])


if __name__ == "__main__":
    unittest.main()
