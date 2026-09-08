"""Audit exported LONA trades, costs, timing and descriptive regime attribution.

Consumes completed reports only. Does not run a backtest or contact a broker.
Run from the repository root with --data-dir core/lona_15m_candidates.
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def timestamp(value):
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.timestamp())


def net_metrics(pnls):
    gains = sum(max(0, pnl) for pnl in pnls)
    losses = -sum(min(0, pnl) for pnl in pnls)
    return {
        "trades": len(pnls),
        "net_pnl": round(sum(pnls), 4),
        "profit_factor": round(gains / losses, 4) if losses else None,
        "win_rate_pct": round(100 * sum(pnl > 0 for pnl in pnls) / len(pnls), 2) if pnls else 0,
    }


def entry_regime(rows, index):
    """ER20 on the completed signal bar; not xAuby's production classifier."""
    end = index - 1
    closes = [float(row["close"]) for row in rows[end - 20:end + 1]]
    path = sum(abs(b - a) for a, b in zip(closes, closes[1:]))
    change = closes[-1] - closes[0]
    efficiency = abs(change) / path if path else 0
    if efficiency <= 0.25:
        return "range"
    if efficiency >= 0.30:
        return "directional_up" if change > 0 else "directional_down"
    return "transition"


def audit_report(report, run, candidate, rows, protocol):
    window = protocol["windows"][run["window"]]
    by_time = {timestamp(row["timestamp"]): (i, row) for i, row in enumerate(rows)}
    start = timestamp(dt.datetime.strptime(str(window["trade_start"]), "%Y%m%d").isoformat())
    end = timestamp(window["feed_end"])
    stats = report["totalStats"]
    if report["status"] != "COMPLETED" or stats["open_trades"] != 0:
        raise ValueError("report is incomplete or holds open trades")
    pnls, gross_pnl, costs = [], 0.0, 0.0
    sides, regimes, months = defaultdict(list), defaultdict(list), defaultdict(list)
    prior_exit = 0
    for trade in report["trades"]:
        entry, exit_ = timestamp(trade["entry_time"]), timestamp(trade["exit_time"])
        if not start <= entry < exit_ < end or entry < prior_exit:
            raise ValueError("warmup/window leak, invalid timing or overlapping trades")
        entry_index, entry_row = by_time[entry]
        _, exit_row = by_time[exit_]
        for actual, expected in ((trade["entry_price"], entry_row["open"]), (trade["exit_price"], exit_row["open"])):
            if not math.isclose(float(actual), float(expected), abs_tol=1e-7):
                raise ValueError("fill does not match the reported next-bar open")
        if trade["direction"] not in ("LONG", "SHORT"):
            raise ValueError("unknown trade direction")
        qty = abs(float(trade["quantity"]))
        units = qty / candidate["step"]
        if not math.isclose(units, round(units), abs_tol=1e-6):
            raise ValueError("quantity violates the recorded OKX contract step")
        side = 1 if trade["direction"] == "LONG" else -1
        gross = side * qty * (trade["exit_price"] - trade["entry_price"])
        fee = qty * (trade["entry_price"] + trade["exit_price"]) * run["commission"]
        if not math.isclose(gross, trade["pnl"], abs_tol=0.02):
            raise ValueError("gross trade PnL mismatch")
        pnl = gross - fee
        gross_pnl += gross
        costs += fee
        pnls.append(pnl)
        sides[trade["direction"]].append(pnl)
        regimes[entry_regime(rows, entry_index)].append(pnl)
        months[trade["exit_time"][:7]].append(pnl)
        prior_exit = exit_
    metrics = net_metrics(pnls)
    expected_net = stats["final_portfolio_value"] - protocol["execution"]["initial_cash_usdt"]
    if len(pnls) != stats["number_of_trades"] or abs(sum(pnls) - expected_net) > 0.02:
        raise ValueError("trade ledger does not reconcile to portfolio equity")
    return {
        "report_id": report["id"],
        "audit_pass": True,
        "net_return_pct": round(100 * sum(pnls) / protocol["execution"]["initial_cash_usdt"], 4),
        "gross_pnl": round(gross_pnl, 4),
        "modeled_costs": round(costs, 4),
        "max_drawdown_pct": stats["maximum_drawdown_percentage"],
        "drawdown_source": "LONA marked-to-market summary; trade ledger alone cannot rederive intrabar drawdown",
        "open_trades": stats["open_trades"],
        **metrics,
        "by_side": {key: net_metrics(value) for key, value in sides.items()},
        "by_entry_regime": {key: net_metrics(value) for key, value in regimes.items()},
        "by_exit_month": {key: net_metrics(value) for key, value in months.items()},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    protocol = json.loads((ROOT / "protocol.json").read_text())
    registry = json.loads((ROOT / "registry.json").read_text())
    summaries = []
    for candidate in registry["candidates"]:
        csv_path = args.data_dir / f'{candidate["symbol"]}_OKX_15m.csv'
        expected_hash = registry["datasets"][candidate["symbol"]]["sha256"]
        if hashlib.sha256(csv_path.read_bytes()).hexdigest() != expected_hash:
            raise ValueError("dataset differs from locked artifact")
        with csv_path.open() as handle:
            rows = list(csv.DictReader(handle))
        results = {}
        for run in registry["runs"]:
            if run["key"] != candidate["key"]:
                continue
            raw = ROOT / "reports" / f'{run["report_id"]}.json'
            report = json.loads(raw.read_text())
            results[run["window"]] = audit_report(report, run, candidate, rows, protocol)
        dev, holdout = results["development"], results["holdout"]
        gate = protocol["selection"]["preliminary_gate"]
        reasons = []
        if dev["net_return_pct"] <= gate["development_net_return_min_exclusive"]:
            reasons.append("development net return is not positive")
        if holdout["net_return_pct"] <= gate["holdout_net_return_min_exclusive"]:
            reasons.append("holdout net return is not positive")
        if holdout["profit_factor"] is None or holdout["profit_factor"] < gate["holdout_profit_factor_min"]:
            reasons.append("holdout PF below 1.20 or undefined")
        if holdout["trades"] < gate["holdout_closed_trades_min"]:
            reasons.append("fewer than 30 holdout trades")
        if holdout["max_drawdown_pct"] > gate["holdout_drawdown_max_pct"]:
            reasons.append("holdout drawdown exceeds 10%")
        summaries.append({
            "key": candidate["key"], "symbol": candidate["symbol"], "family": candidate["family"],
            "status": "rejected_screen" if reasons else "preliminary_pass_needs_stress",
            "live_weight": 0, "reasons": reasons, "results": results,
        })
    output = {"protocol_id": protocol["id"], "regime_note": "Past-only ER20 descriptive attribution; not the production regime classifier", "candidates": summaries}
    (ROOT / "screen_results.json").write_text(json.dumps(output, indent=2) + "\n")
    for item in summaries:
        result = item["results"]["holdout"]
        print(item["key"], item["status"], {key: result[key] for key in ("net_return_pct", "profit_factor", "trades", "max_drawdown_pct")})


if __name__ == "__main__":
    main()
