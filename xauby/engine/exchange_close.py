"""Pure helpers for exchange-authoritative derivative close reconciliation."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional, Tuple


def _timestamp_ms(value: Any) -> int:
    if value in (None, ""):
        return 0
    if isinstance(value, (int, float)):
        number = float(value)
        return int(number if number >= 1e12 else number * 1000)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)
    except (TypeError, ValueError):
        return 0


def reconciliation_key(exchange_id: str, symbol: str, state: Dict[str, Any]) -> str:
    """Return a stable key for one locally tracked position lifecycle."""
    identity = "|".join(
        (
            str(exchange_id or "unknown").lower(),
            str(symbol or "").upper().replace("_", ""),
            str(state.get("opened_at") or ""),
            str(state.get("position_side") or "LONG").upper(),
            f"{float(state.get('entry_price') or 0.0):.12g}",
            f"{float(state.get('quantity') or 0.0):.12g}",
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def match_position_history(
    state: Dict[str, Any],
    rows: Iterable[Dict[str, Any]],
    *,
    exchange_close_id: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Match one completed exchange position without guessing ambiguously."""
    candidates = [
        dict(row)
        for row in rows
        if str(row.get("close_type") or "") in {"2", "3", "4", "5"}
    ]
    if exchange_close_id:
        exact = [
            row
            for row in candidates
            if str(row.get("exchange_close_id") or "") == exchange_close_id
        ]
        if len(exact) == 1:
            return exact[0], ""
        return None, (
            "exchange close id was not found"
            if not exact
            else "exchange close id matched multiple records"
        )

    local_side = str(state.get("position_side") or "").upper()
    if local_side in {"LONG", "SHORT"}:
        candidates = [
            row
            for row in candidates
            if str(row.get("position_side") or "").upper() == local_side
        ]
    if not candidates:
        return None, "no completed position history matched the local side"

    opened_ms = _timestamp_ms(state.get("opened_at"))
    if opened_ms:
        # Local state is written just after the opening fill. A 30-minute
        # tolerance covers delayed persistence without crossing normal trades.
        timed = [
            row
            for row in candidates
            if abs(int(row.get("opened_timestamp") or 0) - opened_ms) <= 30 * 60 * 1000
        ]
        if timed:
            candidates = timed
        else:
            return None, "no position history matched the local opened_at timestamp"

    local_position_id = str(state.get("exchange_position_id") or "")
    if local_position_id:
        same_id = [
            row
            for row in candidates
            if str(row.get("exchange_position_id") or "") == local_position_id
        ]
        if not same_id:
            return None, "no position history matched the exchange position id"
        candidates = same_id

    local_entry = float(state.get("entry_price") or 0.0)
    if local_entry > 0:
        priced = [
            row
            for row in candidates
            if float(row.get("entry_price") or 0.0) > 0
            and abs(float(row["entry_price"]) - local_entry) / local_entry <= 0.02
        ]
        if not priced:
            return None, "no position history matched the local entry price"
        candidates = priced

    if len(candidates) != 1:
        return None, f"position history match is ambiguous ({len(candidates)} candidates)"
    return candidates[0], ""


def build_confirmed_trade(
    state: Dict[str, Any],
    history: Dict[str, Any],
    *,
    symbol: str,
    strategy_name: str,
    execution_mode: str = "live",
    prior_trades: Iterable[Dict[str, Any]] = (),
    trigger: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the remaining closed-trade leg from cumulative exchange totals."""
    opened_at = state.get("opened_at") or history.get("opened_at")
    opened_ms = _timestamp_ms(opened_at)
    exchange_close_id = str(history.get("exchange_close_id") or "")
    related = []
    for trade in prior_trades:
        if exchange_close_id and str(trade.get("exchange_close_id") or "") == exchange_close_id:
            continue
        trade_opened_ms = _timestamp_ms(trade.get("opened_at"))
        if opened_ms and trade_opened_ms and abs(trade_opened_ms - opened_ms) <= 1000:
            related.append(trade)
    previous_pnl = sum(float(trade.get("net_pnl") or 0.0) for trade in related)
    previous_fees = sum(float(trade.get("total_fees") or 0.0) for trade in related)
    previous_funding = sum(float(trade.get("funding_fee") or 0.0) for trade in related)

    realized_total = float(history.get("realized_pnl") or 0.0)
    fee_total = float(history.get("fee_cost") or 0.0)
    funding_total = float(history.get("funding_fee") or 0.0)
    net_pnl = realized_total - previous_pnl
    total_fees = fee_total - previous_fees
    funding_fee = funding_total - previous_funding

    quantity = float(state.get("quantity") or history.get("quantity") or 0.0)
    entry_price = float(history.get("entry_price") or state.get("entry_price") or 0.0)
    exit_price = float(history.get("exit_price") or 0.0)
    entry_cost = quantity * entry_price
    net_pnl_pct = (net_pnl / entry_cost * 100.0) if entry_cost > 0 else 0.0
    side = str(history.get("position_side") or state.get("position_side") or "LONG").upper()

    return {
        "symbol": str(symbol).upper().replace("_", ""),
        "side": "SHORT" if side == "SHORT" else "BUY",
        "amount": quantity,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "entry_cost": entry_cost,
        "gross_exit": quantity * exit_price,
        "entry_fee": 0.0,
        "exit_fee": total_fees,
        "total_fees": total_fees,
        "funding_fee": funding_fee,
        "net_pnl": net_pnl,
        "net_pnl_pct": net_pnl_pct,
        "trigger": trigger or "Exchange-confirmed position close",
        "opened_at": opened_at,
        "closed_at": history.get("closed_at"),
        "entry_regime": None,
        "exit_regime": None,
        "strategy_name": strategy_name,
        "execution_mode": execution_mode,
        "exchange_close_id": history.get("exchange_close_id"),
        "exchange_position_id": history.get("exchange_position_id"),
        "pnl_source": f"{history.get('exchange') or 'exchange'}_positions_history",
        "pnl_confirmed": True,
    }
