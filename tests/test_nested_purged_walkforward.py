from copy import deepcopy
from unittest import mock

import pandas as pd
import pytest

from xauby.backtest import walkforward
from xauby.backtest.walkforward import (
    TemporalLeakageError,
    nested_purged_plan_from_policy,
    nested_purged_walk_forward,
    run_slice,
    slice_bar_span,
)

FOUR_HOURS_MS = 4 * 3_600_000


def _frame(bars=120):
    start = int(pd.Timestamp("2024-01-01", tz="UTC").timestamp() * 1000)
    return pd.DataFrame(
        {
            "open_time": [start + i * FOUR_HOURS_MS for i in range(bars)],
            "open": [100.0 + i for i in range(bars)],
            "high": [101.0 + i for i in range(bars)],
            "low": [99.0 + i for i in range(bars)],
            "close": [100.5 + i for i in range(bars)],
            "volume": [10.0] * bars,
        }
    )


def _policy(**overrides):
    policy = {
        "method": "nested_purged_walk_forward",
        "outer_folds": 2,
        "inner_folds": 3,
        "outer_test_bars": 12,
        "outer_step_bars": 12,
        "inner_validation_bars": 8,
        "min_train_bars": 60,
        "min_inner_train_bars": 20,
        "purge_bars": 3,
        "embargo_bars": 2,
        "warmup_bars": 10,
    }
    policy.update(overrides)
    return policy


def _plan(df=None, **overrides):
    return nested_purged_plan_from_policy(df if df is not None else _frame(), _policy(**overrides))


def test_outer_and_inner_boundaries_are_past_only_and_non_overlapping():
    plan = _plan()
    first = plan.folds[0]

    assert (first.outer.train.start, first.outer.train.end) == (0, 91)
    assert (first.outer.embargo.start, first.outer.embargo.end) == (91, 93)
    assert (first.outer.purge.start, first.outer.purge.end) == (93, 96)
    assert (first.outer.evaluation.start, first.outer.evaluation.end) == (96, 108)
    assert [
        (split.evaluation.start, split.evaluation.end) for split in first.inner
    ] == [(67, 75), (75, 83), (83, 91)]

    for fold in plan.folds:
        assert not fold.outer.train.overlaps(fold.outer.evaluation)
        for inner in fold.inner:
            assert inner.evaluation.end <= fold.outer.train.end
            assert not inner.evaluation.overlaps(fold.outer.evaluation)
            assert inner.purge.bars == 3
            assert inner.embargo.bars == 2

    artifact = plan.to_dict()
    assert artifact["folds"][0]["outer"]["purge"]["bars"] == 3
    assert len(plan.fingerprint) == 64
    assert plan.fingerprint == _plan().fingerprint


def test_plan_uses_the_locked_recent_tail_and_rejects_overlapping_outer_tests():
    plan = _plan()
    assert plan.folds[-1].outer.evaluation.end == len(_frame())
    with pytest.raises(TemporalLeakageError, match="cannot overlap"):
        _plan(outer_step_bars=11)


def test_plan_rejects_insufficient_history_and_non_monotonic_data():
    with pytest.raises(TemporalLeakageError, match="insufficient rows"):
        _plan(_frame(70))

    reversed_frame = _frame().iloc[::-1].reset_index(drop=True)
    with pytest.raises(TemporalLeakageError, match="strictly increasing"):
        _plan(reversed_frame)

    duplicate = _frame()
    duplicate.loc[10, "open_time"] = duplicate.loc[9, "open_time"]
    with pytest.raises(TemporalLeakageError, match="unique"):
        _plan(duplicate)


def test_policy_method_and_all_locked_dimensions_are_required():
    with pytest.raises(TemporalLeakageError, match="method"):
        nested_purged_plan_from_policy(_frame(), _policy(method="ordinary_kfold"))
    policy = _policy()
    policy.pop("purge_bars")
    with pytest.raises(TemporalLeakageError, match="purge_bars"):
        nested_purged_plan_from_policy(_frame(), policy)


def test_warmup_slice_contains_only_past_bars_and_never_trades_them():
    df = _frame()
    split = _plan(df).folds[0].outer
    sliced = slice_bar_span(df, split.evaluation, warmup_bars=10)

    assert sliced.skip_bars == 10
    assert len(sliced.frame) == split.evaluation.bars + 10
    warmup = sliced.frame.iloc[: sliced.skip_bars]
    traded = sliced.frame.iloc[sliced.skip_bars :]
    assert warmup["open_time"].max() < traded["open_time"].min()
    assert traded["open_time"].min() == df.iloc[split.evaluation.start]["open_time"]


def test_poisoning_warmup_cannot_change_replay_statistics():
    df = _frame()
    evaluation = _plan(df).folds[0].outer.evaluation
    original = slice_bar_span(df, evaluation, warmup_bars=10)
    poisoned = deepcopy(original)
    poisoned.frame.loc[: poisoned.skip_bars - 1, "close"] = 10**12

    def fake_replay(frame, **kwargs):
        traded = frame.iloc[kwargs["min_bars_override"] :]
        return {"net_profit_pct": float(traded["close"].sum()), "total_trades": 1}

    call = {
        "strategy_name": "test",
        "strategy_config": {},
        "engine_config": {},
        "symbol": "BTCUSDT",
    }
    with mock.patch("xauby.backtest.replay.run_plugin_replay", fake_replay):
        before = run_slice(original, **call)
        after = run_slice(poisoned, **call)

    assert before == after


def test_poisoning_outer_holdout_cannot_change_inner_selection():
    df = _frame()
    plan = _plan(df, outer_folds=1)
    poisoned = df.copy()
    outer = plan.folds[0].outer.evaluation
    poisoned.loc[outer.start : outer.end - 1, "close"] = 10**12

    def fake_run(slice_, *, strategy_config, **_kwargs):
        traded = slice_.frame.iloc[slice_.skip_bars :]
        return {
            "net_profit_pct": float(traded["close"].mean()) + strategy_config["edge"],
            "total_trades": 1,
        }

    def score(items):
        return sum(item.net_pct for item in items)

    kwargs = {
        "plan": plan,
        "candidates": {"a": {"edge": 0.0}, "b": {"edge": 1.0}},
        "score_candidate": score,
        "strategy_name": "test",
        "engine_config": {},
        "symbol": "BTCUSDT",
    }
    with mock.patch.object(walkforward, "run_slice", fake_run):
        clean_result = nested_purged_walk_forward(df, **kwargs)[0]
        poisoned_result = nested_purged_walk_forward(poisoned, **kwargs)[0]

    assert clean_result.inner_scores == poisoned_result.inner_scores
    assert clean_result.selected_candidate == poisoned_result.selected_candidate == "b"
    assert clean_result.outer_result.net_pct != poisoned_result.outer_result.net_pct


def test_selector_sees_only_inner_results_and_outer_runs_once_for_winner():
    df = _frame()
    plan = _plan(df, outer_folds=1)
    calls = []
    selector_windows = []

    def fake_run(slice_, *, strategy_config, **_kwargs):
        calls.append((slice_.window.label, strategy_config["id"]))
        return {"net_profit_pct": strategy_config["score"], "total_trades": 1}

    def score(items):
        selector_windows.extend(item.window.label for item in items)
        return sum(item.net_pct for item in items)

    with mock.patch.object(walkforward, "run_slice", fake_run):
        result = nested_purged_walk_forward(
            df,
            plan=plan,
            candidates={
                "z-loser": {"id": "loser", "score": 0.0},
                "a-winner": {"id": "winner", "score": 1.0},
            },
            score_candidate=score,
            strategy_name="test",
            engine_config={},
            symbol="BTCUSDT",
        )[0]

    outer_label = plan.folds[0].outer.evaluation.label
    assert all("inner" in label for label in selector_windows)
    assert calls.count((outer_label, "winner")) == 1
    assert (outer_label, "loser") not in calls
    assert result.selected_candidate == "a-winner"


def test_selection_tie_break_is_deterministic_and_non_finite_scores_fail():
    df = _frame()
    plan = _plan(df, outer_folds=1)

    def fake_run(_slice, **_kwargs):
        return {"net_profit_pct": 0.0, "total_trades": 0}

    base = {
        "plan": plan,
        "strategy_name": "test",
        "engine_config": {},
        "symbol": "BTCUSDT",
    }
    with mock.patch.object(walkforward, "run_slice", fake_run):
        result = nested_purged_walk_forward(
            df,
            candidates={"z": {}, "a": {}},
            score_candidate=lambda _items: 0.0,
            **base,
        )[0]
        assert result.selected_candidate == "a"
        with pytest.raises(TemporalLeakageError, match="non-finite"):
            nested_purged_walk_forward(
                df,
                candidates={"a": {}},
                score_candidate=lambda _items: float("nan"),
                **base,
            )


def test_runner_refuses_a_plan_built_for_different_data_length():
    plan = _plan()
    with pytest.raises(TemporalLeakageError, match="expects 120 rows"):
        nested_purged_walk_forward(
            _frame(121),
            plan=plan,
            candidates={"a": {}},
            score_candidate=lambda _items: 0.0,
            strategy_name="test",
            engine_config={},
            symbol="BTCUSDT",
        )


def test_runner_refuses_a_different_timeline_even_when_row_count_matches():
    plan = _plan()
    shifted = _frame()
    shifted["open_time"] += FOUR_HOURS_MS
    with pytest.raises(TemporalLeakageError, match="timeline"):
        nested_purged_walk_forward(
            shifted,
            plan=plan,
            candidates={"a": {}},
            score_candidate=lambda _items: 0.0,
            strategy_name="test",
            engine_config={},
            symbol="BTCUSDT",
        )
