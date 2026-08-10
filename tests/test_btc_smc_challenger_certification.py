from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from scripts.btc_wfa_multi_strategy import STRATEGIES
from scripts.certify_btc_smc_challenger import (
    _economics,
    _preset,
    _require_locked_identity,
    evaluate_gate,
)
from xauby.observability.replay_validation import load_bot_config
from xauby.saas.catalog import preset_by_id


ROOT = Path(__file__).resolve().parents[1]


def _manifest() -> dict:
    path = ROOT / "docs/research/protocols/btc_smc_structure_challenger_v1.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _metrics(*, pf: float, net: float, drawdown: float, trades: int) -> dict:
    return {
        "profit_factor": pf,
        "net_profit_pct": net,
        "max_drawdown_pct": drawdown,
        "total_trades": trades,
    }


def test_manifest_locks_candidate_champion_costs_and_only_side_change() -> None:
    manifest = _manifest()
    config = load_bot_config(str(ROOT / "bot_config.yaml"))
    candidate = _preset(manifest["candidate_preset_id"])

    side_profile = _require_locked_identity(
        manifest,
        candidate,
        _preset(manifest["champion_preset_id"]),
        _economics(config),
    )

    assert candidate["execution_profile"]["allow_short"] is False
    assert side_profile["allow_short"] is True
    assert {
        key
        for key in side_profile
        if side_profile[key] != candidate["execution_profile"].get(key)
    } == {"allow_short"}


def test_locked_gate_requires_robust_distinct_alpha_and_side_noninferiority() -> None:
    champion = _metrics(pf=1.50, net=20.0, drawdown=10.0, trades=140)
    candidate = _metrics(pf=1.25, net=8.0, drawdown=8.0, trades=130)
    long_short = _metrics(pf=1.20, net=7.0, drawdown=9.5, trades=145)
    champion_folds = [
        _metrics(pf=pf, net=net, drawdown=3.0, trades=25)
        for pf, net in zip((1.2, 1.4, 0.8, 1.3, 1.2), (1, 2, -1, 1, 2))
    ]
    candidate_folds = [
        _metrics(pf=pf, net=net, drawdown=2.5, trades=24)
        for pf, net in zip((1.3, 0.8, 1.4, 1.2, 1.1), (2, -1, 2, 2, 1))
    ]
    side_folds = [
        _metrics(pf=pf, net=net, drawdown=2.8, trades=26)
        for pf, net in zip((1.2, 0.9, 1.3, 1.1, 1.0), (1.5, -0.5, 1.5, 1.0, 0.5))
    ]

    gate = evaluate_gate(
        candidate,
        champion,
        long_short,
        candidate_folds,
        champion_folds,
        side_folds,
        history_days=2400,
        manifest=_manifest(),
    )

    assert gate["passed"] is True
    assert gate["observed"]["candidate_positive_when_champion_nonpositive_folds"] == 1
    assert gate["observed"]["long_short_behavior_observed"] is True
    assert all(gate["checks"].values())


def test_latest_fold_failure_rejects_even_when_other_metrics_pass() -> None:
    champion = _metrics(pf=1.50, net=20.0, drawdown=10.0, trades=140)
    candidate = _metrics(pf=1.25, net=8.0, drawdown=8.0, trades=130)
    long_short = _metrics(pf=1.20, net=7.0, drawdown=9.5, trades=145)
    champion_folds = [
        _metrics(pf=1.2, net=net, drawdown=3.0, trades=25)
        for net in (1, 2, -1, 1, 2)
    ]
    candidate_folds = [
        _metrics(pf=1.2, net=net, drawdown=2.5, trades=24)
        for net in (2, -1, 2, 2, -0.1)
    ]
    side_folds = deepcopy(candidate_folds)
    side_folds[0]["total_trades"] += 1

    gate = evaluate_gate(
        candidate,
        champion,
        long_short,
        candidate_folds,
        champion_folds,
        side_folds,
        history_days=2400,
        manifest=_manifest(),
    )

    assert gate["passed"] is False
    assert gate["checks"]["latest_fold_net_positive"] is False


def test_catalog_candidate_is_research_only_and_unassessed() -> None:
    preset = preset_by_id("okx-btc-smc-structure-long-v1")

    assert preset["strategy"] == "xauby_smc_pro"
    assert preset["allowed_sides"] == ["long"]
    assert preset["execution_profile"]["allow_short"] is False
    assert preset["certification_status"] == "not_assessed"
    assert preset["backtest"]["status"] == "pending"
    assert preset["live_certified"] is False


def test_legacy_wfa_smc_grid_is_explicitly_reproducible_as_long_only() -> None:
    for _name, profile in STRATEGIES["xauby_smc_pro"]["grid"]:
        assert "allow_short" not in profile
