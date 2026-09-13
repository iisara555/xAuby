"""Offline contract tests, never historical backtesting on the live VPS."""

import ast
import importlib.util
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from scripts import freqtrade_community_15m_research as research

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def protocol():
    return json.loads((research.STUDY / "protocol.json").read_text())


@pytest.fixture
def strategies(monkeypatch):
    class Volatility:
        timeframe = "1h"
        can_short = True

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
        can_short = True

    for name, klass in (("VolatilitySystem", Volatility), ("FReinforcedStrategy", Reinforced)):
        stub = types.ModuleType(name)
        setattr(stub, name, klass)
        monkeypatch.setitem(sys.modules, name, stub)
    spec = importlib.util.spec_from_file_location("community_15m_test", research.STUDY / "strategies.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_refuses_local_before_any_output(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(sys, "argv", ["research", "--out-dir", str(tmp_path / "must_not_exist")])
    with pytest.raises(RuntimeError, match="GitHub-hosted"):
        research.main()
    assert not (tmp_path / "must_not_exist").exists()


@pytest.mark.parametrize("name", research.INTERVAL_PATCHES)
def test_informative_patch_changes_only_one_assignment_rhs(name):
    original, replacement = research.INTERVAL_PATCHES[name]
    source = f"def populate_indicators(self, dataframe):\n    {original}\n    return dataframe\n".encode()
    patched, note = research.adapt_interval(name, source)
    assert original in note and replacement in note
    before = ast.parse(source)
    after = ast.parse(patched)
    old_assign = [n for n in ast.walk(before) if isinstance(n, ast.Assign)]
    new_assign = [n for n in ast.walk(after) if isinstance(n, ast.Assign)]
    assert len(old_assign) == len(new_assign) == 1
    assert new_assign[0].value.value == 60
    new_assign[0].value = old_assign[0].value
    assert ast.dump(before) == ast.dump(after)
    with pytest.raises(ValueError, match="interval changed"):
        research.adapt_interval(name, source + source)
    with pytest.raises(ValueError, match="interval changed"):
        research.adapt_interval(name, patched)


def test_risk_and_session_helpers_identical_to_frozen_previous_study():
    old = ast.parse((ROOT / "docs/research/freqtrade_community_2026_09/strategies.py").read_text())
    new = ast.parse((research.STUDY / "strategies.py").read_text())
    for name in ("ResearchRiskMixin", "in_research_session"):
        a = next(node for node in old.body if getattr(node, "name", "") == name)
        b = next(node for node in new.body if getattr(node, "name", "") == name)
        assert ast.dump(a) == ast.dump(b)


def test_frozen_candidates_are_new_research_only(protocol, strategies):
    assert len(protocol["candidates"]) == 4
    assert len({row["id"] for row in protocol["candidates"]}) == 4
    assert protocol["reserved_forward_window"]["start"] == "20260914"
    assert protocol["execution"]["detail_timeframes"] == {"15m": "1m"}
    for row in protocol["candidates"]:
        strategy = getattr(strategies, row["strategy"])()
        assert row["live_weight"] == 0
        assert row["timeframe"] == strategy.timeframe == "15m"
        assert row["informative"] == "1h"
        assert strategy.can_short
        assert strategy.startup_candle_count == protocol["validation"]["risk_strategy_startup"][row["strategy"]]
        assert strategy.stoploss == -.02
        assert strategy.position_adjustment_enable is False
        assert strategy.leverage(None, None, None, 10, 10) == 1


def test_risk_stake_and_timeout(strategies):
    strategy = strategies.Community15mVolatilityRisk()
    strategy.wallets = types.SimpleNamespace(get_total_stake_amount=lambda: 8000)
    args = ("BTC/USDT:USDT", None, 100, 2500, 10, 10000, 1, None, "long")
    assert strategy.custom_stake_amount(*args) == 2000
    strategy.wallets = types.SimpleNamespace(get_total_stake_amount=lambda: 20)
    assert strategy.custom_stake_amount(*args) == 0
    now = datetime(2026, 9, 13, tzinfo=timezone.utc)
    trade = types.SimpleNamespace(open_date_utc=now - timedelta(hours=48))
    assert strategy.custom_exit("BTC/USDT:USDT", trade, now, 100, 0) == "research_timeout_48h"
    assert strategy.custom_exit("BTC/USDT:USDT", trade, now - timedelta(minutes=1), 100, 0) is None


def test_15m_next_open_session_boundaries_and_unrestricted_exit(strategies):
    # Summer: London starts 07 UTC, NY starts 12 UTC; weekends excluded.
    df = pd.DataFrame({"date": pd.to_datetime([
        "2026-07-13T06:30Z", "2026-07-13T06:45Z", "2026-07-13T10:45Z",
        "2026-07-13T11:45Z", "2026-07-13T15:45Z", "2026-07-12T06:45Z",
        "2026-03-16T07:45Z", "2026-03-16T11:45Z",
    ], utc=True), "raw_long": [0] * 8, "raw_short": [1] * 8,
        "enter_long": [0] * 8, "enter_short": [0] * 8})
    strategy = strategies.Community15mVolatilitySession()
    entries = strategy.populate_entry_trend(df, {})
    assert entries["enter_short"].tolist() == [0, 1, 0, 1, 0, 0, 1, 1]
    exits = strategy.populate_exit_trend(entries, {})
    assert exits["exit_long"].tolist() == [1] * 8
    assert exits["enter_short"].tolist() == [0, 1, 0, 1, 0, 0, 1, 1]


def test_complete_15m_data_and_strict_missing_duplicate_grid_numeric_gates():
    protocol = {"data_start": "20260215", "data_end_exclusive": "20260216"}
    dates = pd.date_range("2026-02-15", periods=96, freq="15min", tz="UTC")
    df = pd.DataFrame({"date": dates, "open": 100., "high": 101., "low": 99., "close": 100., "volume": 1.})
    assert research.audit_15m_frame(df, protocol)["bars"] == 96
    variants = [df.iloc[1:], pd.concat([df, df.iloc[:1]], ignore_index=True)]
    off_grid = df.copy()
    off_grid.loc[0, "date"] += pd.Timedelta(minutes=1)
    variants.append(off_grid)
    for column, value in (("close", float("inf")), ("volume", -1.), ("high", 98.)):
        invalid = df.copy()
        invalid.loc[0, column] = value
        variants.append(invalid)
    for variant in variants:
        with pytest.raises(ValueError, match="data integrity failed"):
            research.audit_15m_frame(variant, protocol)


def test_stress_does_not_pass_missing_or_losing_results(protocol):
    valid = {"net_return_pct": 1, "profit_factor": 1.1, "trades": 40,
             "unclosed_trades": 0, "max_drawdown_pct": 2}
    assert not research.stress_reasons({"early": valid, "late": valid}, protocol["screen_gate"])
    assert research.stress_reasons({"early": None, "late": valid}, protocol["screen_gate"])
    assert research.stress_reasons({"early": valid, "late": {**valid, "net_return_pct": -1}}, protocol["screen_gate"])


def test_ledger_records_native_fields_and_entry_count():
    data = {"trades": [{"profit_abs": 3, "is_short": True,
                        "orders": [{"ft_is_entry": True}, {"ft_is_entry": False}]}]}
    rows = research.export_trades(data, {"id": "example"}, "late", "base")
    assert rows[0]["candidate"] == "example"
    assert rows[0]["entry_orders"] == 1
    assert rows[0]["profit_abs"] == 3
    assert set(rows[0]) == set(research.LEDGER_FIELDS)
