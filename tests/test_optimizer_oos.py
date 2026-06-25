"""Out-of-sample selection in the grid optimizer."""
from __future__ import annotations

import unittest

import pandas as pd

from xauby.backtest.optimizer import _evaluate_grid, _select_best


class _Bundle:
    """Minimal stand-in for BacktestReplayBundle with a real df."""

    def __init__(self, n, merged_cfg):
        self.df = pd.DataFrame(
            {
                "timestamp": [1_700_000_000 + i * 14_400 for i in range(n)],
                "open": [100.0] * n, "high": [101.0] * n,
                "low": [99.0] * n, "close": [100.0] * n, "volume": [10.0] * n,
            }
        )
        self.merged_cfg = merged_cfg
        self.strat_cfg = {}


class _Meta:
    def __init__(self, ok=True):
        self.run_ok = ok
        self.config_used = {}
        self.data_symbol = "BTCUSDT"
        self.symbol = "BTCUSDT"
        self.used_data_proxy = False


class _Res:
    def __init__(self, **stats):
        self.meta = _Meta()
        base = {"net_profit_pct": 0.0, "win_rate": 0.0, "total_trades": 1,
                "max_drawdown_pct": 1.0, "profit_factor": 1.0, "sharpe": 0.0}
        base.update(stats)
        self.stats = base


class TestOosEvaluation(unittest.TestCase):
    def test_falls_back_without_enough_bars(self):
        # Stub bundle has no usable df length -> single full-window run.
        bundle = _Bundle(10, {"backtest": {"optimizer": {"oos_warmup_bars": 100}}})
        calls = {"n": 0}

        def fake_run(b, *, strat_cfg_override=None, df_override=None):
            calls["n"] += 1
            self.assertIsNone(df_override)  # legacy path passes no slice
            return _Res(net_profit_pct=5.0)

        import xauby.backtest.optimizer as opt
        orig = opt.run_replay_from_bundle
        opt.run_replay_from_bundle = fake_run
        try:
            results, used_oos = _evaluate_grid(bundle, ["sl_atr_mult"], [(2.0,), (2.5,)])
        finally:
            opt.run_replay_from_bundle = orig
        self.assertFalse(used_oos)
        self.assertEqual(calls["n"], 2)  # one run per combo

    def test_oos_runs_two_windows_and_selects_robust(self):
        bundle = _Bundle(400, {"backtest": {"optimizer": {
            "oos_split_ratio": 0.7, "oos_warmup_bars": 100}}})

        # combo A: great in-sample, terrible out-of-sample (overfit).
        # combo B: modest but positive in both (robust) -> should win.
        plan = {
            (2.0,): [_Res(net_profit_pct=99.0), _Res(net_profit_pct=-50.0, sharpe=-2.0)],
            (2.5,): [_Res(net_profit_pct=10.0), _Res(net_profit_pct=8.0, sharpe=1.5)],
        }
        seq = {k: list(v) for k, v in plan.items()}

        def fake_run(b, *, strat_cfg_override=None, df_override=None):
            key = (strat_cfg_override["sl_atr_mult"],)
            return seq[key].pop(0)  # 1st call = IS, 2nd = OOS

        import xauby.backtest.optimizer as opt
        orig = opt.run_replay_from_bundle
        opt.run_replay_from_bundle = fake_run
        try:
            results, used_oos = _evaluate_grid(bundle, ["sl_atr_mult"], [(2.0,), (2.5,)])
        finally:
            opt.run_replay_from_bundle = orig

        self.assertTrue(used_oos)
        best = _select_best(results, used_oos)
        # The overfit combo (negative OOS) must NOT win.
        self.assertEqual(best["sl_atr_mult"], 2.5)
        self.assertEqual(best["oos_net_profit_pct"], 8.0)
        self.assertEqual(best["is_net_profit_pct"], 10.0)


if __name__ == "__main__":
    unittest.main()
