"""Single fail-closed runner for Institutional Certification Framework v2."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from xauby.backtest.certification_v2 import (
    TrialLedger,
    canonical_json,
    sha256_digest,
)
from xauby.backtest.execution_stress import evaluate_execution_stress
from xauby.backtest.statistical_gate import evaluate_statistical_gate
from xauby.backtest.walkforward import NestedFoldResult, NestedPurgedPlan, WindowResult

ARTIFACT_FILENAME = "certificate.json"
DIGEST_FILENAME = "certificate.sha256"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")


class Phase1CertificationError(RuntimeError):
    """Certification inputs or artifact integrity are structurally invalid."""


@dataclass(frozen=True)
class Phase1CertificationResult:
    passed: bool
    verdict: str
    artifact_path: Path
    artifact_sha256: str
    checks: Mapping[str, bool]


def _aware_timestamp(name: str, value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise Phase1CertificationError(f"{name} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise Phase1CertificationError(f"{name} must include a timezone")
    return parsed.isoformat()


def _validate_provenance(
    provenance: Mapping[str, Any],
    *,
    expected_repository: str,
) -> dict[str, Any]:
    if not isinstance(provenance, Mapping):
        raise Phase1CertificationError("CI provenance must be a mapping")
    required = (
        "repository",
        "git_commit",
        "workflow",
        "workflow_ref",
        "workflow_run_id",
        "workflow_run_attempt",
        "workflow_run_url",
        "source_tree_clean",
    )
    missing = [key for key in required if key not in provenance]
    if missing:
        raise Phase1CertificationError(f"CI provenance missing fields: {missing}")
    normalized = deepcopy(dict(provenance))
    if normalized["repository"] != expected_repository:
        raise Phase1CertificationError("CI repository does not match artifact policy")
    if not _COMMIT_RE.fullmatch(str(normalized["git_commit"])):
        raise Phase1CertificationError("CI git_commit must be 40 lowercase hex")
    for key in (
        "workflow",
        "workflow_ref",
        "workflow_run_id",
        "workflow_run_attempt",
        "workflow_run_url",
    ):
        if not str(normalized[key] or "").strip():
            raise Phase1CertificationError(f"CI provenance {key} is required")
    if normalized["source_tree_clean"] is not True:
        raise Phase1CertificationError("CI provenance requires a clean source tree")
    return normalized


def _window_result(result: WindowResult) -> dict[str, Any]:
    payload = {
        "window": {
            "label": result.window.label,
            "start_ms": result.window.start_ms,
            "end_ms": result.window.end_ms,
        },
        "phase": result.phase,
        "stats": deepcopy(dict(result.stats)),
    }
    canonical_json(payload)
    return payload


def _serialize_fold_results(
    fold_results: Sequence[NestedFoldResult],
    *,
    plan: NestedPurgedPlan,
) -> list[dict[str, Any]]:
    if len(fold_results) != len(plan.folds):
        raise Phase1CertificationError("outer fold result count does not match locked plan")
    serialized: list[dict[str, Any]] = []
    for index, (result, locked_fold) in enumerate(
        zip(fold_results, plan.folds, strict=True)
    ):
        if not isinstance(result, NestedFoldResult):
            raise Phase1CertificationError(f"fold result {index} has the wrong type")
        if result.fold.to_dict() != locked_fold.to_dict():
            raise Phase1CertificationError(f"fold result {index} changed locked boundaries")
        selected = str(result.selected_candidate or "").strip()
        if not selected:
            raise Phase1CertificationError(f"fold result {index} has no selected candidate")
        if set(result.inner_scores) != set(result.inner_results):
            raise Phase1CertificationError(
                f"fold result {index} score and inner-result families differ"
            )
        if selected not in result.inner_scores:
            raise Phase1CertificationError(
                f"fold result {index} selected candidate was not scored on inner folds"
            )
        for candidate_id, score in result.inner_scores.items():
            if not math.isfinite(float(score)):
                raise Phase1CertificationError(
                    f"fold result {index} candidate {candidate_id!r} has non-finite score"
                )
            inner = result.inner_results[candidate_id]
            expected_labels = [split.evaluation.label for split in locked_fold.inner]
            actual_labels = [item.window.label for item in inner]
            if actual_labels != expected_labels:
                raise Phase1CertificationError(
                    f"fold result {index} candidate {candidate_id!r} inner windows changed"
                )
        if result.outer_result.window.label != locked_fold.outer.evaluation.label:
            raise Phase1CertificationError(
                f"fold result {index} outer result is not the locked holdout"
            )
        serialized.append(
            {
                "index": index,
                "boundaries": locked_fold.to_dict(),
                "selected_candidate": selected,
                "inner_scores": {
                    key: float(value) for key, value in sorted(result.inner_scores.items())
                },
                "inner_results": {
                    key: [_window_result(item) for item in result.inner_results[key]]
                    for key in sorted(result.inner_results)
                },
                "outer_result": _window_result(result.outer_result),
            }
        )
    canonical_json(serialized)
    return serialized


def _trade_count_parity(
    fold_results: Sequence[Mapping[str, Any]],
    observations: Sequence[Mapping[str, Any]],
) -> tuple[bool, dict[str, Any]]:
    observed: dict[int, int] = {}
    for observation in observations:
        fold = observation.get("outer_fold_index")
        if isinstance(fold, bool) or not isinstance(fold, int):
            return False, {"error": "outer_fold_index is not an integer"}
        observed[fold] = observed.get(fold, 0) + 1
    expected: dict[int, int] = {}
    errors: list[str] = []
    for fold in fold_results:
        index = int(fold["index"])
        trades = (fold["outer_result"].get("stats") or {}).get("total_trades")
        if isinstance(trades, bool) or not isinstance(trades, int) or trades < 0:
            errors.append(f"outer fold {index} has no valid total_trades")
            continue
        expected[index] = trades
    return not errors and expected == observed, {
        "expected_outer_trade_counts": {
            str(key): value for key, value in sorted(expected.items())
        },
        "observed_execution_counts": {
            str(key): value for key, value in sorted(observed.items())
        },
        "errors": errors,
    }


def _write_exclusive(path: Path, payload: bytes, *, mode: int = 0o444) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise Phase1CertificationError(f"artifact already exists: {path}") from exc
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(mode)


def _write_bundle(output_dir: Path, artifact: Mapping[str, Any]) -> tuple[Path, str]:
    try:
        output_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    except FileExistsError as exc:
        raise Phase1CertificationError(
            f"artifact bundle already exists: {output_dir}"
        ) from exc
    certificate_path = output_dir / ARTIFACT_FILENAME
    payload = (canonical_json(artifact) + "\n").encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    _write_exclusive(certificate_path, payload)
    sidecar = f"{digest}  {ARTIFACT_FILENAME}\n".encode("ascii")
    _write_exclusive(output_dir / DIGEST_FILENAME, sidecar)
    output_dir.chmod(0o555)
    return certificate_path, digest


def run_phase1_certification(
    *,
    candidate: Mapping[str, Any],
    selected_trial_id: str,
    ledger: TrialLedger,
    plan: NestedPurgedPlan,
    fold_results: Sequence[NestedFoldResult],
    execution_observations: Sequence[Mapping[str, Any]],
    ci_provenance: Mapping[str, Any],
    certified_at: str,
    output_dir: str | os.PathLike[str],
) -> Phase1CertificationResult:
    """Evaluate all Phase 1 gates and write one create-once certificate bundle."""
    snapshot_before = ledger.verify()
    protocol = snapshot_before.protocol
    provenance = _validate_provenance(
        ci_provenance,
        expected_repository=str(protocol.artifact_policy["repository"]),
    )
    timestamp = _aware_timestamp("certified_at", certified_at)
    candidate_payload = deepcopy(dict(candidate))
    observations_payload = [deepcopy(dict(item)) for item in execution_observations]
    canonical_json(candidate_payload)
    canonical_json(observations_payload)
    plan.validate()
    locked_plan = {
        key: protocol.validation_policy[key]
        for key in (
            "outer_folds",
            "inner_folds",
            "outer_test_bars",
            "outer_step_bars",
            "inner_validation_bars",
            "min_train_bars",
            "min_inner_train_bars",
            "purge_bars",
            "embargo_bars",
            "warmup_bars",
        )
    }
    actual_plan = {key: getattr(plan, key) for key in locked_plan}
    if actual_plan != locked_plan:
        raise Phase1CertificationError("walk-forward plan does not match protocol policy")
    serialized_folds = _serialize_fold_results(fold_results, plan=plan)

    execution = evaluate_execution_stress(
        observations_payload,
        protocol=protocol,
    )
    statistical = evaluate_statistical_gate(
        execution.certification_returns,
        candidate=candidate_payload,
        selected_trial_id=selected_trial_id,
        ledger=ledger,
    )
    snapshot_after = ledger.verify(expected_protocol=protocol)
    ledger_unchanged = snapshot_after.ledger_sha256 == snapshot_before.ledger_sha256
    trade_parity, trade_parity_evidence = _trade_count_parity(
        serialized_folds,
        observations_payload,
    )
    checks = {
        "nested_purged_validation": True,
        "execution_trade_count_parity": trade_parity,
        "execution_stress": execution.passed,
        "statistical_significance": statistical.passed,
        "ledger_closed": snapshot_after.trials_pending == 0,
        "ledger_unchanged_during_run": ledger_unchanged,
        "ci_provenance": True,
    }
    passed = all(checks.values())
    verdict = "certified" if passed else "failed"

    execution_payload = execution.to_dict()
    statistical_payload = statistical.to_dict()
    components = {
        "protocol_sha256": protocol.fingerprint,
        "ledger_sha256": snapshot_after.ledger_sha256,
        "validation_plan_sha256": plan.fingerprint,
        "fold_results_sha256": sha256_digest(serialized_folds),
        "execution_observations_sha256": sha256_digest(observations_payload),
        "execution_report_sha256": sha256_digest(execution_payload),
        "statistical_report_sha256": sha256_digest(statistical_payload),
        "ci_provenance_sha256": sha256_digest(provenance),
    }
    artifact = {
        "schema": protocol.artifact_policy["schema"],
        "schema_version": 2,
        "verdict": verdict,
        "certified_at": timestamp,
        "protocol": protocol.to_dict(),
        "ledger": {
            "protocol_sha256": snapshot_after.protocol_sha256,
            "ledger_sha256": snapshot_after.ledger_sha256,
            "trials_started": snapshot_after.trials_started,
            "trials_completed": snapshot_after.trials_completed,
            "trials_failed": snapshot_after.trials_failed,
            "trials_aborted": snapshot_after.trials_aborted,
            "trials_pending": snapshot_after.trials_pending,
        },
        "candidate": {
            "definition": candidate_payload,
            "selected_trial_id": selected_trial_id,
        },
        "validation": {
            "plan": plan.to_dict(),
            "fold_results": serialized_folds,
        },
        "execution": {
            "observations": observations_payload,
            "report": execution_payload,
            "trade_count_parity": trade_parity_evidence,
        },
        "statistics": statistical_payload,
        "ci_provenance": provenance,
        "gate": {"passed": passed, "checks": checks},
        "component_sha256": components,
        "scope": (
            "Research certification only; this artifact does not authorize live "
            "deployment, capital allocation, or an engine restart."
        ),
    }
    canonical_json(artifact)
    artifact_path, artifact_sha256 = _write_bundle(Path(output_dir), artifact)
    return Phase1CertificationResult(
        passed=passed,
        verdict=verdict,
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        checks=checks,
    )


def verify_certification_artifact(
    bundle_dir: str | os.PathLike[str],
    *,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Verify the external digest and every internal component hash."""
    root = Path(bundle_dir)
    certificate_path = root / ARTIFACT_FILENAME
    digest_path = root / DIGEST_FILENAME
    if not certificate_path.is_file() or not digest_path.is_file():
        raise Phase1CertificationError("artifact bundle is incomplete")
    payload = certificate_path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    sidecar = digest_path.read_text(encoding="ascii").strip().split()
    if len(sidecar) != 2 or sidecar[1] != ARTIFACT_FILENAME:
        raise Phase1CertificationError("artifact digest sidecar is malformed")
    if not _SHA256_RE.fullmatch(sidecar[0]) or sidecar[0] != digest:
        raise Phase1CertificationError("artifact SHA-256 mismatch")
    if expected_sha256 is not None and digest != expected_sha256:
        raise Phase1CertificationError("artifact does not match expected external SHA-256")
    try:
        artifact = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise Phase1CertificationError("artifact JSON is invalid") from exc
    if payload != (canonical_json(artifact) + "\n").encode("utf-8"):
        raise Phase1CertificationError("artifact is not in canonical form")
    if artifact.get("schema") != "institutional_certification_artifact_v2":
        raise Phase1CertificationError("artifact schema is unsupported")
    components = artifact.get("component_sha256") or {}
    calculated = {
        "protocol_sha256": sha256_digest(artifact["protocol"]),
        "ledger_sha256": artifact["ledger"]["ledger_sha256"],
        "validation_plan_sha256": sha256_digest(artifact["validation"]["plan"]),
        "fold_results_sha256": sha256_digest(artifact["validation"]["fold_results"]),
        "execution_observations_sha256": sha256_digest(
            artifact["execution"]["observations"]
        ),
        "execution_report_sha256": sha256_digest(artifact["execution"]["report"]),
        "statistical_report_sha256": sha256_digest(artifact["statistics"]),
        "ci_provenance_sha256": sha256_digest(artifact["ci_provenance"]),
    }
    if components != calculated:
        raise Phase1CertificationError("one or more artifact component hashes mismatch")
    if bool(artifact["gate"]["passed"]) != (artifact["verdict"] == "certified"):
        raise Phase1CertificationError("artifact verdict and gate disagree")
    return artifact
