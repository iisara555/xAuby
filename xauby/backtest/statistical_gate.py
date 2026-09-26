"""Fail-closed statistical certification gate backed by a verified trial ledger.

Callers provide the selected candidate's untouched outer-holdout return series,
but they cannot provide ``n_trials`` or Sharpe dispersion.  Those values are
derived from one verified :class:`~xauby.backtest.certification_v2.TrialLedgerSnapshot`,
which closes the most consequential escape hatch in the legacy certificate
scripts: reporting a searched winner as though it were one experiment.
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from xauby.backtest.certification_v2 import TrialLedger, TrialLedgerSnapshot
from xauby.backtest.significance import (
    OrderInvariantStatistic,
    benjamini_hochberg,
    bonferroni,
    bootstrap_returns,
    deflated_sharpe_ratio,
    max_drawdown,
    shuffle_pvalue,
)


class StatisticalGateError(ValueError):
    """The gate input is malformed and cannot produce an honest rejection."""


@dataclass(frozen=True)
class StatisticalGateReport:
    passed: bool
    checks: Mapping[str, bool]
    failures: tuple[str, ...]
    provenance: Mapping[str, Any]
    statistics: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": dict(self.checks),
            "failures": list(self.failures),
            "provenance": deepcopy(dict(self.provenance)),
            "statistics": deepcopy(dict(self.statistics)),
        }


def _finite_returns(returns: Sequence[float]) -> list[float]:
    values: list[float] = []
    for index, value in enumerate(returns):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise StatisticalGateError(f"return {index} must be numeric")
        number = float(value)
        if not math.isfinite(number):
            raise StatisticalGateError(f"return {index} must be finite")
        if number <= -100.0:
            raise StatisticalGateError(
                f"return {index} is <= -100%; compounded equity cannot recover"
            )
        values.append(number)
    return values


def _completed_metrics(snapshot: TrialLedgerSnapshot) -> list[tuple[str, Mapping[str, Any]]]:
    completed: list[tuple[str, Mapping[str, Any]]] = []
    for record in snapshot.records:
        if record.get("kind") != "trial_finished" or record.get("status") != "completed":
            continue
        metrics = record.get("metrics")
        if not isinstance(metrics, Mapping):
            raise StatisticalGateError(
                f"completed trial {record.get('trial_id')!r} has no metrics"
            )
        completed.append((str(record["trial_id"]), metrics))
    return completed


def evaluate_statistical_gate(
    returns: Sequence[float],
    *,
    candidate: Mapping[str, Any],
    selected_trial_id: str,
    ledger: TrialLedger,
) -> StatisticalGateReport:
    """Evaluate every pre-registered statistical gate from one ledger snapshot.

    ``returns`` must come only from nested outer holdouts and use the exact
    periodicity named by ``statistical_policy.sharpe_basis``.  The later unified
    certification runner is responsible for constructing that series; this
    layer verifies its mathematics and search provenance.
    """
    if not str(selected_trial_id or "").strip():
        raise StatisticalGateError("selected_trial_id is required")
    if not isinstance(candidate, Mapping) or not candidate:
        raise StatisticalGateError("candidate must be a non-empty mapping")
    values = _finite_returns(returns)
    snapshot = ledger.verify()
    evidence = snapshot.evidence_for_candidate(
        candidate,
        selected_trial_id=selected_trial_id,
    )
    policy = snapshot.protocol.statistical_policy
    completed = _completed_metrics(snapshot)

    checks: dict[str, bool] = {}
    failures: list[str] = []

    def record(name: str, passed: bool, failure: str) -> None:
        checks[name] = bool(passed)
        if not passed:
            failures.append(failure)

    min_observations = int(policy["min_observations"])
    record(
        "minimum_observations",
        len(values) >= min_observations,
        f"only {len(values)} returns; protocol requires {min_observations}",
    )
    record(
        "ledger_has_no_pending_trials",
        snapshot.trials_pending == 0,
        f"ledger has {snapshot.trials_pending} pending trial(s)",
    )
    record(
        "selected_trial_is_explicit",
        evidence["selected_trial_ids"] == [selected_trial_id],
        "selected candidate does not resolve to exactly the named completed trial",
    )

    sharpe_metric = str(policy["sharpe_metric"])
    sharpe_basis = str(policy["sharpe_basis"])
    p_value_metric = str(policy["selection_p_value_metric"])
    sharpe_values: list[float] = []
    p_values: list[float] = []
    trial_ids: list[str] = []
    family_errors: list[str] = []
    for trial_id, metrics in completed:
        sharpe = metrics.get(sharpe_metric)
        p_value = metrics.get(p_value_metric)
        basis = metrics.get("sharpe_basis")
        if isinstance(sharpe, bool) or not isinstance(sharpe, (int, float)):
            family_errors.append(f"{trial_id}: missing numeric {sharpe_metric}")
            continue
        if not math.isfinite(float(sharpe)):
            family_errors.append(f"{trial_id}: non-finite {sharpe_metric}")
            continue
        if basis != sharpe_basis:
            family_errors.append(
                f"{trial_id}: sharpe_basis {basis!r} does not match {sharpe_basis!r}"
            )
            continue
        if isinstance(p_value, bool) or not isinstance(p_value, (int, float)):
            family_errors.append(f"{trial_id}: missing numeric {p_value_metric}")
            continue
        p_number = float(p_value)
        if not math.isfinite(p_number) or not 0.0 <= p_number <= 1.0:
            family_errors.append(f"{trial_id}: invalid {p_value_metric}")
            continue
        trial_ids.append(trial_id)
        sharpe_values.append(float(sharpe))
        p_values.append(p_number)

    family_complete = not family_errors and len(sharpe_values) == snapshot.trials_completed
    enough_dispersion = snapshot.trials_started == 1 or len(sharpe_values) >= 2
    record(
        "trial_metric_family_complete",
        family_complete,
        "; ".join(family_errors) or "completed trial metric family is incomplete",
    )
    record(
        "sharpe_dispersion_observable",
        enough_dispersion,
        "multiple attempted trials require at least two completed comparable Sharpes",
    )

    stats: dict[str, Any] = {
        "return_basis": sharpe_basis,
        "observations": len(values),
        "trial_family": {
            "trial_ids": trial_ids,
            "n_trials": snapshot.trials_started,
            "n_completed": snapshot.trials_completed,
            "n_failed": snapshot.trials_failed,
            "n_aborted": snapshot.trials_aborted,
            "n_pending": snapshot.trials_pending,
            "sharpe_metric": sharpe_metric,
            "sharpe_values": sharpe_values,
            "selection_p_value_metric": p_value_metric,
            "selection_p_values": p_values,
            "errors": family_errors,
        },
    }

    enough_returns = checks["minimum_observations"]
    if enough_returns:
        bootstrap = bootstrap_returns(
            values,
            samples=int(policy["bootstrap_samples"]),
            seed=snapshot.protocol.random_seed,
            block=int(policy["bootstrap_block_size"]),
            min_observations=min_observations,
        )
        stats["block_bootstrap"] = bootstrap.to_dict()
        record(
            "bootstrap_lower_bound",
            bootstrap.samples > 0
            and bootstrap.p05_pct >= float(policy["min_bootstrap_p05_pct"]),
            f"bootstrap p05 {bootstrap.p05_pct} is below protocol minimum "
            f"{policy['min_bootstrap_p05_pct']}",
        )
        record(
            "bootstrap_probability_profitable",
            bootstrap.samples > 0
            and bootstrap.prob_profitable >= float(policy["min_probability_profitable"]),
            f"bootstrap probability profitable {bootstrap.prob_profitable} is below "
            f"{policy['min_probability_profitable']}",
        )
    else:
        stats["block_bootstrap"] = {"computed": False, "reason": "insufficient returns"}
        record("bootstrap_lower_bound", False, "bootstrap not computed")
        record("bootstrap_probability_profitable", False, "bootstrap not computed")

    if enough_returns and family_complete and enough_dispersion:
        sharpe_variance = statistics.pvariance(sharpe_values) if len(sharpe_values) > 1 else 0.0
        dsr = deflated_sharpe_ratio(
            values,
            n_trials=snapshot.trials_started,
            sharpe_variance=sharpe_variance,
            benchmark=float(policy["benchmark_sharpe"]),
        )
        stats["deflated_sharpe"] = {
            **dsr.to_dict(),
            "sharpe_variance_from_ledger": sharpe_variance,
        }
        sharpe_is_identifiable = dsr.note != "zero variance"
        record(
            "probabilistic_sharpe",
            sharpe_is_identifiable
            and math.isfinite(dsr.probabilistic_sharpe)
            and dsr.probabilistic_sharpe >= float(policy["min_probabilistic_sharpe"]),
            "PSR is not identifiable for a zero-variance return series"
            if not sharpe_is_identifiable
            else f"PSR {dsr.probabilistic_sharpe} is below "
            f"{policy['min_probabilistic_sharpe']}",
        )
        record(
            "deflated_sharpe",
            sharpe_is_identifiable
            and math.isfinite(dsr.deflated_sharpe)
            and dsr.deflated_sharpe >= float(policy["min_deflated_sharpe"]),
            "DSR is not identifiable for a zero-variance return series"
            if not sharpe_is_identifiable
            else f"DSR {dsr.deflated_sharpe} is below {policy['min_deflated_sharpe']}",
        )
    else:
        stats["deflated_sharpe"] = {
            "computed": False,
            "reason": "insufficient returns or incomplete comparable trial family",
        }
        record("probabilistic_sharpe", False, "PSR not computed")
        record("deflated_sharpe", False, "DSR not computed")

    if enough_returns:
        try:
            permutation = shuffle_pvalue(
                values,
                max_drawdown,
                samples=int(policy["permutation_samples"]),
                seed=snapshot.protocol.random_seed,
                lower_is_better=True,
            )
        except OrderInvariantStatistic as exc:
            stats["drawdown_permutation"] = {"computed": False, "reason": str(exc)}
            record("drawdown_permutation", False, str(exc))
        else:
            stats["drawdown_permutation"] = permutation.to_dict()
            record(
                "drawdown_permutation",
                permutation.p_value <= float(policy["max_permutation_p_value"]),
                f"drawdown permutation p-value {permutation.p_value} exceeds "
                f"{policy['max_permutation_p_value']}",
            )
    else:
        stats["drawdown_permutation"] = {
            "computed": False,
            "reason": "insufficient returns",
        }
        record("drawdown_permutation", False, "drawdown permutation not computed")

    if family_complete and p_values and selected_trial_id in trial_ids:
        method = str(policy["multiple_testing"])
        correction = (
            bonferroni(p_values, alpha=float(policy["alpha"]))
            if method == "bonferroni"
            else benjamini_hochberg(p_values, alpha=float(policy["alpha"]))
        )
        selected_index = trial_ids.index(selected_trial_id)
        stats["multiple_testing"] = {
            **correction.to_dict(),
            "trial_ids": trial_ids,
            "selected_trial_id": selected_trial_id,
            "selected_rejected": correction.rejected[selected_index],
        }
        record(
            "multiple_testing_selected",
            correction.rejected[selected_index],
            f"selected trial did not survive {method} correction",
        )
    else:
        stats["multiple_testing"] = {
            "computed": False,
            "reason": "incomplete p-value family or selected trial missing",
        }
        record("multiple_testing_selected", False, "multiple-testing correction not computed")

    provenance = {
        **evidence,
        "selected_trial_id": selected_trial_id,
        "protocol_id": snapshot.protocol.protocol_id,
        "statistical_policy": deepcopy(dict(policy)),
    }
    return StatisticalGateReport(
        passed=bool(checks) and all(checks.values()),
        checks=checks,
        failures=tuple(failures),
        provenance=provenance,
        statistics=stats,
    )
