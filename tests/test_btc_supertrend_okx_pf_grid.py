from scripts import btc_supertrend_okx_pf_grid as grid


def test_grid_is_predeclared_and_has_unique_ids():
    items = grid.grid_items()
    assert len(items) == 576
    assert len({item["id"] for item in items}) == len(items)
    assert {item["variant"] for item in items} == set(grid.VARIANTS)


def test_grid_contains_exact_required_anchors():
    items = grid.grid_items()
    live = [item for item in items if item["anchor_live"]]
    long_d1 = [item for item in items if item["anchor_long_only_d1"]]
    assert len(live) == 1
    assert len(long_d1) == 1
    assert live[0]["variant"] == "long+short D1 off"
    assert long_d1[0]["variant"] == "long-only D1 on"
    for item in (live[0], long_d1[0]):
        assert item["override"]["supertrend_mult"] == 4.0
        assert item["override"]["atr_period"] == 10
        assert item["override"]["sl_atr_mult"] == 3.0
        assert item["override"]["trailing_atr_mult"] == 2.0
        assert item["override"]["exit_on_ema_loss"] is True


def test_every_variant_declares_complete_d1_control_group():
    for spec in grid.VARIANTS.values():
        assert all(key in spec for key in grid.D1_KEYS)


def test_repo_live_btc_shape_is_the_expected_anchor():
    cfg = grid.load_bot_config("bot_config.yaml")
    prep = grid.prepare(cfg)
    assert prep.primary_timeframe == "4h"
    assert grid.variant_name(prep.base_strategy_config) == "long+short D1 off"
