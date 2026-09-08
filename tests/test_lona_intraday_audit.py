"""Research-result integrity checks; no backtest, data download or LONA call."""
from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

RESEARCH = Path(__file__).resolve().parents[1] / "docs/research/lona_15m_2026_09"
SPEC = spec_from_file_location("lona_intraday_audit", RESEARCH / "audit_results.py")
AUDIT = module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def fixture_report():
    import datetime as dt

    start = dt.datetime(2026, 7, 10, tzinfo=dt.timezone.utc)
    rows = [
        {"timestamp": (start + dt.timedelta(minutes=15 * i)).isoformat(), "open": 100.0, "close": 100.0}
        for i in range(23)
    ]
    rows[-1]["open"] = 100.1
    # A gross gain can become a net loss after per-side costs.
    report = {
        "id": "fixture", "status": "COMPLETED",
        "totalStats": {"open_trades": 0, "number_of_trades": 1, "final_portfolio_value": 9999.8999, "maximum_drawdown_percentage": 0.01},
        "trades": [{"direction": "LONG", "entry_time": rows[21]["timestamp"], "exit_time": rows[22]["timestamp"], "entry_price": 100.0, "exit_price": 100.1, "quantity": 1, "pnl": 0.1}],
    }
    run = {"window": "holdout", "commission": 0.001}
    candidate = {"step": 0.001}
    protocol = {"windows": {"holdout": {"trade_start": 20260710, "feed_end": "2026-09-08"}}, "execution": {"initial_cash_usdt": 10000}}
    return report, run, candidate, rows, protocol


def test_costs_are_deducted_from_gross_trade_pnl():
    result = AUDIT.audit_report(*fixture_report())
    assert result["audit_pass"]
    assert result["net_pnl"] == pytest.approx(-0.1001)
    assert result["profit_factor"] == 0
    assert result["win_rate_pct"] == 0


@pytest.mark.parametrize("fault", ["wrong_fill", "equity_mismatch", "open_position", "fractional_contract", "warmup_leak"])
def test_inconsistent_evidence_is_rejected(fault):
    args = list(deepcopy(fixture_report()))
    report = args[0]
    if fault == "wrong_fill":
        report["trades"][0]["entry_price"] = 99.0
    elif fault == "equity_mismatch":
        report["totalStats"]["final_portfolio_value"] += 1
    elif fault == "open_position":
        report["totalStats"]["open_trades"] = 1
    elif fault == "fractional_contract":
        report["trades"][0]["quantity"] = 1.0005
    else:
        args[4]["windows"]["holdout"]["trade_start"] = 20260711
    with pytest.raises(ValueError):
        AUDIT.audit_report(*args)
