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
            "slippage_model": "locked_bps_stress",
        },
        "statistical_policy": {
            "primary_test": "deflated_sharpe_ratio",
            "alpha": 0.05,
            "multiple_testing": "benjamini_hochberg",
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
            {"statistical_policy": {"primary_test": "dsr", "alpha": 1.0,
                                    "multiple_testing": "bh"}},
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
