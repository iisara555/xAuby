from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.certify_btc_ensemble import (
    _economics,
    _preset,
    _research_member,
    _run_arm,
    combine_arms,
    ensemble_fingerprint,
    evaluate_gate,
    frame_sha256,
    require_locked_identity,
    workflow_provenance,
)
from xauby.observability.replay_validation import load_bot_config
from xauby.strategies.donchian_trend.strategy import DonchianTrendStrategy


ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = (
    ROOT / "docs/research/protocols/btc_supertrend_donchian_ensemble_v1.json"
)


def _manifest() -> dict:
    return json.loads(PROTOCOL.read_text(encoding="utf-8"))


def _arm(equity: list[float], sides: list[str | None], pnls: list[float]) -> dict:
    timestamps = [1_577_836_800 + index * 2_678_400 for index in range(len(equity))]
    return {
        "trace": {
            "timestamps": timestamps,
            "equity_curve": equity,
            "position_side_curve": sides,
        },
        "trades": [{"pnl": pnl} for pnl in pnls],
    }


def test_locked_identity_matches_current_champion_and_separate_4h_research_preset():
    manifest = _manifest()
    champion = _preset(manifest["champion_preset_id"])
    research = _research_member(manifest)

    require_locked_identity(
        manifest,
        champion,
        research,
        _economics(load_bot_config(str(ROOT / "bot_config.yaml"))),
    )

    assert ensemble_fingerprint(manifest) == "6d8e512803204b6e"
    assert research["primary_timeframe"] == "4h"
    assert research["execution_profile"] == {
        "entry_len": 48,
        "exit_len": 24,
        "ema_period": 200,
        "atr_period": 14,
        "adx_period": 14,
        "adx_min": 20.0,
        "vol_ma_period": 20,
        "vol_min_ratio": 0.0,
        "sl_atr_mult": 2.0,
        "trailing_atr_mult": 6.0,
        "fixed_tp_pct": 0.0,
        "breakeven_sl_enabled": True,
        "breakeven_activation_atr_mult": 1.5,
        "breakeven_buffer_atr_mult": 0.1,
        "exit_on_ema_loss": True,
        "max_calc_bars": 1000,
        "cool_down_minutes": 240,
    }
    # The registered strategy contract stays 1H; the 4H member is an explicit
    # research preset and cannot silently alter existing users.
    assert DonchianTrendStrategy.required_timeframes == ["1h"]


@pytest.mark.parametrize("mutation", ["weight", "config", "economics"])
def test_locked_identity_rejects_drift(mutation: str):
    manifest = _manifest()
    champion = _preset(manifest["champion_preset_id"])
    research = _research_member(manifest)
    economics = _economics(load_bot_config(str(ROOT / "bot_config.yaml")))
    if mutation == "weight":
        manifest["members"][0]["weight"] = 0.6
        manifest["members"][1]["weight"] = 0.4
    elif mutation == "config":
        research["execution_profile"]["entry_len"] = 49
    else:
        economics["slippage_bps"] = 3.0

    with pytest.raises(ValueError):
        require_locked_identity(manifest, champion, research, economics)


def test_continuous_virtual_sleeves_are_weighted_and_report_conflicts():
    champion = _arm([1000.0, 1100.0, 1050.0], ["LONG", "LONG", None], [100, -50])
    donchian = _arm([1000.0, 900.0, 1200.0], [None, "SHORT", None], [-100, 300])

    combined = combine_arms(champion, donchian, champion_weight=0.5)

    assert combined["final_balance"] == 1125.0
    assert combined["net_profit_pct"] == 12.5
    assert combined["total_trades"] == 4
    assert combined["gross_profit"] == 200.0
    assert combined["gross_loss"] == 75.0
    assert combined["profit_factor"] == pytest.approx(2.666667)
    assert combined["conflict_bars"] == 1
    assert combined["conflict_rate_pct"] == pytest.approx(33.333333)


def test_all_preregistered_gates_pass_only_with_parity_and_robustness():
    manifest = _manifest()
    champion = {
        "net_profit_pct": 100,
        "profit_factor": 1.5,
        "max_drawdown_pct": 10,
        "sharpe": 0.6,
        "positive_months": 6,
    }
    donchian = dict(manifest["donchian_4h_exploratory_parity"]["baseline"])
    ensemble = {
        "net_profit_pct": 120,
        "profit_factor": 1.6,
        "max_drawdown_pct": 8,
        "sharpe": 0.71,
        "positive_months": 9,
        "conflict_bars": 5,
        "conflict_rate_pct": 1.25,
    }
    folds = [
        {
            "champion": {"max_drawdown_pct": 5},
            "ensemble": {"net_profit_pct": 1, "max_drawdown_pct": 5},
        }
        for _ in range(5)
    ]
    recent = {"net_profit_pct": 1, "profit_factor": 1.1}
    sensitivity = {
        "40_60": {"net_profit_pct": 1, "max_drawdown_pct": 10},
        "60_40": {"net_profit_pct": 1, "max_drawdown_pct": 9},
    }

    result = evaluate_gate(
        champion,
        donchian,
        ensemble,
        folds,
        recent,
        sensitivity,
        member_correlation=0.6,
        history_days=2000,
        manifest=manifest,
    )
    assert result["passed"] is True

    drifted = copy.deepcopy(donchian)
    drifted["total_trades"] += 1
    rejected = evaluate_gate(
        champion,
        drifted,
        ensemble,
        folds,
        recent,
        sensitivity,
        member_correlation=0.6,
        history_days=2000,
        manifest=manifest,
    )
    assert rejected["passed"] is False
    assert rejected["checks"]["donchian_4h_exploratory_parity"] is False


def test_parity_gate_can_use_exploratory_full_history_semantics_separately():
    manifest = _manifest()
    parity = dict(manifest["donchian_4h_exploratory_parity"]["baseline"])
    locked_member_run = dict(parity)
    locked_member_run["total_trades"] -= 1
    champion = {
        "net_profit_pct": 100,
        "profit_factor": 1.5,
        "max_drawdown_pct": 10,
        "sharpe": 0.6,
        "positive_months": 6,
    }
    ensemble = {
        "net_profit_pct": 120,
        "profit_factor": 1.6,
        "max_drawdown_pct": 8,
        "sharpe": 0.71,
        "positive_months": 9,
        "conflict_bars": 0,
        "conflict_rate_pct": 0,
    }
    folds = [
        {
            "champion": {"max_drawdown_pct": 5},
            "ensemble": {"net_profit_pct": 1, "max_drawdown_pct": 5},
        }
        for _ in range(5)
    ]

    result = evaluate_gate(
        champion,
        locked_member_run,
        ensemble,
        folds,
        {"net_profit_pct": 1, "profit_factor": 1.1},
        {
            "40_60": {"net_profit_pct": 1, "max_drawdown_pct": 10},
            "60_40": {"net_profit_pct": 1, "max_drawdown_pct": 9},
        },
        member_correlation=0.6,
        history_days=2000,
        manifest=manifest,
        donchian_parity=parity,
    )

    assert result["passed"] is True
    assert result["checks"]["donchian_4h_exploratory_parity"] is True
    assert all(
        delta == 0
        for delta in result["observed"]["donchian_4h_parity_deltas"].values()
    )


def test_exploratory_parity_replay_keeps_strategy_native_warmup(monkeypatch):
    observed = {}

    def fake_replay(_frame, **kwargs):
        observed.update(kwargs)
        return {
            "trace": {
                "timestamps": [1],
                "equity_curve": [1000.0],
                "position_side_curve": [None],
            }
        }

    monkeypatch.setattr(
        "scripts.certify_btc_ensemble.run_plugin_replay", fake_replay
    )
    _run_arm(
        pd.DataFrame(),
        strategy_name="xauby_donchian_trend",
        profile={},
        config={},
        warmup_bars=None,
    )

    assert observed["min_bars_override"] is None


def test_exploratory_archives_are_hash_locked():
    for artifact in _manifest()["exploratory_evidence"]:
        data = (ROOT / artifact["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == artifact["sha256"]


def test_native_frame_hash_is_order_and_value_sensitive():
    frame = pd.DataFrame(
        [[1, 10, 11, 9, 10.5, 100], [2, 10.5, 12, 10, 11, 120]],
        columns=["open_time", "open", "high", "low", "close", "volume"],
    )
    original = frame_sha256(frame)
    assert original == frame_sha256(frame.copy())
    frame.loc[1, "close"] = 11.01
    assert frame_sha256(frame) != original


def test_workflow_provenance_fails_closed_when_required(monkeypatch):
    for name in (
        "GITHUB_ACTIONS",
        "GITHUB_REPOSITORY",
        "GITHUB_SHA",
        "GITHUB_WORKFLOW",
        "GITHUB_RUN_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="workflow provenance"):
        workflow_provenance(required=True)
