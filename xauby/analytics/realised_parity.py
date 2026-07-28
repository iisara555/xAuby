"""Reconcile realised live execution with the simulator's cost assumptions.

This is deliberately execution-conditioned: it holds the actual entry/exit
decision fixed, reconstructs the pre-cost result, and applies the fee and
slippage assumptions used by replay/backtest.  The report therefore answers
the operational question Phase 1.7 left open — whether live fills/costs turn a
simulated edge into a materially different realised result — without running a
heavy historical backtest on the trading VPS.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping, Optional


def _payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, Mapping) else {}


def _symbol(value: Any) -> str:
    return str(value or "").upper().replace("_", "")


def _slippage_cycles(
    events: Iterable[Mapping[str, Any]],
) -> Dict[str, List[tuple[Optional[float], Optional[float]]]]:
    """Entry/exit slippage observations in lifecycle order per symbol."""
    active_entry: Dict[str, Optional[float]] = {}
    cycles: Dict[str, List[tuple[Optional[float], Optional[float]]]] = defaultdict(list)
    ordered = sorted(
        events,
        key=lambda event: (str(event.get("ts") or ""), int(event.get("seq") or 0)),
    )
    for event in ordered:
        etype = str(event.get("event_type") or event.get("event") or "")
        symbol = _symbol(event.get("symbol"))
        if not symbol:
            continue
        payload = _payload(event)
        if etype == "position_opened":
            raw = payload.get("slippage_bps")
            active_entry[symbol] = float(raw) if raw is not None else None
        elif etype == "position_restored":
            # The entry happened in a prior run, so its execution observation
            # is unavailable rather than zero *unless* the retained event
            # window already contains the original open observation.
            active_entry.setdefault(symbol, None)
        elif etype == "position_closed":
            raw = payload.get("slippage_bps")
            exit_bps = float(raw) if raw is not None else None
            cycles[symbol].append((active_entry.get(symbol), exit_bps))
            if not bool(payload.get("partial")):
                active_entry.pop(symbol, None)
    return dict(cycles)


def build_realised_parity_report(
    trades: Iterable[Mapping[str, Any]],
    events: Iterable[Mapping[str, Any]],
    *,
    fee_rates: Mapping[str, float],
    model_slippage_bps: float,
    tolerance_bps: float = 10.0,
) -> Dict[str, Any]:
    """Return per-trade and aggregate realised-vs-model cost parity."""
    trade_rows = sorted(
        [dict(trade) for trade in trades],
        key=lambda trade: str(trade.get("closed_at") or ""),
    )
    cycles = _slippage_cycles(events)
    cycle_index: Dict[str, int] = defaultdict(int)
    rows: List[Dict[str, Any]] = []

    totals = {
        "actual_net_pnl": 0.0,
        "model_net_pnl": 0.0,
        "actual_fees": 0.0,
        "model_fees": 0.0,
        "actual_slippage_cost": 0.0,
        "model_slippage_cost": 0.0,
        "funding_fee": 0.0,
        "notional": 0.0,
    }
    complete_slippage = 0

    for trade in trade_rows:
        symbol = _symbol(trade.get("symbol"))
        amount = abs(float(trade.get("amount") or 0.0))
        entry_price = abs(float(trade.get("entry_price") or 0.0))
        exit_price = abs(float(trade.get("exit_price") or 0.0))
        entry_notional = amount * entry_price
        exit_notional = amount * exit_price
        round_trip_notional = entry_notional + exit_notional
        fee_rate = float(fee_rates.get(symbol, fee_rates.get("*", 0.0)) or 0.0)
        actual_fee = abs(float(trade.get("total_fees") or 0.0))
        funding = abs(float(trade.get("funding_fee") or 0.0))
        actual_net = float(trade.get("net_pnl") or 0.0)

        index = cycle_index[symbol]
        observations = cycles.get(symbol) or []
        entry_bps, exit_bps = observations[index] if index < len(observations) else (None, None)
        cycle_index[symbol] += 1
        slippage_complete = entry_bps is not None and exit_bps is not None
        if slippage_complete:
            complete_slippage += 1
        actual_slippage = (
            entry_notional * abs(float(entry_bps or 0.0)) / 10_000.0
            + exit_notional * abs(float(exit_bps or 0.0)) / 10_000.0
        )
        model_fee = round_trip_notional * fee_rate
        model_slippage = (
            round_trip_notional * max(0.0, float(model_slippage_bps)) / 10_000.0
        )

        # Actual net already contains actual fill slippage. Add that cost back
        # to recover the common pre-execution baseline, then apply the model's
        # cost assumptions.
        pre_execution_pnl = actual_net + actual_fee + funding + actual_slippage
        model_net = pre_execution_pnl - model_fee - funding - model_slippage
        variance = actual_net - model_net

        row = {
            "symbol": symbol,
            "closed_at": trade.get("closed_at"),
            "actual_net_pnl": actual_net,
            "model_net_pnl": model_net,
            "variance_pnl": variance,
            "actual_fees": actual_fee,
            "model_fees": model_fee,
            "entry_slippage_bps": entry_bps,
            "exit_slippage_bps": exit_bps,
            "actual_slippage_cost": actual_slippage if slippage_complete else None,
            "model_slippage_cost": model_slippage,
            "funding_fee": funding,
            "slippage_evidence_complete": slippage_complete,
            "round_trip_notional": round_trip_notional,
        }
        rows.append(row)
        for key in totals:
            if key == "notional":
                totals[key] += round_trip_notional
            elif key == "actual_net_pnl":
                totals[key] += actual_net
            elif key == "model_net_pnl":
                totals[key] += model_net
            elif key == "actual_fees":
                totals[key] += actual_fee
            elif key == "model_fees":
                totals[key] += model_fee
            elif key == "actual_slippage_cost":
                totals[key] += actual_slippage
            elif key == "model_slippage_cost":
                totals[key] += model_slippage
            elif key == "funding_fee":
                totals[key] += funding

    variance = totals["actual_net_pnl"] - totals["model_net_pnl"]
    variance_bps = (
        abs(variance) / totals["notional"] * 10_000.0
        if totals["notional"] > 0 else 0.0
    )
    coverage_pct = (
        complete_slippage / len(rows) * 100.0 if rows else 100.0
    )
    if not rows:
        status = "no_trades"
    elif complete_slippage != len(rows):
        status = "insufficient_evidence"
    elif variance_bps > float(tolerance_bps):
        status = "check"
    else:
        status = "pass"

    return {
        "status": status,
        "trade_count": len(rows),
        "slippage_coverage_pct": coverage_pct,
        "tolerance_bps": float(tolerance_bps),
        "variance_bps": variance_bps,
        "totals": {**totals, "variance_pnl": variance},
        "trades": rows,
        "basis": (
            "execution-conditioned: actual entry/exit decisions with model "
            "fee/slippage assumptions"
        ),
    }
