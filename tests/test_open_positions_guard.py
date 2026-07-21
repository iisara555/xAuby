"""Startup guard: max_open_positions keys must agree and cover live pairs.

Regression: the BTC deploy raised portfolio.max_open_positions to 3 while the
engine BUY gate (trading.max_open_positions) and the resolver
(risk.max_open_positions) stayed at 1 — the second live pair's entries were
silently blocked. validate_open_positions_config catches this class of
mismatch at startup instead of failing quietly at trade time.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.runtime.trading_config import validate_open_positions_config


class TestOpenPositionsGuard(unittest.TestCase):
    def test_matching_keys_pass(self):
        cfg = {
            "trading": {"max_open_positions": 2},
            "risk": {"max_open_positions": 2},
            "portfolio": {"max_open_positions": 2},
        }
        validate_open_positions_config(cfg)  # must not raise
        validate_open_positions_config(cfg, live_pair_count=2)  # must not raise

    def test_mismatched_keys_raise(self):
        cfg = {
            "trading": {"max_open_positions": 1},
            "risk": {"max_open_positions": 1},
            "portfolio": {"max_open_positions": 3},
        }
        with self.assertRaises(ValueError) as ctx:
            validate_open_positions_config(cfg)
        self.assertIn("disagree", str(ctx.exception))

    def test_partial_key_set_uses_only_present_keys(self):
        # Legacy configs may not set every block; only present keys must agree.
        cfg = {"trading": {"max_open_positions": 2}, "risk": {"max_open_positions": 2}}
        validate_open_positions_config(cfg)  # must not raise

    def test_cap_below_live_pair_count_raises(self):
        cfg = {
            "trading": {"max_open_positions": 1},
            "risk": {"max_open_positions": 1},
            "portfolio": {"max_open_positions": 1},
        }
        with self.assertRaises(ValueError) as ctx:
            validate_open_positions_config(cfg, live_pair_count=2)
        self.assertIn("below the 2 live", str(ctx.exception))

    def test_cap_at_or_above_live_pair_count_passes(self):
        cfg = {
            "trading": {"max_open_positions": 2},
            "risk": {"max_open_positions": 2},
            "portfolio": {"max_open_positions": 2},
        }
        validate_open_positions_config(cfg, live_pair_count=2)  # must not raise

    def test_non_integer_value_raises(self):
        cfg = {"trading": {"max_open_positions": "two"}}
        with self.assertRaises(ValueError) as ctx:
            validate_open_positions_config(cfg)
        self.assertIn("not an integer", str(ctx.exception))

    def test_empty_config_passes(self):
        validate_open_positions_config({})  # must not raise
        validate_open_positions_config({}, live_pair_count=0)  # must not raise

    def test_repo_bot_config_passes(self):
        import yaml

        cfg = yaml.safe_load((ROOT / "bot_config.yaml").read_text(encoding="utf-8"))
        validate_open_positions_config(cfg, live_pair_count=2)  # XAU + BTC live


if __name__ == "__main__":
    unittest.main()
