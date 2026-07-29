from scripts.xau_okx_pf_grid import VARIANTS, grid_items


def test_grid_has_six_shapes_and_72_structural_cells_each():
    rows = grid_items()
    assert len(VARIANTS) == 6
    assert len(rows) == 432
    for variant in VARIANTS:
        assert sum(row["variant"] == variant for row in rows) == 72


def test_grid_has_one_live_and_one_long_only_d1_anchor():
    rows = grid_items()
    live = [row for row in rows if row["anchor_live"]]
    long_only = [row for row in rows if row["anchor_long_only_d1"]]
    assert len(live) == 1
    assert len(long_only) == 1

    assert live[0]["variant"] == "L:D1off S:D1on"
    assert live[0]["override"]["enable_short"] is True
    assert live[0]["override"]["use_d1_regime_filter_long"] is False
    assert live[0]["override"]["use_d1_regime_filter_short"] is True

    assert long_only[0]["variant"] == "long-only D1 on"
    assert long_only[0]["override"]["enable_short"] is False
    assert long_only[0]["override"]["use_d1_regime_filter_long"] is True
