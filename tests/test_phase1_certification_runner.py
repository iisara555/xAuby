import hashlib
import json
import os

import pandas as pd
import pytest

from xauby.backtest.certification_runner import (
    Phase1CertificationError,
    run_phase1_certification,
    verify_certification_artifact,
)
from xauby.backtest.certification_v2 import TrialLedger, canonical_json
from xauby.backtest.walkforward import (
    NestedFoldResult,
    Window,
    WindowResult,
    nested_purged_plan_from_policy,
)

FOUR_HOURS_MS = 4 * 3_600_000
CANDIDATE = {"strategy": "candidate-a", "selection_procedure": "inner-only"}


def _frame(rows=64):
    start = int(pd.Timestamp("2022-01-01", tz="UTC").timestamp() * 1000)
    return pd.DataFrame(
        {
            "open_time": [start + index * FOUR_HOURS_MS for index in range(rows)],
            "open": [100.0 + index for index in range(rows)],
            "high": [101.0 + index for index in range(rows)],
            "low": [99.0 + index for index in range(rows)],
            "close": [100.5 + index for index in range(rows)],
            "volume": [10.0] * rows,
        }
    )


def _window(label, offset):
    return Window(label=label, start_ms=offset * 10, end_ms=offset * 10 + 9)


def _fold_results(plan, *, trades_per_fold=6):
    results = []
    for fold in plan.folds:
        inner_results = {
            candidate: [
                WindowResult(
                    window=_window(split.evaluation.label, split.evaluation.start),
                    stats={"net_profit_pct": score, "total_trades": 2},
                )
                for split in fold.inner
            ]
            for candidate, score in (("candidate-a", 1.0), ("candidate-b", 0.5))
        }
        results.append(
            NestedFoldResult(
                fold=fold,
                selected_candidate="candidate-a",
                inner_scores={"candidate-a": 2.0, "candidate-b": 1.0},
                inner_results=inner_results,
                outer_result=WindowResult(
                    window=_window(
                        fold.outer.evaluation.label,
                        fold.outer.evaluation.start,
                    ),
                    stats={
                        "net_profit_pct": 2.0,
                        "total_trades": trades_per_fold,
                    },
                ),
            )
        )
    return results


def _observations(count=12, *, native=True, gross_scale=1.0):
    pattern = [1.2, -0.3, 0.9, -0.2, 0.7, -0.1]
    return [
        {
            "venue": "okx",
            "symbol": "BTC-USDT-SWAP",
            "market_type": "swap",
            "native": native,
            "data_sha256": "c" * 64,
            "gross_return_pct": pattern[index % len(pattern)] * gross_scale,
            "holding_hours": 12.0,
            "side": "LONG" if index % 2 else "SHORT",
            "fill_ratio": 0.99,
            "latency_ms": 100.0 + index,
            "observed_slippage_bps": 2.5,
            "outer_fold_index": 0 if index < count / 2 else 1,
        }
        for index in range(count)
    ]


def _ledger(tmp_path, protocol):
    ledger = TrialLedger.create(tmp_path / "trials.jsonl", protocol)
    selected = ledger.start_trial(CANDIDATE)
    ledger.finish_trial(
        selected,
        status="completed",
        metrics={
            "sharpe": 0.8,
            "sharpe_basis": "adverse_execution_outer_holdout_returns_pct",
            "selection_p_value": 0.001,
        },
    )
    other = ledger.start_trial({"strategy": "candidate-b"})
    ledger.finish_trial(
        other,
        status="completed",
        metrics={
            "sharpe": 0.2,
            "sharpe_basis": "adverse_execution_outer_holdout_returns_pct",
            "selection_p_value": 0.4,
        },
    )
    return ledger, selected


def _provenance(**overrides):
    value = {
        "repository": "iisara555/xAuby",
        "git_commit": "d" * 40,
        "workflow": "institutional-certification-v2",
        "workflow_ref": ".github/workflows/institutional-certification-v2.yml@refs/heads/main",
        "workflow_run_id": "12345",
        "workflow_run_attempt": "1",
        "workflow_run_url": "https://github.com/iisara555/xAuby/actions/runs/12345",
        "source_tree_clean": True,
    }
    value.update(overrides)
    return value


def _inputs(tmp_path, certification_protocol_factory):
    protocol = certification_protocol_factory()
    plan = nested_purged_plan_from_policy(_frame(), protocol.validation_policy)
    ledger, selected = _ledger(tmp_path, protocol)
    return {
        "candidate": CANDIDATE,
        "selected_trial_id": selected,
        "ledger": ledger,
        "plan": plan,
        "fold_results": _fold_results(plan),
        "execution_observations": _observations(),
        "ci_provenance": _provenance(),
        "certified_at": "2026-08-20T12:00:00+07:00",
        "output_dir": tmp_path / "artifact",
    }


def test_unified_runner_certifies_and_writes_verifiable_create_once_bundle(
    tmp_path,
    certification_protocol_factory,
):
    inputs = _inputs(tmp_path, certification_protocol_factory)

    result = run_phase1_certification(**inputs)
    artifact = verify_certification_artifact(
        inputs["output_dir"],
        expected_sha256=result.artifact_sha256,
    )

    assert result.passed
    assert result.verdict == "certified"
    assert artifact["gate"]["passed"] is True
    assert artifact["execution"]["report"]["certification_scenario"] == "adverse"
    assert artifact["statistics"]["statistics"]["observations"] == 12
    assert artifact["ledger"]["trials_started"] == 2
    assert "does not authorize live" in artifact["scope"]
    assert oct(os.stat(result.artifact_path).st_mode & 0o777) == "0o444"
    assert oct(os.stat(inputs["output_dir"]).st_mode & 0o777) == "0o555"

    with pytest.raises(Phase1CertificationError, match="already exists"):
        run_phase1_certification(**inputs)


def test_runner_uses_execution_stressed_returns_for_statistics(
    tmp_path,
    certification_protocol_factory,
):
    inputs = _inputs(tmp_path, certification_protocol_factory)
    result = run_phase1_certification(**inputs)
    artifact = verify_certification_artifact(inputs["output_dir"])

    stressed = artifact["execution"]["report"]["certification_returns"]
    assert stressed == artifact["execution"]["report"]["statistics"]["scenarios"][
        "adverse"
    ]["net_returns_pct"]
    assert artifact["statistics"]["statistics"]["observations"] == len(stressed)
    assert result.checks["statistical_significance"]


def test_execution_failure_produces_failed_evidence_not_a_certificate_pass(
    tmp_path,
    certification_protocol_factory,
):
    inputs = _inputs(tmp_path, certification_protocol_factory)
    inputs["execution_observations"] = _observations(native=False)

    result = run_phase1_certification(**inputs)
    artifact = verify_certification_artifact(inputs["output_dir"])

    assert not result.passed
    assert result.verdict == "failed"
    assert not result.checks["execution_stress"]
    assert artifact["verdict"] == "failed"


def test_outer_trade_count_omission_cannot_self_certify(
    tmp_path,
    certification_protocol_factory,
):
    inputs = _inputs(tmp_path, certification_protocol_factory)
    inputs["fold_results"][0].outer_result.stats["total_trades"] = 5

    result = run_phase1_certification(**inputs)

    assert not result.passed
    assert not result.checks["execution_trade_count_parity"]


def test_outer_holdout_or_ci_provenance_drift_is_rejected_before_artifact(
    tmp_path,
    certification_protocol_factory,
):
    inputs = _inputs(tmp_path, certification_protocol_factory)
    inputs["fold_results"][0].outer_result.window = _window("invented", 1)
    with pytest.raises(Phase1CertificationError, match="locked holdout"):
        run_phase1_certification(**inputs)
    assert not inputs["output_dir"].exists()

    inputs = _inputs(tmp_path / "second", certification_protocol_factory)
    inputs["ci_provenance"] = _provenance(source_tree_clean=False)
    with pytest.raises(Phase1CertificationError, match="clean source tree"):
        run_phase1_certification(**inputs)
    assert not inputs["output_dir"].exists()


def test_external_digest_and_component_chain_detect_artifact_tampering(
    tmp_path,
    certification_protocol_factory,
):
    inputs = _inputs(tmp_path, certification_protocol_factory)
    result = run_phase1_certification(**inputs)
    certificate = result.artifact_path
    artifact = json.loads(certificate.read_text(encoding="utf-8"))
    artifact["execution"]["observations"][0]["gross_return_pct"] = 99.0
    tampered = (canonical_json(artifact) + "\n").encode("utf-8")
    certificate.chmod(0o644)
    certificate.write_bytes(tampered)
    certificate.chmod(0o444)
    digest = hashlib.sha256(tampered).hexdigest()
    sidecar = inputs["output_dir"] / "certificate.sha256"
    sidecar.chmod(0o644)
    sidecar.write_text(f"{digest}  certificate.json\n", encoding="ascii")
    sidecar.chmod(0o444)

    with pytest.raises(Phase1CertificationError, match="component hashes"):
        verify_certification_artifact(inputs["output_dir"])
    with pytest.raises(Phase1CertificationError, match="expected external"):
        verify_certification_artifact(
            inputs["output_dir"],
            expected_sha256=result.artifact_sha256,
        )


def test_ledger_tampering_is_detected_before_any_artifact(
    tmp_path,
    certification_protocol_factory,
):
    inputs = _inputs(tmp_path, certification_protocol_factory)
    path = inputs["ledger"].path
    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[1])
    record["candidate"]["strategy"] = "invented"
    lines[1] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(Exception, match="hash mismatch"):
        run_phase1_certification(**inputs)
    assert not inputs["output_dir"].exists()
