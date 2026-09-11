"""Small offline tests; actual Freqtrade backtests run only on hosted CI."""

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
    assert config["api_server"]["enabled"] is False


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
