import pytest

from xauby.backtest.certification_v2 import CertificationProtocolV2


@pytest.fixture
def certification_protocol_factory():
    def factory(
        *,
        execution_overrides=None,
        statistical_overrides=None,
        artifact_overrides=None,
    ):
        scenarios = [
            {
                "name": name,
                "fee_multiplier": multiplier,
                "slippage_multiplier": multiplier,
                "funding_multiplier": multiplier,
                "latency_multiplier": multiplier,
                "fill_ratio_multiplier": fill_multiplier,
                "min_compounded_return_pct": minimum_return,
                "max_drawdown_pct": 40.0,
                "max_cost_to_gross_profit": 2.0,
            }
            for name, multiplier, fill_multiplier, minimum_return in (
                ("baseline", 1.0, 1.0, 0.0),
                ("adverse", 1.5, 0.95, 0.0),
                ("severe", 2.0, 0.85, -5.0),
            )
        ]
        execution_policy = {
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
            "scenarios": scenarios,
        }
        execution_policy.update(execution_overrides or {})
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
        statistical_policy.update(statistical_overrides or {})
        artifact_policy = {
            "schema": "institutional_certification_artifact_v2",
            "hash_algorithm": "sha256",
            "require_ci": True,
            "repository": "iisara555/xAuby",
        }
        artifact_policy.update(artifact_overrides or {})
        return CertificationProtocolV2(
            protocol_id="phase1-institutional-test-v2",
            hypothesis="A searched candidate survives untouched institutional gates.",
            primary_metric="sharpe",
            selection_rule="Inner-only selection evaluated on untouched outer holdouts.",
            data_identity={
                "source": "locked-fixture",
                "venue": "okx",
                "symbol": "BTC-USDT-SWAP",
                "timeframe": "4h",
                "start": "2021-01-01T00:00:00Z",
                "end": "2026-01-01T00:00:00Z",
                "sha256": "c" * 64,
            },
            validation_policy={
                "method": "nested_purged_walk_forward",
                "outer_folds": 2,
                "inner_folds": 2,
                "outer_test_bars": 10,
                "outer_step_bars": 10,
                "inner_validation_bars": 5,
                "min_train_bars": 40,
                "min_inner_train_bars": 15,
                "purge_bars": 2,
                "embargo_bars": 2,
                "warmup_bars": 5,
            },
            execution_policy=execution_policy,
            statistical_policy=statistical_policy,
            artifact_policy=artifact_policy,
            random_seed=20260820,
            created_at="2026-08-20T00:00:00+00:00",
        )

    return factory
