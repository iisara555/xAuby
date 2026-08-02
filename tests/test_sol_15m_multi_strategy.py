"""Unit tests for the SOL 15m comparison harness.

These cover the parts that decide the study's conclusion — sentinel rejection,
the ranking formula, the gates, and warmup-preserving window arithmetic — so a
bug there is caught before a multi-hour grid run, not after.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "sol15m", ROOT / "scripts" / "sol_15m_multi_strategy.py"
)
sol15m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sol15m)


def _stats(pf=1.5, mdd=8.0, trades=500, net=20.0) -> dict:
    return {
        "profit_factor": pf, "max_drawdown_pct": mdd,
        "total_trades": trades, "net_profit_pct": net,
    }


class TestCleanPF(unittest.TestCase):
    def test_normal_pf_passes_through(self):
        self.assertAlmostEqual(sol15m.clean_pf(_stats(pf=1.42)), 1.42)

    def test_no_losing_trade_sentinel_is_rejected(self):
        """99.9 means 'no losing trades', an artefact, not an infinite edge."""
        self.assertIsNone(sol15m.clean_pf(_stats(pf=99.9)))

    def test_zero_pf_sentinel_is_rejected(self):
        self.assertIsNone(sol15m.clean_pf(_stats(pf=0.0)))

    def test_pf_is_clamped(self):
        self.assertEqual(sol15m.clean_pf(_stats(pf=12.0)), sol15m.PF_CLAMP)

    def test_missing_stats_is_none(self):
        self.assertIsNone(sol15m.clean_pf(None))
        self.assertIsNone(sol15m.clean_pf({}))


class TestRobustScore(unittest.TestCase):
    def test_uses_the_worst_window(self):
        """A great OOS cannot rescue a bad IS."""
        good = _stats(pf=2.5)
        bad = _stats(pf=1.05)
        strong = sol15m.robust_score(good, good, good)
        weak = sol15m.robust_score(bad, good, good)
        self.assertLess(weak, strong)

    def test_drawdown_of_mdd_ref_halves_the_score(self):
        flat = sol15m.robust_score(_stats(mdd=0.0), _stats(mdd=0.0), _stats(mdd=0.0))
        deep = sol15m.robust_score(
            _stats(mdd=0.0), _stats(mdd=0.0), _stats(mdd=sol15m.MDD_REF)
        )
        self.assertAlmostEqual(deep, flat / 2.0, places=9)

    def test_lower_drawdown_wins_at_equal_pf(self):
        shallow = sol15m.robust_score(_stats(), _stats(), _stats(mdd=5.0))
        deep = sol15m.robust_score(_stats(), _stats(), _stats(mdd=20.0))
        self.assertGreater(shallow, deep)

    def test_a_sentinel_anywhere_disqualifies(self):
        self.assertEqual(
            sol15m.robust_score(_stats(pf=99.9), _stats(), _stats()),
            float("-inf"),
        )


class TestGates(unittest.TestCase):
    def test_default_stats_pass(self):
        self.assertTrue(sol15m.passes_gate(_stats(), _stats(trades=100)))

    def test_too_few_is_trades_fails(self):
        self.assertFalse(
            sol15m.passes_gate(_stats(trades=sol15m.MIN_IS_TRADES - 1), _stats(trades=100))
        )

    def test_too_few_oos_trades_fails(self):
        self.assertFalse(
            sol15m.passes_gate(_stats(), _stats(trades=sol15m.MIN_OOS_TRADES - 1))
        )

    def test_negative_oos_net_fails(self):
        self.assertFalse(sol15m.passes_gate(_stats(), _stats(trades=100, net=-1.0)))

    def test_negative_is_net_fails(self):
        self.assertFalse(sol15m.passes_gate(_stats(net=-1.0), _stats(trades=100)))


class TestGridBuilder(unittest.TestCase):
    def test_cartesian_product_size_and_ids(self):
        combos = sol15m._grid({"base": 1}, {"a": [1, 2], "b": ["x", "y", "z"]})
        self.assertEqual(len(combos), 6)
        cid, ov = combos[0]
        self.assertEqual(cid, "a=1_b=x")
        self.assertEqual(ov, {"base": 1, "a": 1, "b": "x"})

    def test_every_grid_is_within_the_cap(self):
        for name, combos in sol15m.PF_GRIDS.items():
            self.assertLessEqual(len(combos), sol15m.MAX_GRID_PER_STRATEGY, name)

    def test_every_strategy_runs_long_and_short(self):
        """Unequal side coverage would make the ranking meaningless."""
        for name, combos in sol15m.PF_GRIDS.items():
            for _cid, ov in combos:
                self.assertTrue(ov.get("enable_short"), name)


class TestWindowArithmetic(unittest.TestCase):
    def _frame(self, n=1000, start=1_600_000_000):
        return pd.DataFrame({
            "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
            "timestamp": [start + i * 900 for i in range(n)],
        })

    def test_warmup_bars_are_prepended_and_skipped(self):
        df = self._frame()
        start_ts = int(df["timestamp"].iloc[500])
        frame, skip = sol15m._window(df, start_ts, None, 100)
        self.assertEqual(skip, 100)
        # The frame starts 100 bars BEFORE the requested start.
        self.assertEqual(int(frame["timestamp"].iloc[0]),
                         int(df["timestamp"].iloc[400]))
        # And bar `skip` is exactly the requested start.
        self.assertEqual(int(frame["timestamp"].iloc[skip]), start_ts)

    def test_warmup_clamps_at_the_start_of_history(self):
        df = self._frame()
        frame, skip = sol15m._window(df, int(df["timestamp"].iloc[10]), None, 100)
        self.assertEqual(skip, 10)
        self.assertEqual(len(frame), len(df))

    def test_end_bound_is_exclusive(self):
        df = self._frame()
        end_ts = int(df["timestamp"].iloc[600])
        frame, _skip = sol15m._window(df, int(df["timestamp"].iloc[500]), end_ts, 0)
        self.assertLess(int(frame["timestamp"].iloc[-1]), end_ts)

    def test_no_bars_returns_none(self):
        df = self._frame()
        self.assertIsNone(sol15m._window(df, 10**12, None, 0))

    def test_ts_is_seconds_not_milliseconds(self):
        # 2022-01-01 UTC. A millisecond value would be 1000x larger and would
        # silently select zero bars.
        self.assertEqual(sol15m._ts("2022-01-01"), 1_640_995_200)


class TestCostScaling(unittest.TestCase):
    def test_scale_multiplies_fee_and_slippage(self):
        cfg = {"exchange": {"fee_pct": 0.0005}, "backtest": {"slippage_bps": 2.0}}
        out = sol15m._scaled_cost_config(cfg, 2.0)
        self.assertAlmostEqual(out["exchange"]["fee_pct"], 0.001)
        self.assertAlmostEqual(out["backtest"]["slippage_bps"], 4.0)

    def test_scale_of_one_is_the_same_object(self):
        cfg = {"exchange": {"fee_pct": 0.0005}}
        self.assertIs(sol15m._scaled_cost_config(cfg, 1.0), cfg)

    def test_original_config_is_not_mutated(self):
        cfg = {"exchange": {"fee_pct": 0.0005}, "backtest": {"slippage_bps": 2.0}}
        sol15m._scaled_cost_config(cfg, 2.0)
        self.assertAlmostEqual(cfg["exchange"]["fee_pct"], 0.0005)


class TestBootstrapAndStress(unittest.TestCase):
    def test_pf_from_returns(self):
        self.assertAlmostEqual(sol15m._pf_from_returns([3.0, -1.0, -2.0]), 1.0)

    def test_pf_none_when_no_losses(self):
        self.assertIsNone(sol15m._pf_from_returns([1.0, 2.0]))

    def test_bootstrap_needs_enough_trades(self):
        self.assertIsNone(sol15m._bootstrap_pf([1.0, -1.0], block=1))

    def test_bootstrap_is_deterministic_and_bracketing(self):
        rets = [1.0, -0.5, 2.0, -1.0, 0.5] * 20
        a = sol15m._bootstrap_pf(rets, block=1, samples=200)
        b = sol15m._bootstrap_pf(rets, block=1, samples=200)
        self.assertEqual(a, b)
        self.assertLessEqual(a["lo"], a["hi"])
        self.assertTrue(0.0 <= a["p_below_1"] <= 1.0)

    def test_block_bootstrap_runs(self):
        rets = [1.0, -0.5, 2.0, -1.0, 0.5] * 20
        out = sol15m._bootstrap_pf(rets, block=20, samples=200)
        self.assertIsNotNone(out)

    def test_stress_cut_removes_the_largest_winners(self):
        rets = [10.0, 9.0, 8.0, 7.0, 6.0, 1.0, -1.0, -2.0]
        out = sol15m._stress_cut_top(rets, k=5)
        self.assertEqual(out["removed"], [10.0, 9.0, 8.0, 7.0, 6.0])
        # Only 1.0 of profit left against 3.0 of loss.
        self.assertAlmostEqual(out["pf"], 1.0 / 3.0)


if __name__ == "__main__":
    unittest.main()


class TestBuyAndHold(unittest.TestCase):
    """B&H is the decisive benchmark for a long-only book, so it is pinned."""

    def _frame(self, closes):
        n = len(closes)
        return pd.DataFrame({
            "open": closes, "high": closes, "low": closes, "close": closes,
            "volume": [1.0] * n,
            "timestamp": [1_600_000_000 + i * 900 for i in range(n)],
        })

    def setUp(self):
        # buy_and_hold reads fees off _G; populate it the way a worker would.
        sol15m._G.setdefault("merged", {})
        for name in ("supertrend_ema200",):
            sol15m._G["merged"].setdefault(name, {
                "exchange": {"fee_pct": 0.0005},
                "backtest": {"slippage_bps": 2.0},
            })

    def test_doubling_price_is_roughly_plus_100pct(self):
        out = sol15m.buy_and_hold(self._frame([100.0] * 10 + [200.0]), 10 - 10)
        self.assertAlmostEqual(out["net_profit_pct"], 100.0, delta=1.0)

    def test_costs_make_a_flat_market_slightly_negative(self):
        out = sol15m.buy_and_hold(self._frame([100.0] * 20), 0)
        self.assertLess(out["net_profit_pct"], 0.0)
        self.assertGreater(out["net_profit_pct"], -1.0)

    def test_skip_bars_are_excluded_from_the_benchmark(self):
        # Price triples in the warmup, then is flat. B&H must ignore the warmup.
        closes = [100.0, 200.0, 300.0] + [300.0] * 10
        out = sol15m.buy_and_hold(self._frame(closes), 3)
        self.assertAlmostEqual(out["net_profit_pct"], 0.0, delta=1.0)

    def test_drawdown_is_measured(self):
        out = sol15m.buy_and_hold(self._frame([100.0, 150.0, 75.0, 120.0]), 0)
        # Peak 150 -> trough 75 is a 50% drawdown.
        self.assertAlmostEqual(out["max_drawdown_pct"], 50.0, delta=1.0)

    def test_too_short_is_safe(self):
        out = sol15m.buy_and_hold(self._frame([100.0]), 0)
        self.assertEqual(out["net_profit_pct"], 0.0)


class TestLongOnlySlate(unittest.TestCase):
    def test_slate_registry_shape(self):
        self.assertEqual(sorted(sol15m.SLATES), ["long-only", "long-short"])
        for name, cfg in sol15m.SLATES.items():
            self.assertNotEqual(cfg["grid_file"], "", name)
            self.assertGreater(len(cfg["grids"]), 0, name)

    def test_checkpoints_do_not_collide(self):
        files = {c["grid_file"] for c in sol15m.SLATES.values()}
        files |= {c["finalists_file"] for c in sol15m.SLATES.values()}
        self.assertEqual(len(files), 4, "slates must not share checkpoint files")

    def test_candidates_are_dual_thrust_and_simple_scalp_plus(self):
        self.assertEqual(sorted(sol15m.LONG_ONLY_GRIDS),
                         ["dual_thrust", "simple_scalp_plus"])

    def test_no_long_only_combo_enables_shorts(self):
        for name, combos in sol15m.LONG_ONLY_GRIDS.items():
            for _cid, ov in combos:
                self.assertFalse(ov.get("enable_short", False), name)

    def test_simple_scalp_plus_pins_the_coupled_confidence_knob(self):
        """conf = buy_count/7 + 0.22, so gridding both knobs is redundant."""
        for _cid, ov in sol15m.LONG_ONLY_GRIDS["simple_scalp_plus"]:
            self.assertLessEqual(ov["min_buy_confidence"], 0.6486,
                                 "confidence must not shadow the count gate")

    def test_inert_regime_knobs_are_absent_from_the_grid(self):
        # mtf_filter / require_macro_bull are no-ops at regime_timeframe=None.
        for _cid, ov in sol15m.LONG_ONLY_GRIDS["simple_scalp_plus"]:
            self.assertNotIn("mtf_filter", ov)
            self.assertNotIn("require_macro_bull", ov)
            self.assertNotIn("risk_reward", ov)

    def test_long_only_gates_are_lower_and_declared(self):
        self.assertLess(sol15m.LONG_ONLY_MIN_IS_TRADES, sol15m.MIN_IS_TRADES)
        self.assertLess(sol15m.LONG_ONLY_MIN_OOS_TRADES, sol15m.MIN_OOS_TRADES)

    def test_grids_are_within_the_cap(self):
        for name, combos in sol15m.LONG_ONLY_GRIDS.items():
            self.assertLessEqual(len(combos), sol15m.MAX_GRID_PER_STRATEGY, name)


class TestFinalistWindowFanout(unittest.TestCase):
    """Per-window fan-out must reassemble into exactly the old row shape."""

    def test_window_list_covers_the_validation_stack(self):
        names = sol15m._finalist_windows("dual_thrust", full_only=False)
        self.assertIn("full", names)
        self.assertIn("subperiod", names)
        self.assertIn("is", names)
        self.assertIn("oos", names)
        self.assertEqual(sum(1 for n in names if n.startswith("fold")), sol15m.N_FOLDS)
        # 1.0x is reused from the full run and must not be a task.
        self.assertNotIn("cost:1.0x", names)
        self.assertIn("cost:2.0x", names)

    def test_full_only_is_a_single_window(self):
        self.assertEqual(
            sol15m._finalist_windows("simple_scalp_plus", full_only=True), ["full"]
        )

    def _row(self, window, **kw):
        return {"id": "s:c", "strategy": "s", "combo": "c", "override": {},
                "passed_gate": False, "window": window, **kw}

    def test_merge_reassembles_a_config(self):
        rows = [
            self._row("full", stats={"profit_factor": 1.2, "net_profit_pct": 5.0},
                      bh={"net_profit_pct": 99.0}, trade_returns=[1.0, -0.5]),
            self._row("subperiod", stats={"profit_factor": 1.1}, bh={"net_profit_pct": 20.0}),
            self._row("is", stats={"profit_factor": 1.3}, bh={"net_profit_pct": 50.0}),
            self._row("oos", stats={"profit_factor": 0.9}, bh={"net_profit_pct": 10.0}),
            self._row("cost:2.0x", stats={"profit_factor": 0.8}, cost_label="2.0x"),
        ]
        # folds arrive out of order — the merge must sort them
        for i in (2, 0, 1):
            rows.append(self._row(f"fold{i}", stats={"profit_factor": float(i)},
                                  fold_index=i))
        merged = sol15m._merge_finalist_windows(rows)
        self.assertEqual(len(merged), 1)
        m = merged[0]
        self.assertEqual(m["full"]["profit_factor"], 1.2)
        self.assertEqual(m["bh_full"]["net_profit_pct"], 99.0)
        self.assertEqual(m["is"]["profit_factor"], 1.3)
        self.assertEqual(m["oos"]["profit_factor"], 0.9)
        self.assertEqual(m["trade_returns"], [1.0, -0.5])
        self.assertEqual([f["profit_factor"] for f in m["folds"]], [0.0, 1.0, 2.0])
        self.assertEqual(m["costs"]["2.0x"]["profit_factor"], 0.8)
        # 1.0x is the full-span run, reused not replayed
        self.assertEqual(m["costs"]["1.0x"]["profit_factor"], 1.2)

    def test_merge_keeps_configs_separate(self):
        rows = [
            {"id": "a:1", "strategy": "a", "combo": "1", "window": "full",
             "stats": {"profit_factor": 1.0}, "bh": {}},
            {"id": "b:2", "strategy": "b", "combo": "2", "window": "full",
             "stats": {"profit_factor": 2.0}, "bh": {}},
        ]
        merged = sol15m._merge_finalist_windows(rows)
        self.assertEqual(len(merged), 2)

    def test_errored_window_is_recorded_not_merged_as_stats(self):
        rows = [
            self._row("full", stats={"profit_factor": 1.0}, bh={}),
            self._row("fold3", error="Boom"),
        ]
        merged = sol15m._merge_finalist_windows(rows)
        self.assertEqual(len(merged[0]["folds"]), 0)
        self.assertIn("fold3: Boom", merged[0]["errors"])
