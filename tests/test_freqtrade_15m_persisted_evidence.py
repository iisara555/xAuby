"""Durable native evidence consistency checks; no local simulation."""

import csv
import hashlib
import json
from datetime import datetime, timezone

import pytest

from scripts.audit_freqtrade_15m_evidence import in_session
from scripts.freqtrade_community_15m_research import STUDY
from scripts.freqtrade_community_research import screening_reasons


def read(name):
    return json.loads((STUDY / name).read_text())


def test_frozen_evidence_hashes_and_zero_weight_registry():
    protocol, summary = read("protocol.json"), read("results.json")
    provenance, registry = read("provenance.json"), read("registry.json")
    hashes = provenance["artifact_file_sha256"]
    for local, native in (("protocol.json", "protocol.json"), ("results.json", "summary.json"),
                          ("dependencies.txt", "dependencies.txt"), ("strategies.py", "strategies/CommunityStrategies.py")):
        assert hashlib.sha256((STUDY / local).read_bytes()).hexdigest() == hashes[native]
    # Native csv.writer uses CRLF; repository normalizes only line endings.
    ledger = (STUDY / "trade_ledger.csv").read_text().replace("\n", "\r\n").encode()
    assert hashlib.sha256(ledger).hexdigest() == hashes["trade_ledger.csv"]
    assert provenance["run"]["protocol_sha256"] == hashes["protocol.json"]
    assert provenance["execution"]["command_counts"] == {
        "download-data": 2, "backtesting": 8, "lookahead-analysis": 4, "recursive-analysis": 4,
    }
    assert provenance["execution"]["all_native_commands_successful"]
    assert provenance["execution"]["stress_backtests"] == 0
    assert {r["id"] for r in protocol["candidates"]} == {r["id"] for r in registry["candidates"]}
    for candidate in summary["candidates"]:
        row = next(r for r in registry["candidates"] if r["id"] == candidate["id"])
        assert candidate["status"] == row["status"] == "rejected_screen"
        assert screening_reasons(candidate["results"], protocol["screen_gate"]) == row["screen_reasons"]
        assert row["live_weight"] == candidate["live_weight"] == 0
        assert not row["certified"] and not row["arena_admitted"] and not row["funding_certified"]
        assert row["stress_status"] == "not_run_base_gate_failed"
        recursive = row["recursive_review"]
        if "volatility" in row["id"]:
            assert recursive["status"] == "failed_preregistered_tolerance"
            assert abs(recursive["atr_difference_percent"]) > recursive["tolerance_percent"]
            assert recursive["actual_startup"] == 499
        else:
            assert recursive["actual_startup"] == 999
            assert row["lookahead_review"]["tested_signals"] == 16


def test_persisted_198_trade_ledger_and_independent_wallet_audits():
    summary, audits = read("results.json"), read("native_audit.json")
    with (STUDY / "trade_ledger.csv").open(newline="") as stream:
        ledger = list(csv.DictReader(stream))
    assert len(ledger) == 198
    assert audits["all_checks_passed"] and len(audits["runs"]) == 8
    for candidate in summary["candidates"]:
        for window, metrics in candidate["results"].items():
            trades = sorted((t for t in ledger if t["candidate"] == candidate["id"] and t["window"] == window),
                            key=lambda t: t["open_date"])
            audit = next(r for r in audits["runs"] if r["run"] == f"{candidate['id']}_{window}_base")
            assert len(trades) == audit["trades"] == metrics["trades"]
            assert audit["issues"] == []
            assert audit["max_trade_pnl_recompute_error_usdt"] < 1e-8
            start = datetime.fromisoformat(metrics["backtest_start"]).replace(tzinfo=timezone.utc)
            end = datetime.fromisoformat(metrics["backtest_end"]).replace(tzinfo=timezone.utc)
            wallet, previous_close = 10000., start
            for trade in trades:
                opened, closed = (datetime.fromisoformat(trade[k]) for k in ("open_date", "close_date"))
                assert start <= opened < end and opened <= closed <= end
                assert opened >= previous_close
                assert trade["pair"] == candidate["pair"] and trade["cost"] == "base"
                assert trade["is_open"] == "False"
                assert float(trade["fee_open"]) == float(trade["fee_close"]) == .0007
                assert float(trade["leverage"]) == int(trade["entry_orders"]) == 1
                assert float(trade["initial_stop_loss_ratio"]) == -.02
                assert 0 < float(trade["stake_amount"]) <= min(2500, .25 * wallet) + .01
                assert float(trade["trade_duration"]) <= 2880
                if candidate["id"].endswith("-session"):
                    assert in_session(opened)
                wallet += float(trade["profit_abs"])
                previous_close = closed
            assert wallet == pytest.approx(audit["final_balance"], abs=.02)
            assert wallet - 10000 == pytest.approx(metrics["net_pnl_usdt"], abs=.02)
            for short, side in (("True", "short"), ("False", "long")):
                subgroup = [t for t in trades if t["is_short"] == short]
                assert len(subgroup) == metrics["side_breakdown"][side]["trades"]
                assert sum(float(t["profit_abs"]) for t in subgroup) == pytest.approx(metrics["side_breakdown"][side]["net_pnl_usdt"], abs=.02)


def test_data_coverage_is_not_misrepresented_as_funding_certification():
    provenance = read("provenance.json")
    for data in provenance["data"].values():
        assert data["certification_funding_complete"] is False
        assert set(data["candles"]) == {"1m", "15m", "1h"}
        for timeframe, expected in (("1m", 295200), ("15m", 19680), ("1h", 4920)):
            candles = data["candles"][timeframe]
            assert candles["bars"] == candles["expected_bars"] == expected
            assert candles["missing"] == candles["duplicates"] == 0
            assert not candles["invalid_ohlcv"]
        assert data["candles"]["15m"]["off_grid"] == 0
        assert data["funding"][0]["first"] == "2026-06-08 08:00:00+00:00"
