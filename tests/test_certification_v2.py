import json

import pytest

from xauby.backtest.certification_v2 import (
    CertificationProtocolError,
    CertificationProtocolV2,
    TrialLedger,
    TrialLedgerError,
)


def _protocol(**overrides):
    values = {
        "protocol_id": "btc-regime-ensemble-v2",
        "hypothesis": "Regime weights improve untouched forward risk-adjusted returns.",
        "primary_metric": "sharpe",
        "selection_rule": "Highest median outer-fold Sharpe among candidates passing every gate.",
        "data_identity": {
            "source": "locked-fixture",
            "venue": "okx",
            "symbol": "BTC-USDT-SWAP",
            "timeframe": "4h",
            "start": "2021-01-01T00:00:00Z",
            "end": "2026-01-01T00:00:00Z",
            "sha256": "a" * 64,
        },
        "validation_policy": {
            "method": "nested_purged_walk_forward",
            "outer_folds": 5,
            "inner_folds": 3,
            "outer_test_bars": 180,
            "outer_step_bars": 180,
            "inner_validation_bars": 90,
            "min_train_bars": 1_000,
            "min_inner_train_bars": 500,
            "purge_bars": 24,
            "embargo_bars": 24,
            "warmup_bars": 200,
        },
        "execution_policy": {
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
            "min_observations": 30,
            "min_native_coverage": 1.0,
            "min_observed_fill_ratio": 0.95,
            "max_latency_p95_ms": 500.0,
            "certification_scenario": "adverse",
            "scenarios": [
                {
                    "name": "baseline",
                    "fee_multiplier": 1.0,
                    "slippage_multiplier": 1.0,
                    "funding_multiplier": 1.0,
                    "latency_multiplier": 1.0,
                    "fill_ratio_multiplier": 1.0,
                    "min_compounded_return_pct": 0.0,
                    "max_drawdown_pct": 25.0,
                    "max_cost_to_gross_profit": 0.5,
                },
                {
                    "name": "adverse",
                    "fee_multiplier": 1.25,
                    "slippage_multiplier": 2.0,
                    "funding_multiplier": 2.0,
                    "latency_multiplier": 1.5,
                    "fill_ratio_multiplier": 0.95,
                    "min_compounded_return_pct": 0.0,
                    "max_drawdown_pct": 30.0,
                    "max_cost_to_gross_profit": 0.75,
                },
                {
                    "name": "severe",
                    "fee_multiplier": 1.5,
                    "slippage_multiplier": 3.0,
                    "funding_multiplier": 3.0,
                    "latency_multiplier": 2.0,
                    "fill_ratio_multiplier": 0.85,
                    "min_compounded_return_pct": -5.0,
                    "max_drawdown_pct": 35.0,
                    "max_cost_to_gross_profit": 1.0,
                },
            ],
        },
        "statistical_policy": {
            "primary_test": "deflated_sharpe_ratio",
            "alpha": 0.05,
            "multiple_testing": "benjamini_hochberg",
            "sharpe_metric": "sharpe",
            "sharpe_basis": "adverse_execution_outer_holdout_returns_pct",
            "selection_p_value_metric": "selection_p_value",
            "min_observations": 30,
            "bootstrap_samples": 1_000,
            "bootstrap_block_size": 3,
            "min_bootstrap_p05_pct": 0.0,
            "min_probability_profitable": 0.95,
            "permutation_samples": 1_000,
            "max_permutation_p_value": 0.05,
            "benchmark_sharpe": 0.0,
            "min_probabilistic_sharpe": 0.95,
            "min_deflated_sharpe": 0.95,
        },
        "artifact_policy": {
            "schema": "institutional_certification_artifact_v2",
            "hash_algorithm": "sha256",
            "require_ci": True,
            "repository": "iisara555/xAuby",
        },
        "random_seed": 20260820,
        "created_at": "2026-08-20T00:00:00+00:00",
    }
    values.update(overrides)
    return CertificationProtocolV2(**values)


def test_protocol_fingerprint_is_canonical_and_detached_from_caller():
    data = dict(_protocol().data_identity)
    protocol = _protocol(data_identity=data)
    reordered = _protocol(data_identity=dict(reversed(list(data.items()))))

    data["symbol"] = "CHANGED"

    assert protocol.fingerprint == reordered.fingerprint
    assert protocol.data_identity["symbol"] == "BTC-USDT-SWAP"
    assert len(protocol.fingerprint) == 64
    with pytest.raises(TypeError):
        protocol.data_identity["symbol"] = "DIRECT-MUTATION"


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"data_identity": {"source": "x"}}, "data_identity missing"),
        (
            {"validation_policy": {"method": "nested_purged_walk_forward", "outer_folds": 1,
                                   "inner_folds": 2, "outer_test_bars": 10,
                                   "outer_step_bars": 10,
                                   "inner_validation_bars": 5,
                                   "min_train_bars": 20,
                                   "min_inner_train_bars": 10,
                                   "purge_bars": 0, "embargo_bars": 0,
                                   "warmup_bars": 0}},
            "outer_folds",
        ),
        (
            {
                "statistical_policy": {
                    **dict(_protocol().statistical_policy),
                    "alpha": 1.0,
                }
            },
            "alpha",
        ),
        ({"random_seed": True}, "random_seed"),
    ],
)
def test_protocol_fails_closed_when_required_contract_is_invalid(override, message):
    with pytest.raises(CertificationProtocolError, match=message):
        _protocol(**override)


def test_protocol_rejects_non_finite_or_non_json_values():
    policy = dict(_protocol().execution_policy)
    policy["stress"] = float("nan")
    with pytest.raises(CertificationProtocolError, match="finite"):
        _protocol(execution_policy=policy)


def test_protocol_locks_execution_statistics_and_ci_artifact_contracts_together():
    execution = dict(_protocol().execution_policy)
    execution["venue"] = "binance"
    with pytest.raises(CertificationProtocolError, match="data_identity.venue"):
        _protocol(execution_policy=execution)

    statistics = dict(_protocol().statistical_policy)
    statistics["sharpe_basis"] = "optimistic_returns"
    with pytest.raises(CertificationProtocolError, match="certification scenario"):
        _protocol(statistical_policy=statistics)

    artifacts = dict(_protocol().artifact_policy)
    artifacts["require_ci"] = False
    with pytest.raises(CertificationProtocolError, match="require_ci"):
        _protocol(artifact_policy=artifacts)

    execution = dict(_protocol().execution_policy)
    execution["scenarios"] = list(execution["scenarios"][:2])
    with pytest.raises(CertificationProtocolError, match="at least three"):
        _protocol(execution_policy=execution)


def test_protocol_requires_aware_ordered_data_window():
    with pytest.raises(CertificationProtocolError, match="timezone"):
        _protocol(created_at="2026-08-20T00:00:00")
    data = dict(_protocol().data_identity)
    data["end"] = data["start"]
    with pytest.raises(CertificationProtocolError, match="after start"):
        _protocol(data_identity=data)


def test_trial_is_counted_before_it_has_an_outcome(tmp_path):
    ledger = TrialLedger.create(tmp_path / "trials.jsonl", _protocol())
    trial_id = ledger.start_trial({"fast": 10, "slow": 30})

    snapshot = ledger.verify()
    assert snapshot.trials_started == 1
    assert snapshot.trials_pending == 1

    ledger.finish_trial(trial_id, status="completed", metrics={"sharpe": 1.2})
    snapshot = ledger.verify()
    assert snapshot.trials_completed == 1
    assert snapshot.trials_pending == 0
    assert snapshot.metric_values("sharpe") == [1.2]


def test_failed_and_duplicate_candidates_remain_in_true_trial_count(tmp_path):
    candidate = {"fast": 10, "slow": 30}
    ledger = TrialLedger.create(tmp_path / "trials.jsonl", _protocol())
    winner = ledger.start_trial(candidate)
    failed = ledger.start_trial({"fast": 11, "slow": 30})
    duplicate = ledger.start_trial(candidate)
    ledger.finish_trial(winner, status="completed", metrics={"sharpe": 1.5})
    ledger.finish_trial(failed, status="failed", error="insufficient bars")
    ledger.finish_trial(duplicate, status="aborted", error="resource budget reached")

    evidence = ledger.evidence_for_candidate(candidate)

    assert evidence["n_trials"] == 3
    assert evidence["n_completed"] == 1
    assert evidence["n_failed"] == 1
    assert evidence["n_aborted"] == 1
    assert evidence["selected_trial_ids"] == [winner]
    assert len(evidence["ledger_sha256"]) == 64


def test_candidate_evidence_can_be_pinned_to_one_exact_completed_trial(tmp_path):
    candidate = {"fast": 10, "slow": 30}
    ledger = TrialLedger.create(tmp_path / "trials.jsonl", _protocol())
    first = ledger.start_trial(candidate)
    second = ledger.start_trial(candidate)
    ledger.finish_trial(first, status="completed", metrics={"sharpe": 1.0})
    ledger.finish_trial(second, status="completed", metrics={"sharpe": 1.1})

    evidence = ledger.evidence_for_candidate(candidate, selected_trial_id=second)

    assert evidence["selected_trial_ids"] == [second]
    with pytest.raises(TrialLedgerError, match="selected trial"):
        ledger.evidence_for_candidate(candidate, selected_trial_id="invented")


def test_candidate_without_completed_ledger_trial_cannot_claim_evidence(tmp_path):
    ledger = TrialLedger.create(tmp_path / "trials.jsonl", _protocol())
    ledger.start_trial({"fast": 10})

    with pytest.raises(TrialLedgerError, match="no completed trial"):
        ledger.evidence_for_candidate({"fast": 10})
    with pytest.raises(TrialLedgerError, match="no completed trial"):
        ledger.evidence_for_candidate({"fast": 99})


def test_evidence_requires_the_pre_registered_primary_metric(tmp_path):
    ledger = TrialLedger.create(tmp_path / "trials.jsonl", _protocol())
    trial_id = ledger.start_trial({"fast": 10})
    ledger.finish_trial(trial_id, status="completed", metrics={"profit_factor": 9.0})

    with pytest.raises(TrialLedgerError, match="primary metric"):
        ledger.evidence_for_candidate({"fast": 10})


def test_unknown_or_double_outcome_is_refused(tmp_path):
    ledger = TrialLedger.create(tmp_path / "trials.jsonl", _protocol())
    trial_id = ledger.start_trial({"fast": 10})

    with pytest.raises(TrialLedgerError, match="unknown trial_id"):
        ledger.finish_trial("invented", status="completed", metrics={"sharpe": 99})

    ledger.finish_trial(trial_id, status="completed", metrics={"sharpe": 1.0})
    with pytest.raises(TrialLedgerError, match="already finished"):
        ledger.finish_trial(trial_id, status="completed", metrics={"sharpe": 99})


def test_failed_trial_requires_a_reason_and_completed_trial_requires_metrics(tmp_path):
    ledger = TrialLedger.create(tmp_path / "trials.jsonl", _protocol())
    first = ledger.start_trial({"fast": 10})
    second = ledger.start_trial({"fast": 11})

    with pytest.raises(TrialLedgerError, match="requires metrics"):
        ledger.finish_trial(first, status="completed")
    with pytest.raises(TrialLedgerError, match="requires an error"):
        ledger.finish_trial(second, status="failed")


def test_tampering_with_candidate_or_chain_is_detected(tmp_path):
    path = tmp_path / "trials.jsonl"
    ledger = TrialLedger.create(path, _protocol())
    ledger.start_trial({"fast": 10})
    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[1])
    record["candidate"]["fast"] = 99
    lines[1] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(TrialLedgerError, match="hash mismatch"):
        ledger.verify()


def test_partial_trailing_record_is_detected(tmp_path):
    path = tmp_path / "trials.jsonl"
    ledger = TrialLedger.create(path, _protocol())
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind":"trial_started"')

    with pytest.raises(TrialLedgerError, match="partial trailing"):
        ledger.verify()


def test_ledger_cannot_be_recreated_or_opened_under_another_protocol(tmp_path):
    path = tmp_path / "trials.jsonl"
    original = _protocol()
    ledger = TrialLedger.create(path, original)

    with pytest.raises(TrialLedgerError, match="already exists"):
        TrialLedger.create(path, original)
    with pytest.raises(TrialLedgerError, match="does not match"):
        ledger.verify(expected_protocol=_protocol(random_seed=1))
