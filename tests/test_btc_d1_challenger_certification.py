from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from scripts.certify_btc_d1_challenger import (
    _economics,
    _preset,
    _require_locked_identity,
    evaluate_gate,
)
from xauby.observability.replay_validation import load_bot_config


def _manifest() -> dict:
    path = (
        Path(__file__).resolve().parents[1]
        / "docs/research/protocols/btc_long_d1_challenger_v1.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _metrics(*, pf: float, net: float, drawdown: float, trades: int) -> dict:
    return {
        "profit_factor": pf,
        "net_profit_pct": net,
        "max_drawdown_pct": drawdown,
        "total_trades": trades,
    }


def test_manifest_fingerprints_and_execution_model_match_the_repo() -> None:
    manifest = _manifest()
    config = load_bot_config(
        str(Path(__file__).resolve().parents[1] / "bot_config.yaml")
    )

    _require_locked_identity(
        manifest,
        _preset(manifest["candidate_preset_id"]),
        _preset(manifest["champion_preset_id"]),
        _economics(config),
    )


def test_locked_gate_passes_only_with_absolute_and_comparative_evidence() -> None:
    champion = _metrics(pf=1.50, net=18.0, drawdown=10.0, trades=130)
    challenger = _metrics(pf=1.60, net=16.0, drawdown=9.0, trades=100)
    champion_folds = [
        _metrics(pf=value, net=1.0, drawdown=3.0, trades=20)
        for value in (1.2, 1.3, 1.1, 1.4, 1.2)
    ]
    challenger_folds = [
        _metrics(pf=value, net=net, drawdown=3.0, trades=18)
        for value, net in zip((1.3, 1.4, 1.2, 1.5, 0.9), (2, 2, 2, 2, -1))
    ]

    gate = evaluate_gate(
        challenger,
        champion,
        challenger_folds,
        champion_folds,
        history_days=2400,
        manifest=_manifest(),
    )

    assert gate["passed"] is True
    assert gate["observed"]["profitable_folds"] == 4
    assert gate["observed"]["candidate_vs_champion_fold_profit_factor_wins"] == 4
    assert all(gate["checks"].values())


def test_one_failed_locked_criterion_rejects_the_challenger() -> None:
    champion = _metrics(pf=1.50, net=18.0, drawdown=10.0, trades=130)
    challenger = _metrics(pf=1.60, net=16.0, drawdown=12.1, trades=100)
    folds = [_metrics(pf=1.3, net=2.0, drawdown=3.0, trades=18)] * 5

    gate = evaluate_gate(
        challenger,
        champion,
        deepcopy(folds),
        deepcopy(folds),
        history_days=2400,
        manifest=_manifest(),
    )

    assert gate["passed"] is False
    assert gate["checks"]["candidate_full_drawdown"] is False
