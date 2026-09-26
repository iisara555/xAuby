import inspect
import json
import statistics

import pytest

from xauby.backtest.certification_v2 import (
    CertificationProtocolV2,
    TrialLedger,
    TrialLedgerError,
)
from xauby.backtest.statistical_gate import (
    StatisticalGateError,
    evaluate_statistical_gate,
)

RETURNS = tuple([1.0, -0.35, 0.8, -0.2, 0.6, -0.1] * 5)


def _protocol(**statistical_overrides):
    statistical_policy = {
        "primary_test": "deflated_sharpe_ratio",
        "alpha": 0.05,
        "multiple_testing": "bonferroni",
        "sharpe_metric": "sharpe",
        "sharpe_basis": "adverse_execution_outer_holdout_returns_pct",
        "selection_p_value_metric": "selection_p_value",
        "min_observations": 10,
        "bootstrap_samples": 100,
        "bootstrap_block_size": 3,
        "min_bootstrap_p05_pct": -100.0,
        "min_probability_profitable": 0.0,
        "permutation_samples": 100,
        "max_permutation_p_value": 1.0,
        "benchmark_sharpe": 0.0,
        "min_probabilistic_sharpe": 0.0,
        "min_deflated_sharpe": 0.0,
    }
    statistical_policy.update(statistical_overrides)
    return CertificationProtocolV2(
        protocol_id="statistical-gate-test-v2",
        hypothesis="Selected candidate survives search-aware statistical gates.",
        primary_metric="sharpe",
        selection_rule="Highest inner-selected candidate on untouched outer holdouts.",
        data_identity={
            "source": "locked-fixture",
            "venue": "okx",
            "symbol": "BTC-USDT-SWAP",
            "timeframe": "4h",
            "start": "2021-01-01T00:00:00Z",
            "end": "2026-01-01T00:00:00Z",
            "sha256": "b" * 64,
        },
        validation_policy={
            "method": "nested_purged_walk_forward",
            "outer_folds": 2,
            "inner_folds": 2,
            "outer_test_bars": 30,
            "outer_step_bars": 30,
            "inner_validation_bars": 10,
            "min_train_bars": 100,
            "min_inner_train_bars": 50,
            "purge_bars": 4,
            "embargo_bars": 4,
            "warmup_bars": 20,
        },
        execution_policy={
            "fee_model": "venue_taker",
            "slippage_model": "observed_plus_stress",
            "funding_model": "adverse_venue_8h",
            "latency_model": "observed_p95_stress",
            "fill_model": "observed_ratio_stress",
            "venue": "okx",
            "market_type": "swap",
            "taker_fee_bps": 5.0,
            "baseline_slippage_bps": 2.0,
            "funding_rate_8h_bps": 1.0,
            "latency_bps_per_100ms": 0.1,
            "min_observations": 10,
            "min_native_coverage": 1.0,
            "min_observed_fill_ratio": 0.95,
            "max_latency_p95_ms": 500.0,
            "certification_scenario": "adverse",
            "scenarios": [
                {
                    "name": name,
                    "fee_multiplier": multiplier,
                    "slippage_multiplier": multiplier,
                    "funding_multiplier": multiplier,
                    "latency_multiplier": multiplier,
                    "fill_ratio_multiplier": fill,
                    "min_compounded_return_pct": -99.0,
                    "max_drawdown_pct": 100.0,
                    "max_cost_to_gross_profit": 100.0,
                }
                for name, multiplier, fill in (
                    ("baseline", 1.0, 1.0),
                    ("adverse", 1.5, 0.95),
                    ("severe", 2.0, 0.85),
                )
            ],
        },
        statistical_policy=statistical_policy,
        artifact_policy={
            "schema": "institutional_certification_artifact_v2",
            "hash_algorithm": "sha256",
            "require_ci": True,
            "repository": "iisara555/xAuby",
        },
        random_seed=20260820,
        created_at="2026-08-20T00:00:00+00:00",
    )


def _complete(
    ledger,
    candidate,
    *,
    sharpe,
    p_value,
    basis="adverse_execution_outer_holdout_returns_pct",
):
    trial_id = ledger.start_trial(candidate)
    ledger.finish_trial(
        trial_id,
        status="completed",
        metrics={
            "sharpe": sharpe,
            "sharpe_basis": basis,
            "selection_p_value": p_value,
        },
    )
    return trial_id


def test_gate_derives_trial_count_and_sharpe_dispersion_from_ledger(tmp_path):
    ledger = TrialLedger.create(tmp_path / "trials.jsonl", _protocol())
    selected = {"lookback": 20}
    selected_trial = _complete(ledger, selected, sharpe=0.9, p_value=0.001)
    _complete(ledger, {"lookback": 30}, sharpe=0.4, p_value=0.3)
    failed = ledger.start_trial({"lookback": 40})
    aborted = ledger.start_trial({"lookback": 50})
    ledger.finish_trial(failed, status="failed", error="insufficient outer trades")
    ledger.finish_trial(aborted, status="aborted", error="research budget reached")

    report = evaluate_statistical_gate(
        RETURNS,
        candidate=selected,
        selected_trial_id=selected_trial,
        ledger=ledger,
    )

    family = report.statistics["trial_family"]
    deflated = report.statistics["deflated_sharpe"]
    assert report.passed
    assert family["n_trials"] == 4
    assert family["n_failed"] == 1
    assert family["n_aborted"] == 1
    assert deflated["n_trials"] == 4
    assert deflated["sharpe_variance_from_ledger"] == pytest.approx(
        statistics.pvariance([0.9, 0.4])
    )
    assert report.statistics["block_bootstrap"]["note"] == "block bootstrap, block=3"
    assert report.provenance["selected_trial_id"] == selected_trial


def test_gate_api_has_no_caller_controlled_trial_count_or_sharpe_variance():
    parameters = inspect.signature(evaluate_statistical_gate).parameters

    assert "n_trials" not in parameters
    assert "sharpe_variance" not in parameters


def test_gate_fails_closed_while_any_trial_is_pending(tmp_path):
    ledger = TrialLedger.create(tmp_path / "trials.jsonl", _protocol())
    candidate = {"lookback": 20}
    selected_trial = _complete(ledger, candidate, sharpe=0.9, p_value=0.001)
    ledger.start_trial({"lookback": 30})

    report = evaluate_statistical_gate(
        RETURNS,
        candidate=candidate,
        selected_trial_id=selected_trial,
        ledger=ledger,
    )

    assert not report.passed
    assert not report.checks["ledger_has_no_pending_trials"]
    assert not report.checks["sharpe_dispersion_observable"]


@pytest.mark.parametrize(
    ("metrics", "failure_fragment"),
    [
        (
            {"sharpe": 0.3, "selection_p_value": 0.2},
            "sharpe_basis",
        ),
        (
            {
                "sharpe": 0.3,
                "sharpe_basis": "adverse_execution_outer_holdout_returns_pct",
            },
            "selection_p_value",
        ),
    ],
)
def test_gate_rejects_incomplete_comparable_trial_family(
    tmp_path,
    metrics,
    failure_fragment,
):
    ledger = TrialLedger.create(tmp_path / "trials.jsonl", _protocol())
    candidate = {"lookback": 20}
    selected_trial = _complete(ledger, candidate, sharpe=0.9, p_value=0.001)
    other = ledger.start_trial({"lookback": 30})
    ledger.finish_trial(other, status="completed", metrics=metrics)

    report = evaluate_statistical_gate(
        RETURNS,
        candidate=candidate,
        selected_trial_id=selected_trial,
        ledger=ledger,
    )

    assert not report.passed
    assert not report.checks["trial_metric_family_complete"]
    assert failure_fragment in " ".join(report.failures)
    assert not report.checks["deflated_sharpe"]
    assert not report.checks["multiple_testing_selected"]


def test_selected_trial_must_survive_registered_multiple_testing_method(tmp_path):
    ledger = TrialLedger.create(tmp_path / "trials.jsonl", _protocol())
    candidate = {"lookback": 20}
    selected_trial = _complete(ledger, candidate, sharpe=0.9, p_value=0.02)
    _complete(ledger, {"lookback": 30}, sharpe=0.4, p_value=0.2)
    _complete(ledger, {"lookback": 40}, sharpe=0.2, p_value=0.3)

    report = evaluate_statistical_gate(
        RETURNS,
        candidate=candidate,
        selected_trial_id=selected_trial,
        ledger=ledger,
    )

    assert not report.passed
    assert not report.checks["multiple_testing_selected"]
    assert report.statistics["multiple_testing"]["threshold"] == pytest.approx(
        0.05 / 3,
        abs=1e-5,
    )


def test_gate_rejects_too_few_or_malformed_returns(tmp_path):
    ledger = TrialLedger.create(tmp_path / "trials.jsonl", _protocol())
    candidate = {"lookback": 20}
    selected_trial = _complete(ledger, candidate, sharpe=0.9, p_value=0.001)

    report = evaluate_statistical_gate(
        RETURNS[:5],
        candidate=candidate,
        selected_trial_id=selected_trial,
        ledger=ledger,
    )

    assert not report.passed
    assert not report.checks["minimum_observations"]
    assert not report.checks["bootstrap_lower_bound"]
    assert not report.checks["probabilistic_sharpe"]
    assert not report.checks["drawdown_permutation"]
    with pytest.raises(StatisticalGateError, match="finite"):
        evaluate_statistical_gate(
            [*RETURNS[:-1], float("nan")],
            candidate=candidate,
            selected_trial_id=selected_trial,
            ledger=ledger,
        )


def test_order_invariant_permutation_sample_fails_closed(tmp_path):
    ledger = TrialLedger.create(tmp_path / "trials.jsonl", _protocol())
    candidate = {"lookback": 20}
    selected_trial = _complete(ledger, candidate, sharpe=0.9, p_value=0.001)

    report = evaluate_statistical_gate(
        [1.0] * 12,
        candidate=candidate,
        selected_trial_id=selected_trial,
        ledger=ledger,
    )

    assert not report.passed
    assert not report.checks["drawdown_permutation"]
    assert not report.checks["probabilistic_sharpe"]
    assert not report.checks["deflated_sharpe"]


def test_exact_selected_trial_and_hash_chain_are_enforced(tmp_path):
    path = tmp_path / "trials.jsonl"
    ledger = TrialLedger.create(path, _protocol())
    candidate = {"lookback": 20}
    selected_trial = _complete(ledger, candidate, sharpe=0.9, p_value=0.001)

    with pytest.raises(TrialLedgerError, match="selected trial"):
        evaluate_statistical_gate(
            RETURNS,
            candidate=candidate,
            selected_trial_id="invented",
            ledger=ledger,
        )

    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[1])
    record["candidate"]["lookback"] = 99
    lines[1] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(TrialLedgerError, match="hash mismatch"):
        evaluate_statistical_gate(
            RETURNS,
            candidate=candidate,
            selected_trial_id=selected_trial,
            ledger=ledger,
        )
