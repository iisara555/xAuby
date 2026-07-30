from scripts import btc_supertrend_okx_pf_grid as grid
from scripts import btc_supertrend_okx_pf_grid_shard as shard


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


def test_shards_partition_the_grid_exactly_once():
    items = grid.grid_items()
    partitions = [shard.shard_items(items, index, 8) for index in range(8)]
    ids = [item["id"] for partition in partitions for item in partition]
    assert len(ids) == len(items)
    assert len(set(ids)) == len(items)
    assert {len(partition) for partition in partitions} == {72}


def test_shard_union_verifier_rejects_missing_or_duplicate_cells():
    rows = [{"id": item["id"]} for item in grid.grid_items()]
    assert len(shard.verify_shard_rows(rows)) == len(rows)
    try:
        shard.verify_shard_rows(rows[:-1] + [rows[0]])
    except RuntimeError as exc:
        assert "invalid shard union" in str(exc)
    else:
        raise AssertionError("invalid shard union was accepted")
