"""Venue-locked execution and cost stress gates for certification v2.

Every observation is linked to the protocol's native data identity.  The gate
re-prices the untouched outer-holdout return stream under every pre-registered
scenario and exposes the designated adverse stream to the statistical gate.
This prevents a certificate from testing significance on optimistic returns
while presenting a separate, unaudited execution-cost paragraph.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from xauby.backtest.certification_v2 import CertificationProtocolV2
from xauby.backtest.significance import compound, max_drawdown


class ExecutionStressError(ValueError):
    """Execution evidence is malformed and cannot produce an honest verdict."""


@dataclass(frozen=True)
class ExecutionStressReport:
    passed: bool
    checks: Mapping[str, bool]
    failures: tuple[str, ...]
    certification_scenario: str
    certification_returns: tuple[float, ...]
    statistics: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": dict(self.checks),
            "failures": list(self.failures),
            "certification_scenario": self.certification_scenario,
            "certification_returns": list(self.certification_returns),
            "statistics": deepcopy(dict(self.statistics)),
        }


def _number(value: Any, *, field_name: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExecutionStressError(f"{field_name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ExecutionStressError(f"{field_name} must be finite")
    if minimum is not None and result < minimum:
        raise ExecutionStressError(f"{field_name} must be >= {minimum}")
    return result


def _percentile95(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def _normalize_observations(
    observations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    required = (
        "venue",
        "symbol",
        "market_type",
        "native",
        "data_sha256",
        "gross_return_pct",
        "holding_hours",
        "side",
        "fill_ratio",
        "latency_ms",
        "observed_slippage_bps",
        "outer_fold_index",
    )
    for index, observation in enumerate(observations):
        if not isinstance(observation, Mapping):
            raise ExecutionStressError(f"observation {index} must be a mapping")
        missing = [key for key in required if key not in observation]
        if missing:
            raise ExecutionStressError(f"observation {index} missing fields: {missing}")
        if not isinstance(observation["native"], bool):
            raise ExecutionStressError(f"observation {index}.native must be boolean")
        side = str(observation["side"] or "").upper()
        if side not in {"LONG", "SHORT"}:
            raise ExecutionStressError(
                f"observation {index}.side must be LONG or SHORT"
            )
        gross_return = _number(
            observation["gross_return_pct"],
            field_name=f"observation {index}.gross_return_pct",
        )
        if gross_return <= -100.0:
            raise ExecutionStressError(
                f"observation {index}.gross_return_pct must be > -100"
            )
        fill_ratio = _number(
            observation["fill_ratio"],
            field_name=f"observation {index}.fill_ratio",
            minimum=0.0,
        )
        if fill_ratio > 1.0:
            raise ExecutionStressError(f"observation {index}.fill_ratio must be <= 1")
        fold_index = observation["outer_fold_index"]
        if isinstance(fold_index, bool) or not isinstance(fold_index, int) or fold_index < 0:
            raise ExecutionStressError(
                f"observation {index}.outer_fold_index must be an integer >= 0"
            )
        normalized.append(
            {
                "venue": str(observation["venue"] or "").lower(),
                "symbol": str(observation["symbol"] or "").upper(),
                "market_type": str(observation["market_type"] or "").lower(),
                "native": observation["native"],
                "data_sha256": str(observation["data_sha256"] or ""),
                "gross_return_pct": gross_return,
                "holding_hours": _number(
                    observation["holding_hours"],
                    field_name=f"observation {index}.holding_hours",
                    minimum=0.0,
                ),
                "side": side,
                "fill_ratio": fill_ratio,
                "latency_ms": _number(
                    observation["latency_ms"],
                    field_name=f"observation {index}.latency_ms",
                    minimum=0.0,
                ),
                "observed_slippage_bps": _number(
                    observation["observed_slippage_bps"],
                    field_name=f"observation {index}.observed_slippage_bps",
                    minimum=0.0,
                ),
                "outer_fold_index": fold_index,
            }
        )
    return normalized


def evaluate_execution_stress(
    observations: Sequence[Mapping[str, Any]],
    *,
    protocol: CertificationProtocolV2,
) -> ExecutionStressReport:
    """Re-price the locked outer-holdout stream under all execution scenarios."""
    if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes)):
        raise ExecutionStressError("observations must be a sequence")
    rows = _normalize_observations(observations)
    policy = protocol.execution_policy
    data = protocol.data_identity

    checks: dict[str, bool] = {}
    failures: list[str] = []

    def record(name: str, passed: bool, failure: str) -> None:
        checks[name] = bool(passed)
        if not passed:
            failures.append(failure)

    record(
        "minimum_observations",
        len(rows) >= int(policy["min_observations"]),
        f"only {len(rows)} observations; protocol requires {policy['min_observations']}",
    )
    observed_folds = {row["outer_fold_index"] for row in rows}
    expected_folds = set(range(int(protocol.validation_policy["outer_folds"])))
    record(
        "outer_fold_coverage",
        observed_folds == expected_folds,
        f"execution observations cover folds {sorted(observed_folds)}, expected "
        f"{sorted(expected_folds)}",
    )
    expected_identity = (
        str(policy["venue"]).lower(),
        str(data["symbol"]).upper(),
        str(policy["market_type"]).lower(),
        str(data["sha256"]),
    )
    identity_matches = [
        (
            row["venue"],
            row["symbol"],
            row["market_type"],
            row["data_sha256"],
        )
        == expected_identity
        for row in rows
    ]
    record(
        "venue_data_identity",
        bool(rows) and all(identity_matches),
        "one or more observations do not match the locked venue/data identity",
    )
    native_count = sum(
        row["native"] and matched
        for row, matched in zip(rows, identity_matches, strict=True)
    )
    native_coverage = native_count / len(rows) if rows else 0.0
    record(
        "native_coverage",
        native_coverage >= float(policy["min_native_coverage"]),
        f"native coverage {native_coverage:.4f} is below {policy['min_native_coverage']}",
    )
    min_fill = min((row["fill_ratio"] for row in rows), default=0.0)
    record(
        "observed_fill_ratio",
        min_fill >= float(policy["min_observed_fill_ratio"]),
        f"minimum observed fill ratio {min_fill:.4f} is below "
        f"{policy['min_observed_fill_ratio']}",
    )
    latency_p95 = _percentile95([row["latency_ms"] for row in rows])
    record(
        "latency_p95",
        bool(rows) and latency_p95 <= float(policy["max_latency_p95_ms"]),
        f"latency p95 {latency_p95:.2f}ms exceeds {policy['max_latency_p95_ms']}ms",
    )

    scenario_results: dict[str, Any] = {}
    for scenario in policy["scenarios"]:
        name = str(scenario["name"])
        net_returns: list[float] = []
        total_cost_pct = 0.0
        gross_profit_pct = 0.0
        component_totals = {
            "fee_pct": 0.0,
            "slippage_pct": 0.0,
            "funding_pct": 0.0,
            "latency_pct": 0.0,
        }
        stressed_fills: list[float] = []
        for row in rows:
            fill = min(
                1.0,
                row["fill_ratio"] * float(scenario["fill_ratio_multiplier"]),
            )
            stressed_fills.append(fill)
            fee_pct = (
                2.0
                * float(policy["taker_fee_bps"])
                * float(scenario["fee_multiplier"])
                / 100.0
                * fill
            )
            slippage_per_fill = max(
                float(policy["baseline_slippage_bps"]),
                row["observed_slippage_bps"],
            )
            slippage_pct = (
                2.0
                * slippage_per_fill
                * float(scenario["slippage_multiplier"])
                / 100.0
                * fill
            )
            funding_pct = (
                float(policy["funding_rate_8h_bps"])
                * (row["holding_hours"] / 8.0)
                * float(scenario["funding_multiplier"])
                / 100.0
                * fill
            )
            latency_pct = (
                (row["latency_ms"] / 100.0)
                * float(policy["latency_bps_per_100ms"])
                * float(scenario["latency_multiplier"])
                / 100.0
                * fill
            )
            costs = fee_pct + slippage_pct + funding_pct + latency_pct
            gross = row["gross_return_pct"] * fill
            net_returns.append(gross - costs)
            total_cost_pct += costs
            gross_profit_pct += max(0.0, gross)
            component_totals["fee_pct"] += fee_pct
            component_totals["slippage_pct"] += slippage_pct
            component_totals["funding_pct"] += funding_pct
            component_totals["latency_pct"] += latency_pct

        compounded = compound(net_returns)
        drawdown = max_drawdown(net_returns)
        cost_ratio = (
            total_cost_pct / gross_profit_pct if gross_profit_pct > 0.0 else None
        )
        scenario_checks = {
            "returns_recoverable": all(value > -100.0 for value in net_returns),
            "compounded_return": compounded
            >= float(scenario["min_compounded_return_pct"]),
            "max_drawdown": drawdown <= float(scenario["max_drawdown_pct"]),
            "cost_to_gross_profit": cost_ratio is not None
            and cost_ratio <= float(scenario["max_cost_to_gross_profit"]),
        }
        scenario_results[name] = {
            "passed": all(scenario_checks.values()),
            "checks": scenario_checks,
            "net_returns_pct": net_returns,
            "compounded_return_pct": compounded,
            "max_drawdown_pct": drawdown,
            "total_cost_pct": total_cost_pct,
            "gross_profit_pct": gross_profit_pct,
            "cost_to_gross_profit": cost_ratio,
            "minimum_stressed_fill_ratio": min(stressed_fills, default=0.0),
            "cost_components": component_totals,
            "thresholds": {
                "min_compounded_return_pct": float(
                    scenario["min_compounded_return_pct"]
                ),
                "max_drawdown_pct": float(scenario["max_drawdown_pct"]),
                "max_cost_to_gross_profit": float(
                    scenario["max_cost_to_gross_profit"]
                ),
            },
        }
        record(
            f"scenario:{name}",
            scenario_results[name]["passed"],
            f"execution scenario {name!r} failed one or more locked thresholds",
        )

    certification_scenario = str(policy["certification_scenario"])
    certification = scenario_results[certification_scenario]
    statistics = {
        "observations": len(rows),
        "expected_identity": {
            "venue": expected_identity[0],
            "symbol": expected_identity[1],
            "market_type": expected_identity[2],
            "data_sha256": expected_identity[3],
        },
        "native_coverage": native_coverage,
        "minimum_observed_fill_ratio": min_fill,
        "latency_p95_ms": latency_p95,
        "outer_fold_counts": {
            str(fold): sum(row["outer_fold_index"] == fold for row in rows)
            for fold in sorted(observed_folds)
        },
        "scenarios": scenario_results,
    }
    return ExecutionStressReport(
        passed=bool(checks) and all(checks.values()),
        checks=checks,
        failures=tuple(failures),
        certification_scenario=certification_scenario,
        certification_returns=tuple(certification["net_returns_pct"]),
        statistics=statistics,
    )
