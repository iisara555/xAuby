"""Small offline tests; actual Freqtrade backtests run only on hosted CI."""

import ast
import csv
import hashlib
import importlib.util
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def research():
    return load_module(ROOT / "scripts/freqtrade_community_research.py", "community_harness")


@pytest.fixture
def strategies(monkeypatch):
    class Volatility:
        timeframe = "1h"

        def populate_entry_trend(self, dataframe, metadata):
            dataframe.loc[dataframe["raw_long"] == 1, "enter_long"] = 1
            dataframe.loc[dataframe["raw_short"] == 1, "enter_short"] = 1
            return dataframe

        def populate_exit_trend(self, dataframe, metadata):
            dataframe["exit_long"] = dataframe["enter_short"]
            dataframe["exit_short"] = dataframe["enter_long"]
            return dataframe

    class Reinforced:
        timeframe = "5m"

    for name, klass in (("VolatilitySystem", Volatility), ("FReinforcedStrategy", Reinforced)):
        stub = types.ModuleType(name)
        setattr(stub, name, klass)
        monkeypatch.setitem(sys.modules, name, stub)
    return load_module(ROOT / "docs/research/freqtrade_community_2026_09/strategies.py", "community_strategies_test")


def test_runner_refuses_local_execution(research, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("RUNNER_ENVIRONMENT", raising=False)
    with pytest.raises(RuntimeError, match="never run on the trading VPS"):
        research.require_hosted_runner()
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "self-hosted")
    with pytest.raises(RuntimeError):
        research.require_hosted_runner()


def test_no_trade_command_allowed(research, monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    with pytest.raises(ValueError, match="non-trading"):
        research.run_command(["trade"], tmp_path / "unused.log", [])


def test_research_config_has_no_credentials_or_live_mode(research):
    config = research.make_config("XAU/USDT:USDT")
    assert config["dry_run"] is True
    assert config["exchange"]["pair_whitelist"] == ["XAU/USDT:USDT"]
    assert all(config["exchange"][key] == "" for key in ("key", "secret", "password"))
    assert config.get("api_server", {}).get("enabled", False) is False
    assert config.get("telegram", {}).get("enabled", False) is False


def test_download_buffer_does_not_change_locked_windows(research):
    protocol = {"data_start": "20260215", "data_end_exclusive": "20260908",
                "windows": {"late": "20260710-20260908"}}
    assert research.download_timerange(protocol) == "20260215-20260909"
    assert protocol["data_end_exclusive"] == "20260908"
    assert protocol["windows"]["late"] == "20260710-20260908"


def test_compatibility_patch_only_adds_parameter_space(research):
    source = (b'adx_period = IntParameter(4, 24, default=14)\n'
              b'ema_short_period = IntParameter(4, 24, default=8)\n'
              b'ema_long_period = IntParameter(12, 175, default=21)\n')
    executable, patches = research.compatible_upstream("FReinforcedStrategy.py", source)
    assert len(patches) == 3
    original_tree = ast.parse(source)
    patched_tree = ast.parse(executable)
    for node in ast.walk(patched_tree):
        if isinstance(node, ast.Call):
            spaces = [kw for kw in node.keywords if kw.arg == "space"]
            assert len(spaces) == 1 and spaces[0].value.value == "buy"
            node.keywords = [kw for kw in node.keywords if kw.arg != "space"]
    assert ast.dump(original_tree) == ast.dump(patched_tree)
    assert research.compatible_upstream("VolatilitySystem.py", source) == (source, [])
    with pytest.raises(ValueError, match="declaration changed"):
        research.compatible_upstream("FReinforcedStrategy.py", b"different source")


def test_logged_native_errors_are_not_success(research):
    assert research.fatal_log_errors("2026-09-12 - ERROR - Configuration error")
    assert research.fatal_log_errors("2026-09-12 - CRITICAL - invalid configuration")
    assert not research.fatal_log_errors("2026-09-12 - WARNING - using historical data")


def test_recursive_probes_respect_venue_limit_without_mutating_protocol(research):
    protocol = {"validation": {"recursive_startups": [199, 499, 999, 1999]}}
    assert research.recursive_startups(protocol) == [199, 499, 999, 1499]
    assert protocol["validation"]["recursive_startups"][-1] == 1999


def test_session_filter_dst_weekend_and_boundaries(strategies):
    clocks = pd.Series(pd.to_datetime([
        "2026-01-12T07:00Z", "2026-01-12T08:00Z", "2026-01-12T12:00Z",
        "2026-01-12T13:00Z", "2026-01-12T17:00Z",
        "2026-07-13T07:00Z", "2026-07-13T11:00Z", "2026-07-13T12:00Z",
        "2026-07-13T16:00Z", "2026-07-12T08:00Z",
        # US daylight time has started; UK daylight time has not.
        "2026-03-16T07:00Z", "2026-03-16T12:00Z",
    ], utc=True))
    assert strategies.in_research_session(clocks).tolist() == [
        False, True, False, True, False, True, False, True, False, False, False, True,
    ]


def test_session_never_suppresses_opposite_exit(strategies):
    strategy = strategies.CommunityVolatilitySession()
    df = pd.DataFrame({"date": pd.to_datetime(["2026-07-13T19:00Z"], utc=True),
                       "raw_long": [0], "raw_short": [1], "enter_long": [0], "enter_short": [0]})
    entry = strategy.populate_entry_trend(df, {})
    assert entry["enter_short"].iloc[0] == 0
    exit_df = strategy.populate_exit_trend(entry, {})
    assert exit_df["exit_long"].iloc[0] == 1
    assert exit_df["enter_short"].iloc[0] == 0


def test_session_uses_next_open_clock(strategies):
    strategy = strategies.CommunityVolatilitySession()
    df = pd.DataFrame({"date": pd.to_datetime(["2026-07-13T06:00Z"], utc=True),
                       "raw_long": [1], "raw_short": [0], "enter_long": [0], "enter_short": [0]})
    assert strategy.populate_entry_trend(df, {})["enter_long"].iloc[0] == 1


def test_risk_caps_and_native_timeframes(strategies):
    strategy = strategies.CommunityVolatilityRisk()
    strategy.wallets = types.SimpleNamespace(get_total_stake_amount=lambda: 8000)
    args = ("BTC/USDT:USDT", None, 100, 2500, 10, 10000, 1, None, "long")
    assert strategy.custom_stake_amount(*args) == 2000
    assert strategy.stoploss == -0.02
    assert strategy.position_adjustment_enable is False
    assert strategy.timeframe == "1h"
    assert strategies.CommunityReinforcedRisk().timeframe == "5m"
    now = datetime(2026, 9, 11, tzinfo=timezone.utc)
    trade = types.SimpleNamespace(open_date_utc=now - timedelta(hours=48))
    assert strategy.custom_exit("BTC/USDT:USDT", trade, now, 100, 0) == "research_timeout_48h"


def test_native_ledger_reconciliation_and_empty_pf(research):
    data = {"trades": [{"profit_abs": 15, "is_short": False}, {"profit_abs": -5, "is_short": True}],
            "profit_total_abs": 10, "profit_total": .001, "total_trades": 2, "max_drawdown_account": .01}
    result = research.summarize_result(data)
    assert result["profit_factor"] == 3
    assert result["net_return_pct"] == .1
    assert result["side_breakdown"]["short"]["net_pnl_usdt"] == -5
    data["profit_total_abs"] = 11
    with pytest.raises(ValueError, match="does not reconcile"):
        research.summarize_result(data)


def test_missing_results_and_bias_cannot_pass(research, tmp_path):
    protocol = json.loads((ROOT / "docs/research/freqtrade_community_2026_09/protocol.json").read_text())
    assert research.screening_reasons({"early": None, "late": None}, protocol["screen_gate"])
    assert research.lookahead_status(tmp_path / "missing.csv")["status"] == "unverified"
    assert all(row["live_weight"] == 0 for row in protocol["candidates"])
    assert protocol["reserved_forward_window"]["start"] > protocol["data_end_exclusive"]


def test_persisted_results_and_registry_keep_failed_candidates_research_only(research):
    study = ROOT / "docs/research/freqtrade_community_2026_09"
    protocol = json.loads((study / "protocol.json").read_text())
    results = json.loads((study / "results.json").read_text())
    registry = json.loads((study / "registry.json").read_text())
    by_id = {row["id"]: row for row in registry["candidates"]}
    assert set(by_id) == {row["id"] for row in protocol["candidates"]}
    assert len(results["candidates"]) == 4
    assert results["errors"] == []
    assert registry["scope"] == "research_only"
    assert registry["certified"] is registry["production_arena_admitted"] is False
    assert registry["live_weight"] == results["live_weight"] == 0
    for row in results["candidates"]:
        registered = by_id[row["id"]]
        reasons = research.screening_reasons(row["results"], protocol["screen_gate"])
        assert reasons == row["screen_reasons"] == registered["screen_reasons"]
        assert reasons and registered["status"] == row["status"] == "rejected_screen"
        assert registered["live_weight"] == row["live_weight"] == 0
        assert registered["certified"] is registered["production_arena_admitted"] is False
        assert registered["lookahead"] == row["lookahead"]
        assert registered["lookahead"]["status"] == "no_bias_detected_in_tested_signals"
        review = registered["recursive_review"]
        assert review["startup_candles"] == protocol["validation"]["risk_strategy_startup"][row["timeframe"]]
        assert review["status"] == "reviewed_no_material_variance_at_actual_startup_in_printed_table"
    priority = by_id["btc-volatility-1h-risk"]
    assert priority["research_disposition"] == "priority_for_unchanged_fresh_sample"
    assert priority["screen_reasons"] == ["late: insufficient trades"]


def test_persisted_provenance_and_original_evidence_hashes():
    study = ROOT / "docs/research/freqtrade_community_2026_09"
    provenance = json.loads((study / "provenance.json").read_text())
    assert hashlib.sha256((study / "protocol.json").read_bytes()).hexdigest() == provenance["run"]["protocol_sha256"]
    for local, original in (("results.json", "summary.json"), ("dependencies.txt", "dependencies.txt")):
        assert hashlib.sha256((study / local).read_bytes()).hexdigest() == provenance["artifact_file_sha256"][original]
    assert provenance["execution"]["command_counts"] == {
        "download-data": 2, "backtesting": 14, "lookahead-analysis": 4, "recursive-analysis": 4,
    }
    assert provenance["execution"]["all_native_commands_successful"] is True
    assert provenance["execution"]["stress_backtests"] == 0
    for data in provenance["data"].values():
        assert data["certification_funding_complete"] is False
        for candles in data["candles"].values():
            assert candles["missing"] == candles["duplicates"] == 0
            assert candles["invalid_ohlcv"] is False
            assert candles["bars"] == candles["expected_bars"]


def test_persisted_trade_ledger_reconciles_and_derivatives_obey_risk_caps():
    study = ROOT / "docs/research/freqtrade_community_2026_09"
    results = json.loads((study / "results.json").read_text())
    audits = json.loads((study / "native_audit.json").read_text())["runs"]
    expected = {f"{candidate['id']}_{window}_base": metrics
                for candidate in results["candidates"] + results["original_controls"]
                for window, metrics in candidate["results"].items()}
    with (study / "trade_ledger.csv").open(newline="") as handle:
        ledger = list(csv.DictReader(handle))
    assert len(ledger) == 301
    assert len(audits) == len(expected) == 14
    assert {trade["run"] for trade in ledger} == set(expected)
    for audit in audits:
        metrics = expected[audit["run"]]
        trades = sorted((t for t in ledger if t["run"] == audit["run"]), key=lambda t: t["open_date"])
        assert audit["issues"] == []
        assert len(trades) == audit["trades"] == metrics["trades"]
        pnl = sum(float(t["profit_abs"]) for t in trades)
        assert pnl == pytest.approx(metrics["net_pnl_usdt"], abs=0.02)
        wallet = audit["starting_balance"]
        start = datetime.fromisoformat(metrics["backtest_start"]).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(metrics["backtest_end"]).replace(tzinfo=timezone.utc)
        previous_close = start
        for trade in trades:
            opened = datetime.fromisoformat(trade["open_date"])
            closed = datetime.fromisoformat(trade["close_date"])
            assert start <= opened < end and opened <= closed <= end
            assert opened >= previous_close
            assert trade["is_open"] == "False"
            assert float(trade["fee_open"]) == float(trade["fee_close"]) == 0.0007
            if audit["risk_derivative"]:
                assert float(trade["leverage"]) == 1
                assert int(trade["entry_orders"]) == 1
                assert float(trade["initial_stop_loss_ratio"]) == -0.02
                assert float(trade["max_stake_amount"]) <= min(2500, 0.25 * wallet) + 0.01
                assert float(trade["trade_duration"]) <= 2880
            wallet += float(trade["profit_abs"])
            previous_close = closed
        assert wallet == pytest.approx(audit["final_balance"], abs=0.02)
