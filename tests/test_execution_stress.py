import pytest

from xauby.backtest.execution_stress import (
    ExecutionStressError,
    evaluate_execution_stress,
)


def _observations(count=12, *, gross_return_pct=1.0):
    return [
        {
            "venue": "okx",
            "symbol": "BTC-USDT-SWAP",
            "market_type": "swap",
            "native": True,
            "data_sha256": "c" * 64,
            "gross_return_pct": gross_return_pct if index % 3 else -0.25,
            "holding_hours": 12.0,
            "side": "LONG" if index % 2 else "SHORT",
            "fill_ratio": 0.99,
            "latency_ms": 120.0 + index,
            "observed_slippage_bps": 2.5,
            "outer_fold_index": index % 2,
        }
        for index in range(count)
    ]


def test_every_locked_execution_scenario_passes_and_adverse_returns_are_exposed(
    certification_protocol_factory,
):
    protocol = certification_protocol_factory()

    report = evaluate_execution_stress(_observations(), protocol=protocol)

    assert report.passed
    assert report.certification_scenario == "adverse"
    assert len(report.certification_returns) == 12
    scenarios = report.statistics["scenarios"]
    assert set(scenarios) == {"baseline", "adverse", "severe"}
    assert scenarios["baseline"]["total_cost_pct"] < scenarios["adverse"]["total_cost_pct"]
    assert scenarios["adverse"]["total_cost_pct"] < scenarios["severe"]["total_cost_pct"]
    assert tuple(scenarios["adverse"]["net_returns_pct"]) == report.certification_returns


@pytest.mark.parametrize(
    ("field", "value", "failed_check"),
    [
        ("venue", "binance", "venue_data_identity"),
        ("symbol", "ETH-USDT-SWAP", "venue_data_identity"),
        ("data_sha256", "d" * 64, "venue_data_identity"),
        ("native", False, "native_coverage"),
        ("fill_ratio", 0.5, "observed_fill_ratio"),
        ("latency_ms", 2_000.0, "latency_p95"),
    ],
)
def test_identity_proxy_fill_and_latency_evidence_fail_closed(
    certification_protocol_factory,
    field,
    value,
    failed_check,
):
    rows = _observations()
    for row in rows:
        row[field] = value

    report = evaluate_execution_stress(
        rows,
        protocol=certification_protocol_factory(),
    )

    assert not report.passed
    assert not report.checks[failed_check]


def test_cost_stress_can_destroy_an_apparent_gross_edge(certification_protocol_factory):
    rows = _observations(gross_return_pct=0.05)

    report = evaluate_execution_stress(
        rows,
        protocol=certification_protocol_factory(),
    )

    assert not report.passed
    assert not report.checks["scenario:baseline"]
    assert report.statistics["scenarios"]["baseline"]["compounded_return_pct"] < 0


def test_scenario_threshold_drift_changes_protocol_and_verdict(
    certification_protocol_factory,
):
    base = certification_protocol_factory()
    scenarios = [dict(scenario) for scenario in base.execution_policy["scenarios"]]
    scenarios[1]["min_compounded_return_pct"] = 100.0
    strict = certification_protocol_factory(execution_overrides={"scenarios": scenarios})

    report = evaluate_execution_stress(_observations(), protocol=strict)

    assert base.fingerprint != strict.fingerprint
    assert not report.checks["scenario:adverse"]


def test_malformed_execution_observation_is_rejected(certification_protocol_factory):
    rows = _observations()
    rows[0]["fill_ratio"] = float("nan")
    with pytest.raises(ExecutionStressError, match="finite"):
        evaluate_execution_stress(rows, protocol=certification_protocol_factory())

    rows = _observations()
    del rows[0]["holding_hours"]
    with pytest.raises(ExecutionStressError, match="missing"):
        evaluate_execution_stress(rows, protocol=certification_protocol_factory())
