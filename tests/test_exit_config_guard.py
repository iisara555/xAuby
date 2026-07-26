"""Startup guard: a partial TP hidden under a minimal_roi rung is dead config.

The ROI ladder always wins the tick — the engine runs `_apply_minimal_roi_exit`
before `_maybe_take_partial_tp`, whose first guard is `action == "SELL"`, and the
backtest PositionSimulator returns out of the bar after a minimal-ROI close. So a
partial-TP trigger at or above the ladder's first rung can never fire on either
path. The July 2026 XAU config shipped exactly that shape for weeks.

These tests pin the rule and the reachable case, so the guard cannot be relaxed
into a warning by accident and the partial-TP feature stays usable for pairs that
do not run a ladder.
"""
from __future__ import annotations

import json
import unittest

from xauby.observability.replay import PositionSimulator
from xauby.runtime.exits import resolve_minimal_roi, validate_exit_config
from xauby.strategies.signal import hold


HOUR = 3600

# The exact shape that shipped live on XAU: ladder from entry at 8%, partial at 12%.
DEAD_CONFIG = {
    "partial_tp_pct": 12.0,
    "partial_tp_fraction": 0.5,
    "minimal_roi": {"0": 8.0, "1440": 5.0, "4320": 3.0},
}


class TestGuardRejects(unittest.TestCase):
    def test_rejects_the_shipped_xau_shape(self):
        with self.assertRaises(ValueError) as ctx:
            validate_exit_config(DEAD_CONFIG, symbol="XAUUSDT")
        msg = str(ctx.exception)
        self.assertIn("XAUUSDT", msg)
        self.assertIn("unreachable", msg)
        # Both numbers must be in the message so the operator sees the conflict.
        self.assertIn("12", msg)
        self.assertIn("8", msg)

    def test_rejects_rung_equal_to_trigger(self):
        # Equal is still unreachable: reaching 12% crosses the 12% rung first.
        with self.assertRaises(ValueError):
            validate_exit_config(
                {"partial_tp_pct": 12.0, "minimal_roi": {"0": 12.0}}
            )

    def test_rejects_regardless_of_dict_key_order(self):
        # The active-from-entry rung is the lowest age, not the first dict key.
        cfg = {
            "partial_tp_pct": 12.0,
            "minimal_roi": {"4320": 3.0, "1440": 5.0, "0": 8.0},
        }
        self.assertEqual(resolve_minimal_roi(cfg)[0], (0.0, 8.0))
        with self.assertRaises(ValueError):
            validate_exit_config(cfg)

    def test_message_names_no_symbol_when_absent(self):
        with self.assertRaises(ValueError) as ctx:
            validate_exit_config({"partial_tp_pct": 12.0, "minimal_roi": {"0": 8.0}})
        self.assertFalse(str(ctx.exception).startswith(":"))


class TestGuardAllows(unittest.TestCase):
    def test_allows_rung_above_trigger(self):
        validate_exit_config(
            {
                "partial_tp_pct": 12.0,
                "partial_tp_fraction": 0.5,
                "minimal_roi": {"0": 20.0, "1440": 8.0},
            },
            symbol="XAUUSDT",
        )

    def test_allows_ladder_that_only_starts_later(self):
        # No rung active from entry, so the partial is reachable in the first day.
        validate_exit_config(
            {"partial_tp_pct": 12.0, "minimal_roi": {"1440": 5.0}}
        )

    def test_allows_partial_tp_without_ladder(self):
        validate_exit_config({"partial_tp_pct": 12.0, "partial_tp_fraction": 0.5})

    def test_allows_ladder_without_partial_tp(self):
        validate_exit_config({"minimal_roi": {"0": 8.0}})

    def test_allows_empty_and_none(self):
        validate_exit_config({})
        validate_exit_config(None)

    def test_ignores_disabled_partial_tp(self):
        # pct 0 or an out-of-range fraction disables the partial entirely.
        validate_exit_config({"partial_tp_pct": 0.0, "minimal_roi": {"0": 8.0}})
        validate_exit_config(
            {"partial_tp_pct": 12.0, "partial_tp_fraction": 1.0,
             "minimal_roi": {"0": 8.0}}
        )

    def test_ignores_zero_rung(self):
        # resolve_minimal_roi drops roi<=0 steps, so they cannot mask a partial.
        validate_exit_config({"partial_tp_pct": 12.0, "minimal_roi": {"0": 0.0}})


class TestCommittedConfig(unittest.TestCase):
    """The shipped config must satisfy the guard the engine runs at startup.

    This resolves through split_legacy_runtime_config the way the engine's
    _get_strategy_config does, rather than reading coin_whitelist.json alone.
    That distinction is the whole point: partial_tp_pct lived in BOTH
    bot_config.yaml's strategy block and the whitelist, so a whitelist-only
    check passed while the resolved config the engine actually sees still
    carried the dead key and would have refused to start.
    """

    def _pairs(self):
        with open("coin_whitelist.json", encoding="utf-8") as fh:
            whitelist = json.load(fh)
        assets = whitelist.get("assets") or []
        self.assertTrue(assets, "whitelist has no assets")
        return assets

    def test_resolved_config_passes_for_every_pair(self):
        import yaml

        from xauby.runtime.trading_config import split_legacy_runtime_config

        with open("bot_config.yaml", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)

        for asset in self._pairs():
            symbol = str(asset.get("pair") or asset.get("symbol") or "")
            with self.subTest(symbol=symbol):
                strategy_cfg, _trading = split_legacy_runtime_config(
                    cfg, asset["strategy"], symbol=symbol, for_live=True
                )
                validate_exit_config(strategy_cfg, symbol=symbol)

    def test_raw_whitelist_params_pass_too(self):
        for asset in self._pairs():
            with self.subTest(symbol=asset.get("symbol")):
                validate_exit_config(
                    asset.get("strategy_params") or {},
                    symbol=str(asset.get("symbol") or ""),
                )


class TestLadderPreemptsPartialInSimulator(unittest.TestCase):
    """Prove the guard is describing real behaviour, not a theoretical worry."""

    @staticmethod
    def _sim(**kwargs) -> PositionSimulator:
        defaults = dict(
            initial_balance=1000.0,
            fee_pct=0.0,
            slippage_bps=0.0,
            disable_stop_loss=True,
            position_pct=1.0,
        )
        defaults.update(kwargs)
        return PositionSimulator(**defaults)

    @staticmethod
    def _bar(sim, *, o, h, l, c, t):
        return sim.on_bar(
            open_p=o, high=h, low=l, close=c, atr=1.0, zone="GREEN",
            bar_time=t, signal=hold("test"),
        )

    def test_ladder_below_trigger_closes_before_partial_can_fire(self):
        # Entry 100, ladder rung 8% (108), partial trigger 12% (112). A bar
        # running to 115 crosses both — the ladder must take the whole position.
        sim = self._sim(
            minimal_roi=resolve_minimal_roi({"minimal_roi": {"0": 8.0}}),
            partial_tp_pct=12.0,
            partial_tp_fraction=0.5,
        )
        self.assertTrue(sim.try_open(100.0, 1.0, 0))
        self._bar(sim, o=101.0, h=115.0, l=100.5, c=114.0, t=4 * HOUR)
        self.assertIsNone(sim.position)
        self.assertEqual(len(sim.trades), 1)
        self.assertAlmostEqual(sim.trades[0].exit_price, 108.0)
        # Nothing was banked: the partial never ran.
        self.assertAlmostEqual(sim.trades[0].pnl_pct, 8.0)

    def test_ladder_above_trigger_lets_the_partial_bank(self):
        # Same bar, but the ladder now sits at 20% — the partial is reachable.
        sim = self._sim(
            minimal_roi=resolve_minimal_roi({"minimal_roi": {"0": 20.0}}),
            partial_tp_pct=12.0,
            partial_tp_fraction=0.5,
        )
        self.assertTrue(sim.try_open(100.0, 1.0, 0))
        qty0 = sim.position.qty
        self._bar(sim, o=101.0, h=115.0, l=100.5, c=114.0, t=4 * HOUR)
        self.assertIsNotNone(sim.position)
        self.assertTrue(sim.position.partial_taken)
        self.assertAlmostEqual(sim.position.qty, qty0 * 0.5)
        self.assertAlmostEqual(sim.position.banked_pnl, qty0 * 0.5 * 12.0)


if __name__ == "__main__":
    unittest.main()
