"""Pin the shipped XAU long-only + D1 profile.

The 2026-07-29 certificate disables automated SHORT entries and requires D1
confirmation for LONG entries. These tests fail loudly if shorts are re-enabled,
the long gate turns off, or the engine stops loading the 1d frame it depends on.
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


class TestWhitelistLongOnlyD1(unittest.TestCase):
    def test_long_entries_are_d1_gated(self):
        params = _xau_entry()["strategy_params"]
        long_gated, short_gated = _effective_sides(params)
        self.assertTrue(long_gated, "XAU longs MUST be D1-gated")
        # Kept explicit for fingerprint completeness even though shorts are off.
        self.assertTrue(short_gated)

    def test_automated_short_entries_are_disabled(self):
        entry = _xau_entry()
        self.assertFalse(bool(entry["strategy_params"].get("enable_short")))
        self.assertEqual(entry["allowed_sides"], ["long"])
        self.assertFalse(entry["short_live_enabled"])

    def test_confirm_timeframe_present(self):
        # The gate reads the D1 zone; without a confirm timeframe there is no
        # regime frame to read and every long entry would be blocked.
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

    def test_resolver_produces_long_only_d1(self):
        cfg = load_bot_config(os.path.join(REPO_ROOT, "bot_config.yaml"))
        name = resolve_strategy_name("XAUUSDT", cfg, None)
        _merged, strat, _ptf, regime_tf, use_d1, _used = _prepare_backtest_config(
            "XAUUSDT", name, cfg, None
        )
        long_gated, short_gated = _effective_sides(strat)
        self.assertTrue(long_gated)
        self.assertTrue(short_gated)
        self.assertFalse(strat["enable_short"])
        self.assertEqual(regime_tf, "1d")
        self.assertTrue(use_d1)

    def test_bot_config_matches_the_whitelist(self):
        cfg = load_bot_config(os.path.join(REPO_ROOT, "bot_config.yaml"))
        block = ((cfg.get("strategy") or {}).get("config") or {}).get(
            "xauby_actionzone"
        ) or {}
        wl = _xau_entry()["strategy_params"]
        for key in (
            "enable_short",
            "use_d1_regime_filter",
            "use_d1_regime_filter_long",
            "use_d1_regime_filter_short",
        ):
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
