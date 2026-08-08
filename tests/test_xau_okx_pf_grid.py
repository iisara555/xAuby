from scripts.xau_harness import VARIANTS, deployed_variant_name, prepare
from scripts.xau_okx_pf_grid import ENTRY_KEYS, grid_items, live_entry_shape
from xauby.observability.replay_validation import load_bot_config


# The pre-rollout live shape, kept as a fixture rather than as the expected
# answer: these tests used to assert it directly, which made them pass only for
# as long as production stayed on it. It moved on 2026-07-29 and they did not.
PRE_ROLLOUT = {
    "enable_short": True,
    "use_d1_regime_filter": True,
    "use_d1_regime_filter_long": False,
    "use_d1_regime_filter_short": True,
    "ap_smoothing": 2,
    "fresh_zone_window": 3,
    "require_slow_slope": True,
    "slow_slope_bars": 3,
    "entry_thrust_min": 0.0,
    "exit_on_bear_cross": False,
}

LONG_ONLY_D1_ON = {
    "enable_short": False,
    "use_d1_regime_filter": True,
    "use_d1_regime_filter_long": True,
    "use_d1_regime_filter_short": True,
    "ap_smoothing": 2,
    "fresh_zone_window": 3,
    "require_slow_slope": False,
    "slow_slope_bars": 3,
    "entry_thrust_min": 0.5,
    "exit_on_bear_cross": False,
}


def test_grid_has_six_shapes_and_72_structural_cells_each():
    rows = grid_items(PRE_ROLLOUT)
    assert len(VARIANTS) == 6
    assert len(rows) == 432
    for variant in VARIANTS:
        assert sum(row["variant"] == variant for row in rows) == 72


def test_grid_cells_do_not_depend_on_the_deployed_config():
    """Only the anchor flags move with production; the search space is fixed."""
    before = grid_items(PRE_ROLLOUT)
    after = grid_items(LONG_ONLY_D1_ON)
    assert [row["id"] for row in before] == [row["id"] for row in after]
    assert [row["override"] for row in before] == [row["override"] for row in after]


def test_anchors_follow_the_deployed_config():
    rows = grid_items(PRE_ROLLOUT)
    live = [row for row in rows if row["anchor_live"]]
    long_only = [row for row in rows if row["anchor_long_only_d1"]]
    assert len(live) == 1
    assert len(long_only) == 1

    assert live[0]["variant"] == "L:D1off S:D1on"
    assert live[0]["override"]["enable_short"] is True
    assert live[0]["override"]["use_d1_regime_filter_long"] is False
    assert live[0]["override"]["entry_thrust_min"] == 0.0

    # Same entry shape, long-only D1 — the comparison anchor, not the live one.
    assert long_only[0]["variant"] == "long-only D1 on"
    assert long_only[0]["override"]["enable_short"] is False
    assert long_only[0]["override"]["entry_thrust_min"] == 0.0


def test_anchor_tracks_a_changed_deployed_config():
    rows = grid_items(LONG_ONLY_D1_ON)
    live = [row for row in rows if row["anchor_live"]]
    assert len(live) == 1
    assert live[0]["variant"] == "long-only D1 on"
    assert live[0]["override"]["entry_thrust_min"] == 0.5
    assert live[0]["override"]["require_slow_slope"] is False
    # With production already long-only D1 on, both anchors are the same cell.
    assert live[0]["anchor_long_only_d1"] is True


def test_no_config_yields_the_search_space_without_anchors():
    """`binance_th_spot_grid` reuses these cells for a pair the deployed OKX XAU
    config says nothing about, so it must be able to ask for cells alone."""
    rows = grid_items()
    assert len(rows) == 432
    assert not any(row["anchor_live"] for row in rows)
    assert not any(row["anchor_long_only_d1"] for row in rows)


def test_live_entry_shape_normalises_dead_slope_bars():
    """slope_bars is dead config when the slope filter is off.

    The grid emits one cell per (require_slope, slope_bars) pair, so a deployed
    config carrying slope_bars 5 with the filter off would match no cell at all
    and silently lose its anchor.
    """
    off = live_entry_shape({**LONG_ONLY_D1_ON, "slow_slope_bars": 5})
    assert off["slow_slope_bars"] == 3
    on = live_entry_shape({**LONG_ONLY_D1_ON, "require_slow_slope": True,
                           "slow_slope_bars": 5})
    assert on["slow_slope_bars"] == 5


def test_repo_config_is_a_cell_in_the_grid():
    """The run refuses to start without this, so assert it here too."""
    prep = prepare(load_bot_config("bot_config.yaml"))
    base = prep.base_strategy_config
    assert deployed_variant_name(base) in VARIANTS
    assert set(live_entry_shape(base)) == set(ENTRY_KEYS)
    rows = grid_items(base)
    assert sum(1 for row in rows if row["anchor_live"]) == 1
