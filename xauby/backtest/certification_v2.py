"""Institutional certification protocol and append-only research trial ledger.

This module is deliberately upstream of a certification verdict.  It records
the experiment that was promised and every candidate that was attempted, so a
later statistical gate cannot be handed only the winning result or a fictional
``n_trials=1``.

The ledger is a hash-chained JSONL file.  Hash chaining detects modification,
reordering, and deletion from the middle.  A final certificate must also retain
the :attr:`TrialLedgerSnapshot.ledger_sha256` anchor to detect tail truncation.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any

try:  # Linux in production; the fallback still protects threads in one process.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX platforms
    fcntl = None  # type: ignore[assignment]


PROTOCOL_VERSION = 2
LEDGER_SCHEMA_VERSION = 1
TERMINAL_STATUSES = frozenset({"completed", "failed", "aborted"})
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_LOCAL_LOCKS: dict[str, threading.Lock] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()


class CertificationProtocolError(ValueError):
    """A protocol is incomplete, ambiguous, or not serializable."""


class TrialLedgerError(RuntimeError):
    """A trial ledger is missing, corrupt, or used out of sequence."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_json(value: Any, *, field_name: str) -> None:
    """Reject values that canonical JSON cannot represent honestly."""

    def walk(item: Any, location: str) -> None:
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise CertificationProtocolError(f"{location} must be finite")
            return
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise CertificationProtocolError(
                        f"{location} contains non-string key {key!r}"
                    )
                walk(child, f"{location}.{key}")
            return
        if isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                walk(child, f"{location}[{index}]")
            return
        raise CertificationProtocolError(
            f"{location} contains unsupported value {type(item).__name__}"
        )

    walk(value, field_name)


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json(child) for key, child in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(child) for child in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_json(child) for child in value]
    return value


def canonical_json(value: Any) -> str:
    """Return the strict canonical representation used by every digest."""
    _validate_json(value, field_name="payload")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _require_keys(name: str, value: Mapping[str, Any], required: Sequence[str]) -> None:
    missing = [key for key in required if key not in value]
    if missing:
        raise CertificationProtocolError(f"{name} missing required fields: {missing}")


def _require_text_fields(name: str, value: Mapping[str, Any], required: Sequence[str]) -> None:
    empty = [key for key in required if not str(value.get(key) or "").strip()]
    if empty:
        raise CertificationProtocolError(f"{name} fields must be non-empty: {empty}")


def _require_aware_timestamp(name: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise CertificationProtocolError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CertificationProtocolError(f"{name} must include a timezone")
    return parsed


@dataclass(frozen=True)
class CertificationProtocolV2:
    """Pre-registered contract for one research and certification campaign."""

    protocol_id: str
    hypothesis: str
    primary_metric: str
    selection_rule: str
    data_identity: Mapping[str, Any]
    validation_policy: Mapping[str, Any]
    execution_policy: Mapping[str, Any]
    statistical_policy: Mapping[str, Any]
    artifact_policy: Mapping[str, Any]
    random_seed: int
    created_at: str
    version: int = field(default=PROTOCOL_VERSION, init=False)

    def __post_init__(self) -> None:
        for name in ("protocol_id", "hypothesis", "primary_metric", "selection_rule"):
            if not str(getattr(self, name) or "").strip():
                raise CertificationProtocolError(f"{name} is required")
        if self.version != PROTOCOL_VERSION:
            raise CertificationProtocolError(
                f"protocol version must be {PROTOCOL_VERSION}, got {self.version}"
            )
        if isinstance(self.random_seed, bool) or not isinstance(self.random_seed, int):
            raise CertificationProtocolError("random_seed must be an integer")
        _require_aware_timestamp("created_at", self.created_at)

        blocks = {
            "data_identity": (
                self.data_identity,
                ("source", "venue", "symbol", "timeframe", "start", "end", "sha256"),
            ),
            "validation_policy": (
                self.validation_policy,
                (
                    "method",
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
                ),
            ),
            "execution_policy": (
                self.execution_policy,
                (
                    "fee_model",
                    "slippage_model",
                    "funding_model",
                    "latency_model",
                    "fill_model",
                    "venue",
                    "market_type",
                    "taker_fee_bps",
                    "baseline_slippage_bps",
                    "funding_rate_8h_bps",
                    "latency_bps_per_100ms",
                    "min_observations",
                    "min_native_coverage",
                    "min_observed_fill_ratio",
                    "max_latency_p95_ms",
                    "certification_scenario",
                    "scenarios",
                ),
            ),
            "statistical_policy": (
                self.statistical_policy,
                (
                    "primary_test",
                    "alpha",
                    "multiple_testing",
                    "sharpe_metric",
                    "sharpe_basis",
                    "selection_p_value_metric",
                    "min_observations",
                    "bootstrap_samples",
                    "bootstrap_block_size",
                    "min_bootstrap_p05_pct",
                    "min_probability_profitable",
                    "permutation_samples",
                    "max_permutation_p_value",
                    "benchmark_sharpe",
                    "min_probabilistic_sharpe",
                    "min_deflated_sharpe",
                ),
            ),
            "artifact_policy": (
                self.artifact_policy,
                (
                    "schema",
                    "hash_algorithm",
                    "require_ci",
                    "repository",
                ),
            ),
        }
        for name, (value, required) in blocks.items():
            if not isinstance(value, Mapping):
                raise CertificationProtocolError(f"{name} must be a mapping")
            _require_keys(name, value, required)
            _validate_json(value, field_name=name)

        _require_text_fields(
            "data_identity",
            self.data_identity,
            ("source", "venue", "symbol", "timeframe", "start", "end", "sha256"),
        )
        _require_text_fields("validation_policy", self.validation_policy, ("method",))
        if self.validation_policy["method"] != "nested_purged_walk_forward":
            raise CertificationProtocolError(
                "validation_policy.method must be nested_purged_walk_forward"
            )
        _require_text_fields(
            "execution_policy",
            self.execution_policy,
            (
                "fee_model",
                "slippage_model",
                "funding_model",
                "latency_model",
                "fill_model",
                "venue",
                "market_type",
                "certification_scenario",
            ),
        )
        supported_execution_models = {
            "fee_model": "venue_taker",
            "slippage_model": "observed_plus_stress",
            "funding_model": "adverse_venue_8h",
            "latency_model": "observed_p95_stress",
            "fill_model": "observed_ratio_stress",
        }
        for key, expected in supported_execution_models.items():
            if self.execution_policy[key] != expected:
                raise CertificationProtocolError(
                    f"execution_policy.{key} must be {expected}"
                )
        _require_text_fields(
            "artifact_policy",
            self.artifact_policy,
            ("schema", "hash_algorithm", "repository"),
        )
        if self.artifact_policy["schema"] != "institutional_certification_artifact_v2":
            raise CertificationProtocolError(
                "artifact_policy.schema must be institutional_certification_artifact_v2"
            )
        if self.artifact_policy["hash_algorithm"] != "sha256":
            raise CertificationProtocolError("artifact_policy.hash_algorithm must be sha256")
        if self.artifact_policy["require_ci"] is not True:
            raise CertificationProtocolError("artifact_policy.require_ci must be true")
        _require_text_fields(
            "statistical_policy",
            self.statistical_policy,
            (
                "primary_test",
                "multiple_testing",
                "sharpe_metric",
                "sharpe_basis",
                "selection_p_value_metric",
            ),
        )
        if self.statistical_policy["primary_test"] != "deflated_sharpe_ratio":
            raise CertificationProtocolError(
                "statistical_policy.primary_test must be deflated_sharpe_ratio"
            )
        if self.statistical_policy["multiple_testing"] not in {
            "bonferroni",
            "benjamini_hochberg",
        }:
            raise CertificationProtocolError(
                "statistical_policy.multiple_testing must be bonferroni or "
                "benjamini_hochberg"
            )

        data_hash = str(self.data_identity.get("sha256") or "")
        if not _SHA256_RE.fullmatch(data_hash):
            raise CertificationProtocolError("data_identity.sha256 must be 64 lowercase hex")
        data_start = _require_aware_timestamp("data_identity.start", self.data_identity["start"])
        data_end = _require_aware_timestamp("data_identity.end", self.data_identity["end"])
        if data_end <= data_start:
            raise CertificationProtocolError("data_identity.end must be after start")

        for key in (
            "outer_folds",
            "inner_folds",
            "outer_test_bars",
            "outer_step_bars",
            "inner_validation_bars",
            "min_train_bars",
            "min_inner_train_bars",
        ):
            value = self.validation_policy.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise CertificationProtocolError(f"validation_policy.{key} must be > 0")
        if self.validation_policy["outer_folds"] < 2:
            raise CertificationProtocolError("validation_policy.outer_folds must be >= 2")
        if self.validation_policy["inner_folds"] < 2:
            raise CertificationProtocolError("validation_policy.inner_folds must be >= 2")
        if self.validation_policy["outer_step_bars"] < self.validation_policy["outer_test_bars"]:
            raise CertificationProtocolError(
                "validation_policy.outer_step_bars must be >= outer_test_bars"
            )
        for key in ("purge_bars", "embargo_bars", "warmup_bars"):
            value = self.validation_policy.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CertificationProtocolError(f"validation_policy.{key} must be >= 0")

        alpha = self.statistical_policy.get("alpha")
        if isinstance(alpha, bool) or not isinstance(alpha, (int, float)):
            raise CertificationProtocolError("statistical_policy.alpha must be numeric")
        if not 0.0 < float(alpha) < 1.0:
            raise CertificationProtocolError("statistical_policy.alpha must be between 0 and 1")
        for key, minimum in (
            ("min_observations", 5),
            ("bootstrap_samples", 100),
            ("bootstrap_block_size", 2),
            ("permutation_samples", 100),
        ):
            value = self.statistical_policy.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise CertificationProtocolError(
                    f"statistical_policy.{key} must be an integer >= {minimum}"
                )
        for key in (
            "min_probability_profitable",
            "max_permutation_p_value",
            "min_probabilistic_sharpe",
            "min_deflated_sharpe",
        ):
            value = self.statistical_policy.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CertificationProtocolError(f"statistical_policy.{key} must be numeric")
            if not 0.0 <= float(value) <= 1.0:
                raise CertificationProtocolError(
                    f"statistical_policy.{key} must be between 0 and 1"
                )
        for key in ("min_bootstrap_p05_pct", "benchmark_sharpe"):
            value = self.statistical_policy.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CertificationProtocolError(f"statistical_policy.{key} must be numeric")
            if not math.isfinite(float(value)):
                raise CertificationProtocolError(f"statistical_policy.{key} must be finite")

        for key in (
            "taker_fee_bps",
            "baseline_slippage_bps",
            "funding_rate_8h_bps",
            "latency_bps_per_100ms",
            "max_latency_p95_ms",
        ):
            value = self.execution_policy.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CertificationProtocolError(f"execution_policy.{key} must be numeric")
            if not math.isfinite(float(value)) or float(value) < 0.0:
                raise CertificationProtocolError(f"execution_policy.{key} must be finite and >= 0")
        execution_min_observations = self.execution_policy.get("min_observations")
        if (
            isinstance(execution_min_observations, bool)
            or not isinstance(execution_min_observations, int)
            or execution_min_observations < 5
        ):
            raise CertificationProtocolError(
                "execution_policy.min_observations must be an integer >= 5"
            )
        for key in ("min_native_coverage", "min_observed_fill_ratio"):
            value = self.execution_policy.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CertificationProtocolError(f"execution_policy.{key} must be numeric")
            if not 0.0 <= float(value) <= 1.0:
                raise CertificationProtocolError(
                    f"execution_policy.{key} must be between 0 and 1"
                )
        if float(self.execution_policy["min_native_coverage"]) != 1.0:
            raise CertificationProtocolError(
                "execution_policy.min_native_coverage must be 1.0"
            )
        scenarios = self.execution_policy.get("scenarios")
        if not isinstance(scenarios, (list, tuple)) or len(scenarios) < 3:
            raise CertificationProtocolError(
                "execution_policy.scenarios must contain at least three stress scenarios"
            )
        scenario_names: list[str] = []
        required_scenario_fields = (
            "name",
            "fee_multiplier",
            "slippage_multiplier",
            "funding_multiplier",
            "latency_multiplier",
            "fill_ratio_multiplier",
            "min_compounded_return_pct",
            "max_drawdown_pct",
            "max_cost_to_gross_profit",
        )
        for index, scenario in enumerate(scenarios):
            if not isinstance(scenario, Mapping):
                raise CertificationProtocolError(
                    f"execution_policy.scenarios[{index}] must be a mapping"
                )
            _require_keys(
                f"execution_policy.scenarios[{index}]",
                scenario,
                required_scenario_fields,
            )
            name = str(scenario.get("name") or "").strip()
            if not name:
                raise CertificationProtocolError(
                    f"execution_policy.scenarios[{index}].name is required"
                )
            scenario_names.append(name)
            for key in required_scenario_fields[1:]:
                value = scenario.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise CertificationProtocolError(
                        f"execution_policy.scenarios[{index}].{key} must be numeric"
                    )
                if not math.isfinite(float(value)):
                    raise CertificationProtocolError(
                        f"execution_policy.scenarios[{index}].{key} must be finite"
                    )
            for key in required_scenario_fields[1:6]:
                if float(scenario[key]) < 0.0:
                    raise CertificationProtocolError(
                        f"execution_policy.scenarios[{index}].{key} must be >= 0"
                    )
            if not 0.0 <= float(scenario["fill_ratio_multiplier"]) <= 1.0:
                raise CertificationProtocolError(
                    "execution_policy.scenarios"
                    f"[{index}].fill_ratio_multiplier must be between 0 and 1"
                )
            if float(scenario["min_compounded_return_pct"]) <= -100.0:
                raise CertificationProtocolError(
                    "execution_policy.scenarios"
                    f"[{index}].min_compounded_return_pct must be > -100"
                )
            if not 0.0 <= float(scenario["max_drawdown_pct"]) <= 100.0:
                raise CertificationProtocolError(
                    f"execution_policy.scenarios[{index}].max_drawdown_pct must be "
                    "between 0 and 100"
                )
            if float(scenario["max_cost_to_gross_profit"]) < 0.0:
                raise CertificationProtocolError(
                    "execution_policy.scenarios"
                    f"[{index}].max_cost_to_gross_profit must be >= 0"
                )
        if len(set(scenario_names)) != len(scenario_names):
            raise CertificationProtocolError("execution_policy scenario names must be unique")
        if not {"baseline", "adverse", "severe"}.issubset(scenario_names):
            raise CertificationProtocolError(
                "execution_policy scenarios must include baseline, adverse, and severe"
            )
        by_name = {str(scenario["name"]): scenario for scenario in scenarios}
        for key in (
            "fee_multiplier",
            "slippage_multiplier",
            "funding_multiplier",
            "latency_multiplier",
        ):
            if not (
                float(by_name["baseline"][key])
                <= float(by_name["adverse"][key])
                <= float(by_name["severe"][key])
            ):
                raise CertificationProtocolError(
                    f"execution_policy scenario {key} must increase with severity"
                )
        if not (
            float(by_name["baseline"]["fill_ratio_multiplier"])
            >= float(by_name["adverse"]["fill_ratio_multiplier"])
            >= float(by_name["severe"]["fill_ratio_multiplier"])
        ):
            raise CertificationProtocolError(
                "execution_policy scenario fill ratios must decrease with severity"
            )
        if self.execution_policy["certification_scenario"] not in scenario_names:
            raise CertificationProtocolError(
                "execution_policy.certification_scenario is not present in scenarios"
            )
        if self.execution_policy["certification_scenario"] != "adverse":
            raise CertificationProtocolError(
                "execution_policy.certification_scenario must be adverse"
            )
        if str(self.execution_policy["venue"]).lower() != str(
            self.data_identity["venue"]
        ).lower():
            raise CertificationProtocolError(
                "execution_policy.venue must match data_identity.venue"
            )
        expected_sharpe_basis = (
            f"{self.execution_policy['certification_scenario']}"
            "_execution_outer_holdout_returns_pct"
        )
        if self.statistical_policy["sharpe_basis"] != expected_sharpe_basis:
            raise CertificationProtocolError(
                "statistical_policy.sharpe_basis must match the locked execution "
                "certification scenario"
            )
        if (
            self.statistical_policy["min_observations"]
            != self.execution_policy["min_observations"]
        ):
            raise CertificationProtocolError(
                "statistical and execution min_observations must match"
            )

        # Frozen dataclasses do not freeze mutable mappings.  Recursively freeze
        # them so neither caller mutation nor direct attribute access can alter
        # the pre-registered protocol after its fingerprint is issued.
        for name in blocks:
            object.__setattr__(self, name, _freeze_json(deepcopy(dict(getattr(self, name)))))

        # Validate the complete payload, including scalar fields.
        canonical_json(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "protocol_id": self.protocol_id,
            "hypothesis": self.hypothesis,
            "primary_metric": self.primary_metric,
            "selection_rule": self.selection_rule,
            "data_identity": _thaw_json(self.data_identity),
            "validation_policy": _thaw_json(self.validation_policy),
            "execution_policy": _thaw_json(self.execution_policy),
            "statistical_policy": _thaw_json(self.statistical_policy),
            "artifact_policy": _thaw_json(self.artifact_policy),
            "random_seed": self.random_seed,
            "created_at": self.created_at,
        }

    @property
    def fingerprint(self) -> str:
        return sha256_digest(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CertificationProtocolV2:
        if value.get("version") != PROTOCOL_VERSION:
            raise CertificationProtocolError(
                f"unsupported protocol version {value.get('version')!r}"
            )
        try:
            return cls(
                protocol_id=value["protocol_id"],
                hypothesis=value["hypothesis"],
                primary_metric=value["primary_metric"],
                selection_rule=value["selection_rule"],
                data_identity=value["data_identity"],
                validation_policy=value["validation_policy"],
                execution_policy=value["execution_policy"],
                statistical_policy=value["statistical_policy"],
                artifact_policy=value["artifact_policy"],
                random_seed=value["random_seed"],
                created_at=value["created_at"],
            )
        except KeyError as exc:
            raise CertificationProtocolError(f"protocol missing {exc.args[0]}") from exc


@dataclass(frozen=True)
class TrialLedgerSnapshot:
    protocol: CertificationProtocolV2
    protocol_sha256: str
    ledger_sha256: str
    trials_started: int
    trials_completed: int
    trials_failed: int
    trials_aborted: int
    trials_pending: int
    records: tuple[dict[str, Any], ...]

    def metric_values(self, metric: str) -> list[float]:
        values: list[float] = []
        for record in self.records:
            if record.get("kind") != "trial_finished" or record.get("status") != "completed":
                continue
            value = (record.get("metrics") or {}).get(metric)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if math.isfinite(float(value)):
                values.append(float(value))
        return values

    def evidence_for_candidate(
        self,
        candidate: Mapping[str, Any],
        *,
        selected_trial_id: str | None = None,
    ) -> dict[str, Any]:
        """Resolve candidate evidence from this exact immutable snapshot."""
        candidate_hash = sha256_digest(dict(candidate))
        starts = [
            record
            for record in self.records
            if record.get("kind") == "trial_started"
            and record.get("candidate_sha256") == candidate_hash
        ]
        finishes = {
            record["trial_id"]: record
            for record in self.records
            if record.get("kind") == "trial_finished"
        }
        completed = [
            record
            for record in starts
            if finishes.get(record["trial_id"], {}).get("status") == "completed"
            and isinstance(finishes[record["trial_id"]].get("metrics"), Mapping)
            and self.protocol.primary_metric in finishes[record["trial_id"]]["metrics"]
        ]
        if selected_trial_id is not None:
            completed = [
                record for record in completed if record["trial_id"] == selected_trial_id
            ]
        if not completed:
            qualifier = (
                f" selected trial {selected_trial_id!r}" if selected_trial_id is not None else ""
            )
            raise TrialLedgerError(
                "candidate has no completed trial"
                f"{qualifier} with the protocol primary metric in this ledger"
            )
        return {
            "protocol_sha256": self.protocol_sha256,
            "ledger_sha256": self.ledger_sha256,
            "candidate_sha256": candidate_hash,
            "selected_trial_ids": [record["trial_id"] for record in completed],
            "n_trials": self.trials_started,
            "n_completed": self.trials_completed,
            "n_failed": self.trials_failed,
            "n_aborted": self.trials_aborted,
            "n_pending": self.trials_pending,
        }


class TrialLedger:
    """Append-only, hash-chained record of every attempted research trial."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._lock = self._local_lock(self.path)

    @staticmethod
    def _local_lock(path: Path) -> threading.Lock:
        key = str(path.resolve())
        with _LOCAL_LOCKS_GUARD:
            return _LOCAL_LOCKS.setdefault(key, threading.Lock())

    @classmethod
    def create(
        cls,
        path: str | os.PathLike[str],
        protocol: CertificationProtocolV2,
    ) -> TrialLedger:
        ledger = cls(path)
        ledger.path.parent.mkdir(parents=True, exist_ok=True)
        header = {
            "kind": "protocol",
            "ledger_schema_version": LEDGER_SCHEMA_VERSION,
            "protocol": protocol.to_dict(),
            "protocol_sha256": protocol.fingerprint,
            "created_at": _utc_now(),
            "previous_hash": None,
        }
        header["record_hash"] = sha256_digest(header)
        payload = canonical_json(header) + "\n"
        try:
            fd = os.open(ledger.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise TrialLedgerError(f"ledger already exists: {ledger.path}") from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                ledger.path.unlink()
            except OSError:
                pass
            raise
        ledger.verify(expected_protocol=protocol)
        return ledger

    def start_trial(
        self,
        candidate: Mapping[str, Any],
        *,
        started_at: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        if not isinstance(candidate, Mapping) or not candidate:
            raise TrialLedgerError("candidate must be a non-empty mapping")
        _validate_json(candidate, field_name="candidate")
        _validate_json(metadata or {}, field_name="metadata")
        with self._lock:
            with self._locked_file() as handle:
                snapshot = self._verify_handle(handle)
                sequence = snapshot.trials_started + 1
                candidate_copy = deepcopy(dict(candidate))
                candidate_hash = sha256_digest(candidate_copy)
                trial_id = f"{sequence:06d}-{candidate_hash[:16]}"
                record = {
                    "kind": "trial_started",
                    "sequence": sequence,
                    "trial_id": trial_id,
                    "candidate": candidate_copy,
                    "candidate_sha256": candidate_hash,
                    "started_at": started_at or _utc_now(),
                    "metadata": deepcopy(dict(metadata or {})),
                    "previous_hash": snapshot.records[-1]["record_hash"],
                }
                self._append_locked(handle, record)
                return trial_id

    def finish_trial(
        self,
        trial_id: str,
        *,
        status: str,
        metrics: Mapping[str, Any] | None = None,
        error: str | None = None,
        finished_at: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if status not in TERMINAL_STATUSES:
            raise TrialLedgerError(f"status must be one of {sorted(TERMINAL_STATUSES)}")
        if status == "completed" and (not isinstance(metrics, Mapping) or not metrics):
            raise TrialLedgerError("completed trial requires metrics")
        if status != "completed" and not str(error or "").strip():
            raise TrialLedgerError(f"{status} trial requires an error or reason")
        _validate_json(metrics or {}, field_name="metrics")
        _validate_json(metadata or {}, field_name="metadata")
        with self._lock:
            with self._locked_file() as handle:
                snapshot = self._verify_handle(handle)
                starts = {
                    record["trial_id"]: record
                    for record in snapshot.records
                    if record.get("kind") == "trial_started"
                }
                finishes = {
                    record["trial_id"]
                    for record in snapshot.records
                    if record.get("kind") == "trial_finished"
                }
                if trial_id not in starts:
                    raise TrialLedgerError(f"unknown trial_id {trial_id!r}")
                if trial_id in finishes:
                    raise TrialLedgerError(f"trial {trial_id!r} is already finished")
                record = {
                    "kind": "trial_finished",
                    "trial_id": trial_id,
                    "status": status,
                    "metrics": deepcopy(dict(metrics or {})),
                    "error": str(error or ""),
                    "finished_at": finished_at or _utc_now(),
                    "metadata": deepcopy(dict(metadata or {})),
                    "previous_hash": snapshot.records[-1]["record_hash"],
                }
                self._append_locked(handle, record)

    def verify(
        self,
        *,
        expected_protocol: CertificationProtocolV2 | None = None,
    ) -> TrialLedgerSnapshot:
        with self._lock:
            with self._locked_file() as handle:
                snapshot = self._verify_handle(handle)
        if expected_protocol is not None and snapshot.protocol_sha256 != expected_protocol.fingerprint:
            raise TrialLedgerError(
                "ledger protocol does not match the expected pre-registered protocol"
            )
        return snapshot

    def evidence_for_candidate(
        self,
        candidate: Mapping[str, Any],
        *,
        selected_trial_id: str | None = None,
    ) -> dict[str, Any]:
        """Build evidence that cannot omit the unsuccessful search attempts."""
        snapshot = self.verify()
        return snapshot.evidence_for_candidate(
            candidate,
            selected_trial_id=selected_trial_id,
        )

    def _locked_file(self):
        if not self.path.is_file():
            raise TrialLedgerError(f"ledger does not exist: {self.path}")
        handle = self.path.open("r+", encoding="utf-8")
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return _LockedHandle(handle)

    @staticmethod
    def _append_locked(handle, record: dict[str, Any]) -> None:
        record["record_hash"] = sha256_digest(record)
        line = canonical_json(record) + "\n"
        handle.seek(0, os.SEEK_END)
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())

    def _verify_handle(self, handle) -> TrialLedgerSnapshot:
        handle.seek(0)
        raw = handle.read()
        if not raw:
            raise TrialLedgerError("ledger is empty")
        if not raw.endswith("\n"):
            raise TrialLedgerError("ledger has a partial trailing record")
        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(raw.splitlines(), start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TrialLedgerError(f"ledger line {line_number} is not valid JSON") from exc
            if not isinstance(record, dict):
                raise TrialLedgerError(f"ledger line {line_number} must be an object")
            records.append(record)
        return self._validate_records(records, raw.encode("utf-8"))

    @staticmethod
    def _validate_records(records: list[dict[str, Any]], raw: bytes) -> TrialLedgerSnapshot:
        header = records[0]
        if header.get("kind") != "protocol":
            raise TrialLedgerError("first ledger record must be the protocol")
        if header.get("ledger_schema_version") != LEDGER_SCHEMA_VERSION:
            raise TrialLedgerError("unsupported ledger schema version")
        if header.get("previous_hash") is not None:
            raise TrialLedgerError("protocol record cannot have a previous hash")

        previous_hash: str | None = None
        starts: dict[str, dict[str, Any]] = {}
        finishes: dict[str, dict[str, Any]] = {}
        for index, record in enumerate(records):
            actual_hash = record.get("record_hash")
            unhashed = dict(record)
            unhashed.pop("record_hash", None)
            expected_hash = sha256_digest(unhashed)
            if actual_hash != expected_hash:
                raise TrialLedgerError(f"ledger record {index + 1} hash mismatch")
            if record.get("previous_hash") != previous_hash:
                raise TrialLedgerError(f"ledger record {index + 1} breaks the hash chain")
            previous_hash = actual_hash

            kind = record.get("kind")
            if index == 0:
                continue
            if kind == "protocol":
                raise TrialLedgerError("ledger contains more than one protocol")
            if kind == "trial_started":
                trial_id = str(record.get("trial_id") or "")
                expected_sequence = len(starts) + 1
                if record.get("sequence") != expected_sequence:
                    raise TrialLedgerError("trial sequence is not contiguous")
                if not trial_id or trial_id in starts:
                    raise TrialLedgerError("duplicate or missing trial_id")
                candidate = record.get("candidate")
                if not isinstance(candidate, Mapping) or not candidate:
                    raise TrialLedgerError(f"trial {trial_id!r} has no candidate")
                if record.get("candidate_sha256") != sha256_digest(candidate):
                    raise TrialLedgerError(f"trial {trial_id!r} candidate hash mismatch")
                starts[trial_id] = record
            elif kind == "trial_finished":
                trial_id = str(record.get("trial_id") or "")
                if trial_id not in starts:
                    raise TrialLedgerError(f"finished unknown trial {trial_id!r}")
                if trial_id in finishes:
                    raise TrialLedgerError(f"trial {trial_id!r} has multiple outcomes")
                status = record.get("status")
                if status not in TERMINAL_STATUSES:
                    raise TrialLedgerError(f"trial {trial_id!r} has invalid status")
                if status == "completed" and (
                    not isinstance(record.get("metrics"), Mapping) or not record["metrics"]
                ):
                    raise TrialLedgerError(f"completed trial {trial_id!r} has no metrics")
                if status != "completed" and not str(record.get("error") or "").strip():
                    raise TrialLedgerError(f"{status} trial {trial_id!r} has no reason")
                finishes[trial_id] = record
            else:
                raise TrialLedgerError(f"unknown ledger record kind {kind!r}")

        try:
            protocol = CertificationProtocolV2.from_dict(header["protocol"])
        except (KeyError, CertificationProtocolError) as exc:
            raise TrialLedgerError(f"invalid ledger protocol: {exc}") from exc
        if header.get("protocol_sha256") != protocol.fingerprint:
            raise TrialLedgerError("protocol hash does not match its contents")

        counts = {status: 0 for status in TERMINAL_STATUSES}
        for outcome in finishes.values():
            counts[outcome["status"]] += 1
        return TrialLedgerSnapshot(
            protocol=protocol,
            protocol_sha256=protocol.fingerprint,
            ledger_sha256=hashlib.sha256(raw).hexdigest(),
            trials_started=len(starts),
            trials_completed=counts["completed"],
            trials_failed=counts["failed"],
            trials_aborted=counts["aborted"],
            trials_pending=len(starts) - len(finishes),
            records=tuple(deepcopy(records)),
        )


class _LockedHandle:
    """Small context wrapper that reliably releases an advisory file lock."""

    def __init__(self, handle):
        self.handle = handle

    def __enter__(self):
        return self.handle

    def __exit__(self, exc_type, exc, traceback) -> None:
        if fcntl is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
