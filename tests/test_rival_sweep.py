import pandas as pd
import pytest

from scripts.rival_sweep import (
    SHORT_ONLY,
    format_row,
    gates_on_d1,
    passes,
    rival_items,
    run_rival,
)
from xauby.strategies.registry import available_strategies


def test_incumbent_and_short_only_plugins_are_never_candidates():
    items = rival_items("supertrend_ema200")
    names = {item["strategy"] for item in items}
    assert "supertrend_ema200" not in names
    assert not (names & SHORT_ONLY)
    assert names == set(available_strategies()) - SHORT_ONLY - {"supertrend_ema200"}


def test_every_candidate_is_run_both_ways():
    items = rival_items("xauby_actionzone")
    for name in {item["strategy"] for item in items}:
        arms = {item["enable_short"] for item in items if item["strategy"] == name}
        assert arms == {False, True}


@pytest.mark.parametrize(
    "cfg,expected",
    [
        ({}, False),
        ({"use_d1_regime_filter": False}, False),
        ({"use_d1_regime_filter": True}, True),
        # Per-side keys default to None meaning "follow the shared flag", so a
        # shared True with unset sides still reads the daily frame.
        ({"use_d1_regime_filter": True,
          "use_d1_regime_filter_long": None,
          "use_d1_regime_filter_short": None}, True),
        # One side gated is enough to need the frame.
        ({"use_d1_regime_filter": False,
          "use_d1_regime_filter_long": True,
          "use_d1_regime_filter_short": False}, True),
        ({"use_d1_regime_filter": False,
          "use_d1_regime_filter_long": False,
          "use_d1_regime_filter_short": False}, False),
    ],
)
def test_gates_on_d1(cfg, expected):
    assert gates_on_d1(cfg) is expected


def test_a_d1_gated_plugin_without_a_daily_frame_raises():
    """The failure this replaces was silent: no daily frame means the gate reads
    UNKNOWN and blocks every entry, so the plugin reports zero trades and looks
    like it simply has no edge. `xauby_actionzone` did exactly that on the first
    BTC sweep. Failing loudly is the point of this test."""
    frame = pd.DataFrame({
        "open_time": [0], "open": [1.0], "high": [1.0],
        "low": [1.0], "close": [1.0], "volume": [1.0],
    })
    with pytest.raises(RuntimeError, match="gates on the D1 regime frame"):
        run_rival(
            frame,
            {"strategy": "xauby_actionzone", "override": {}},
            symbol="BTCUSDT",
            engine_config={"strategy": {"config": {
                "xauby_actionzone": {"use_d1_regime_filter": True},
            }}},
            skip_bars=0,
            label="BTC-USDT-SWAP",
            df_regime=None,
        )


def test_format_row_shows_exposure_next_to_net():
    """Net is not comparable across strategies with different sizing models, so
    the exposure that explains the gap has to be on the same line."""
    line = format_row({"full": {"profit_factor": 1.5, "net_profit_pct": 12.0,
                                "max_drawdown_pct": 3.0, "exposure_pct": 17.0,
                                "total_trades": 40}}, "full")
    assert "PF 1.500" in line
    assert "+12.00%" in line
    assert "exp 17.0%" in line
    assert format_row({}, "full") == "—"


def test_validity_gate_requires_trades_and_two_profitable_windows():
    def row(is_n, oos_n, is_net, oos_net):
        return {"is": {"total_trades": is_n, "net_profit_pct": is_net},
                "oos": {"total_trades": oos_n, "net_profit_pct": oos_net}}

    assert passes(row(30, 10, 5.0, 5.0), min_is=30, min_oos=10)
    assert not passes(row(29, 10, 5.0, 5.0), min_is=30, min_oos=10)
    assert not passes(row(30, 9, 5.0, 5.0), min_is=30, min_oos=10)
    assert not passes(row(30, 10, -1.0, 5.0), min_is=30, min_oos=10)
    assert not passes(row(30, 10, 5.0, -1.0), min_is=30, min_oos=10)
    assert not passes({"error": "boom"}, min_is=0, min_oos=0)
