"""Synthetic native-result audit tests; no downloaded data or backtesting."""

import copy
import json
from datetime import datetime, timezone

import pytest

from scripts.audit_freqtrade_15m_evidence import audit_run, in_session
from scripts.freqtrade_community_15m_research import STUDY


@pytest.fixture
def sample():
    protocol = json.loads((STUDY / "protocol.json").read_text())
    candidate = protocol["candidates"][0]
    data = {"backtest_start": "2026-07-10 00:00:00", "backtest_end": "2026-09-08 00:00:00",
            "timeframe": "15m", "timeframe_detail": "1m", "max_open_trades": 1,
            "starting_balance": 10000, "final_balance": 10007.8593, "stake_amount": 2500,
            "stoploss": -.02, "total_trades": 1, "profit_total_abs": 7.8593, "profit_total": .00078593,
            "trades": [{"pair": candidate["pair"], "open_date": "2026-07-13 07:00:00+00:00",
                        "close_date": "2026-07-13 08:00:00+00:00", "is_short": False, "is_open": False,
                        "amount": 1, "open_rate": 100, "close_rate": 101, "profit_abs": 7.8593,
                        "funding_fees": 7, "fee_open": .0007, "fee_close": .0007, "leverage": 1,
                        "stake_amount": 100, "max_stake_amount": 100, "initial_stop_loss_ratio": -.02,
                        "orders": [{"ft_is_entry": True}, {"ft_is_entry": False}]}]}
    return data, candidate, protocol


def test_independent_price_fee_funding_reconciliation(sample):
    data, candidate, protocol = sample
    assert audit_run(data, candidate, "late", "base", protocol)["issues"] == []
    data["trades"][0]["profit_abs"] += 1
    assert "trade 0: price/fee/funding PnL mismatch" in audit_run(data, candidate, "late", "base", protocol)["issues"]


@pytest.mark.parametrize("key,value,message", [
    ("leverage", 2, "leverage changed"), ("is_open", True, "unclosed trade"),
    ("fee_open", 0., "wrong fees"), ("stake_amount", 3000, "stake cap exceeded"),
    ("close_date", "2026-07-16 08:00:00+00:00", "holding time exceeded"),
    ("open_date", "2026-07-09 07:00:00+00:00", "out-of-window trade"),
])
def test_native_audit_catches_invalid_trade(sample, key, value, message):
    data, candidate, protocol = copy.deepcopy(sample)
    data["trades"][0][key] = value
    assert f"trade 0: {message}" in audit_run(data, candidate, "late", "base", protocol)["issues"]


def test_independent_session_calendar():
    assert in_session(datetime(2026, 7, 13, 7, tzinfo=timezone.utc))
    assert not in_session(datetime(2026, 7, 13, 16, tzinfo=timezone.utc))
    assert not in_session(datetime(2026, 7, 12, 7, tzinfo=timezone.utc))


def test_short_pnl_and_funding_sign(sample):
    data, candidate, protocol = copy.deepcopy(sample)
    trade = data["trades"][0]
    trade.update(is_short=True, close_rate=99, funding_fees=-.3, profit_abs=.5607)
    data.update(final_balance=10000.5607, profit_total_abs=.5607, profit_total=.00005607)
    assert not audit_run(data, candidate, "late", "base", protocol)["issues"]


def test_audit_rejects_overlapping_or_added_positions_and_bad_sessions(sample):
    data, candidate, protocol = copy.deepcopy(sample)
    data["trades"].append(copy.deepcopy(data["trades"][0]))
    assert "trade 1: overlapping trades" in audit_run(data, candidate, "late", "base", protocol)["issues"]
    data["trades"] = data["trades"][:1]
    data["trades"][0]["orders"].append({"ft_is_entry": True})
    assert "trade 0: position additions" in audit_run(data, candidate, "late", "base", protocol)["issues"]
    data["trades"][0].update(open_date="2026-07-13 16:00:00+00:00", close_date="2026-07-13 17:00:00+00:00")
    candidate["id"] += "-session"
    assert "trade 0: entry outside fixed session" in audit_run(data, candidate, "late", "base", protocol)["issues"]
