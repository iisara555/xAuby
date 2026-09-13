"""Read-only audit of completed native Community 15m artifacts; no simulation.

CLI prints JSON to stdout. It does not run Freqtrade, fetch data, write files,
or import the trading runtime, and is safe for focused offline verification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from scripts.freqtrade_community_research import load_result


def utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def in_session(clock: datetime) -> bool:
    # Intentionally independent of the strategy's pandas calendar implementation.
    from zoneinfo import ZoneInfo

    return any((local := clock.astimezone(ZoneInfo(zone))).weekday() < 5 and 8 <= local.hour < 12
               for zone in ("Europe/London", "America/New_York"))


def audit_run(data: dict, candidate: dict, window: str, cost: str, protocol: dict) -> dict:
    issues = []

    def check(ok, message):
        if not ok:
            issues.append(message)

    bounds = protocol["windows"][window].split("-")
    start, end = (datetime.strptime(day, "%Y%m%d").replace(tzinfo=timezone.utc) for day in bounds)
    fee = protocol["execution"][f"{cost}_cost_per_side"]
    check(utc(data["backtest_start"]) == start and utc(data["backtest_end"]) == end, "changed window")
    check(data["timeframe"] == "15m" and data["timeframe_detail"] == "1m", "wrong execution/detail timeframe")
    check(data["max_open_trades"] == 1, "wrong simultaneous-trade limit")
    check(data["starting_balance"] == protocol["execution"]["initial_cash_usdt"], "wrong initial cash")
    check(data["stake_amount"] == protocol["execution"]["proposed_stake_usdt"], "wrong proposed stake")
    check(data["stoploss"] == -.02, "wrong strategy stop")
    trades = sorted(data["trades"], key=lambda t: t["open_date"])
    check(len(trades) == data["total_trades"], "trade count does not reconcile")
    wallet = data["starting_balance"]
    previous_close = start
    max_pnl_error = 0.0
    total_fees = 0.0
    for number, trade in enumerate(trades):
        opened, closed = utc(trade["open_date"]), utc(trade["close_date"])
        prefix = f"trade {number}: "
        check(start <= opened < end and opened <= closed <= end, prefix + "out-of-window trade")
        check(opened >= previous_close, prefix + "overlapping trades")
        check(not trade["is_open"], prefix + "unclosed trade")
        check(trade["pair"] == candidate["pair"], prefix + "wrong pair")
        check(trade["fee_open"] == trade["fee_close"] == fee, prefix + "wrong fees")
        check(trade["leverage"] == 1, prefix + "leverage changed")
        check(trade["initial_stop_loss_ratio"] == -.02, prefix + "stop changed")
        cap = min(2500, .25 * wallet)
        check(0 < trade["stake_amount"] <= trade["max_stake_amount"] <= cap + .01, prefix + "stake cap exceeded")
        check(sum(bool(o.get("ft_is_entry")) for o in trade["orders"]) == 1, prefix + "position additions")
        check((closed - opened).total_seconds() <= 48 * 3600, prefix + "holding time exceeded")
        if candidate["id"].endswith("-session"):
            check(in_session(opened), prefix + "entry outside fixed session")
        numeric = [trade[k] for k in ("amount", "open_rate", "close_rate", "profit_abs", "funding_fees")]
        check(all(math.isfinite(value) for value in numeric), prefix + "non-finite native fields")
        gross = trade["amount"] * (trade["close_rate"] - trade["open_rate"]) * (-1 if trade["is_short"] else 1)
        trade_fees = trade["amount"] * (trade["open_rate"] * fee + trade["close_rate"] * fee)
        pnl_error = abs(gross - trade_fees + trade["funding_fees"] - trade["profit_abs"])
        check(pnl_error <= .02, prefix + "price/fee/funding PnL mismatch")
        max_pnl_error = max(max_pnl_error, pnl_error)
        total_fees += trade_fees
        wallet += trade["profit_abs"]
        previous_close = closed
    pnl = wallet - data["starting_balance"]
    check(abs(wallet - data["final_balance"]) <= .02, "final wallet mismatch")
    check(abs(pnl - data["profit_total_abs"]) <= .02, "ledger PnL mismatch")
    check(abs(pnl / data["starting_balance"] - data["profit_total"]) <= .000002, "return mismatch")
    return {"run": f"{candidate['id']}_{window}_{cost}", "trades": len(trades),
            "starting_balance": data["starting_balance"], "final_balance": data["final_balance"],
            "net_pnl_usdt": pnl, "fees_including_friction_proxy_usdt": total_fees,
            "funding_fees_usdt": sum(t["funding_fees"] for t in trades),
            "max_trade_pnl_recompute_error_usdt": max_pnl_error, "issues": issues}


def audit_artifact(out: Path) -> dict:
    protocol = json.loads((out / "protocol.json").read_text())
    summary = json.loads((out / "summary.json").read_text())
    audits = []
    expected_ids = {row["id"] for row in protocol["candidates"]}
    if {row["id"] for row in summary["candidates"]} != expected_ids:
        raise ValueError("Missing or unexpected candidates")
    for candidate in protocol["candidates"]:
        record = next(row for row in summary["candidates"] if row["id"] == candidate["id"])
        for cost in ("base", "stress"):
            if cost == "stress" and "stress" not in record:
                continue
            for window in protocol["windows"]:
                directory = out / "results" / f"{candidate['id']}_{window}_{cost}"
                data = load_result(directory, candidate["strategy"])
                audit = audit_run(data, candidate, window, cost, protocol)
                audit["native_archive_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                                  for p in sorted(directory.glob("*.zip"))}
                audits.append(audit)
    return {"study": protocol["id"], "read_only_native_audit": True,
            "all_checks_passed": all(not row["issues"] for row in audits),
            "runs": audits, "live_weight": 0, "certified": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    result = audit_artifact(args.artifact)
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
