import pytest

from scripts.xau_champion_search import (
    CONFIG_SHAPES,
    LIVE_CELL,
    ROI_LADDERS,
    SKIP_STRATEGIES,
    _balanced_rank,
    config_items,
    strategy_items,
)
from scripts.xau_harness import prepare
from xauby.backtest.walkforward import resolve_variant
from xauby.observability.replay_validation import load_bot_config
from xauby.runtime.exits import resolve_minimal_roi
from xauby.strategies.registry import available_strategies


def test_config_grid_size_and_shape():
    items = config_items()
    # 2 shapes x 2 ap x 3 fresh x 2 slope x 3 thrust x 4 ladders
    assert len(items) == 288
    assert len({item["id"] for item in items}) == 288
    for shape in CONFIG_SHAPES:
        assert sum(item["cell"]["shape"] == shape for item in items) == 144


def test_every_cell_states_the_whole_d1_control_group():
    """A partial D1 spec inherits from the base config and silently collapses
    variants together — `resolve_variant` is the guard, so route every cell
    through it rather than trusting the table."""
    base = prepare(load_bot_config("bot_config.yaml")).base_strategy_config
    for item in config_items():
        resolved = resolve_variant(base, item["override"])
        assert resolved["use_d1_regime_filter_long"] is (
            item["cell"]["shape"] == "long-only D1 on"
        )
        assert resolved["enable_short"] is False


def test_deployed_cell_is_searched():
    items = config_items()
    anchors = [item for item in items if item["anchor_live"]]
    assert len(anchors) == 1
    assert anchors[0]["cell"] == LIVE_CELL
    assert anchors[0]["override"]["minimal_roi"] == ROI_LADDERS["live"]


def test_live_cell_matches_the_repo_config():
    """If production moves, this fails instead of the study quietly measuring
    a baseline that is no longer deployed."""
    base = prepare(load_bot_config("bot_config.yaml")).base_strategy_config
    assert base["ap_smoothing"] == LIVE_CELL["ap_smoothing"]
    assert base["fresh_zone_window"] == LIVE_CELL["fresh_zone_window"]
    assert base["require_slow_slope"] == LIVE_CELL["require_slow_slope"]
    assert base["entry_thrust_min"] == LIVE_CELL["entry_thrust_min"]
    assert base["exit_on_bear_cross"] == LIVE_CELL["exit_on_bear_cross"]
    assert base["enable_short"] is False
    assert base["use_d1_regime_filter_long"] is True
    assert dict(base["minimal_roi"]) == {str(k): v
                                         for k, v in ROI_LADDERS["live"].items()}


def test_roi_ladders_are_all_distinct_and_none_is_a_real_option():
    assert ROI_LADDERS["none"] is None
    ladders = [resolve_minimal_roi({"minimal_roi": v})
               for v in ROI_LADDERS.values()]
    assert [] in ladders  # the "no ladder" arm really disables the exit
    non_empty = [tuple(x) for x in ladders if x]
    assert len(set(non_empty)) == len(non_empty)


def test_strategy_stage_covers_every_rival_long_and_short():
    items = strategy_items()
    names = {item["strategy"] for item in items}
    assert "xauby_actionzone" not in names, "the incumbent is the benchmark"
    assert not (names & SKIP_STRATEGIES)
    assert names == set(available_strategies()) - SKIP_STRATEGIES
    for name in names:
        sides = {item["enable_short"] for item in items if item["strategy"] == name}
        assert sides == {False, True}


def test_balanced_rank_prefers_the_weaker_window():
    """Worst-of-IS/OOS first: a cell that is spectacular in one window and thin
    in the other must not outrank a consistent one."""
    def row(id_, is_pf, oos_pf):
        block = lambda pf: {"profit_factor": pf, "net_profit_pct": 10.0,
                            "total_trades": 100}
        return {"id": id_, "is": block(is_pf), "oos": block(oos_pf)}

    ranked = _balanced_rank([row("lopsided", 4.0, 1.1), row("steady", 1.9, 1.8)])
    assert [r["id"] for r in ranked] == ["steady", "lopsided"]


def test_balanced_rank_drops_cells_failing_the_validity_gate():
    thin = {"id": "thin",
            "is": {"profit_factor": 9.0, "net_profit_pct": 50.0, "total_trades": 3},
            "oos": {"profit_factor": 9.0, "net_profit_pct": 50.0, "total_trades": 2}}
    losing = {"id": "losing",
              "is": {"profit_factor": 1.2, "net_profit_pct": -5.0, "total_trades": 90},
              "oos": {"profit_factor": 1.2, "net_profit_pct": 8.0, "total_trades": 40}}
    assert _balanced_rank([thin, losing]) == []


@pytest.mark.parametrize("stage", ("config", "strategy"))
def test_items_are_json_serialisable(stage):
    import json

    items = config_items() if stage == "config" else strategy_items()
    json.dumps(items)
