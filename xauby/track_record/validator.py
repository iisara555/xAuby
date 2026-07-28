"""Self-audit: replay the event log and check the trade table against it.

Roadmap P1.6. The audit existed but had never been run outside a unit test, and
it could not have survived contact with the current runtime, which trades two
pairs at once:

* It tracked **one** ``active_position`` for the whole account. With XAU and BTC
  both open, every second ``position_opened`` raised "fired while a position was
  already open" — a false anomaly — and the next ``position_closed`` was paired
  with whichever pair happened to be open, silently reconstructing a trade that
  never existed (BTC's exit against XAU's entry).
* It compared reconstructed trade *i* against database trade *i* across a merged,
  multi-symbol list, so a single extra event on one pair shifted every later
  comparison and produced a cascade of price mismatches.

Both are fixed by doing the whole thing per symbol. The audit also now takes an
epoch, because the OKX migration renamed ``XAUTUSDT`` to ``XAUUSDT`` and trades
either side of it are not the same instrument.
"""
from typing import Any, Dict, List, Optional, Tuple

from xauby.database.db import LiteDB

#: Official start of the track record. Before this the gold pair traded as
#: XAUTUSDT on a different venue, so earlier trades are not comparable and are
#: excluded rather than blended into the same numbers.
TRACK_RECORD_EPOCH = "2026-07-20"

PRICE_TOLERANCE = 0.01
QUANTITY_TOLERANCE = 0.0001


def _symbol_of(record: Dict[str, Any]) -> str:
    return str(record.get("symbol") or "").upper().replace("_", "")


def _after_epoch(timestamp: Any, epoch: Optional[str]) -> bool:
    if not epoch:
        return True
    return str(timestamp or "") >= str(epoch)


def audit_track_record(
    db: LiteDB,
    *,
    epoch: Optional[str] = TRACK_RECORD_EPOCH,
    limit: int = 10000,
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Reconstruct trades from position events and compare with the trade table.

    Returns ``(success, discrepancies, stats)``. Every discrepancy names the
    symbol it belongs to, because "trade index 4 mismatched" is unactionable
    once more than one pair is running.
    """
    discrepancies: List[str] = []

    opened_events = db.query_events(event_type="position_opened", limit=limit, order="asc")
    restored_events = db.query_events(
        event_type="position_restored", limit=limit, order="asc"
    )
    closed_events = db.query_events(event_type="position_closed", limit=limit, order="asc")
    all_events = sorted(
        list(opened_events) + list(restored_events) + list(closed_events),
        key=lambda e: (str(e.get("ts") or ""), int(e.get("seq") or 0)),
    )
    all_events = [e for e in all_events if _after_epoch(e.get("ts"), epoch)]

    db_trades = [
        trade for trade in db.get_closed_trades(None, limit=limit)
        if _after_epoch(trade.get("closed_at"), epoch)
    ]

    # One open position per symbol, not one for the account.
    active: Dict[str, Dict[str, Any]] = {}
    reconstructed: Dict[str, List[Dict[str, Any]]] = {}
    epoch_seeded_symbols: set[str] = set()

    # A calendar cutover can fall in the middle of a real position.  The close
    # belongs to the post-epoch track record, but its open event was correctly
    # filtered out above.  Seed that boundary position from the durable trade
    # row so the first close does not shift every later comparison.  Multiple
    # rows with the same entry represent partial closes of one position.
    if epoch:
        for trade in db_trades:
            opened_at = str(trade.get("opened_at") or "")
            closed_at = str(trade.get("closed_at") or "")
            if not opened_at or not (opened_at < str(epoch) <= closed_at):
                continue
            symbol = _symbol_of(trade)
            entry_price = float(trade.get("entry_price") or 0.0)
            amount = float(trade.get("amount") or 0.0)
            current = active.get(symbol)
            if current is None:
                epoch_seeded_symbols.add(symbol)
                active[symbol] = {
                    "run_id": "epoch-seed",
                    "opened_at": opened_at,
                    "entry_price": entry_price,
                    "amount": amount,
                    "symbol": symbol,
                }
            elif (
                current["opened_at"] == opened_at
                and abs(current["entry_price"] - entry_price) <= PRICE_TOLERANCE
            ):
                current["amount"] += amount
            else:
                discrepancies.append(
                    f"[{symbol}] multiple positions overlap audit epoch {epoch}"
                )

    for event in all_events:
        etype = event.get("event_type")
        payload = event.get("payload") or {}
        symbol = _symbol_of(event)

        if etype in {"position_opened", "position_restored"}:
            entry_price = float(
                payload.get("entry_price") or payload.get("entry")
                or payload.get("price") or 0.0
            )
            amount = float(
                payload.get("quantity") or payload.get("amount")
                or payload.get("qty") or 0.0
            )
            if etype == "position_restored" and symbol in active:
                current = active[symbol]
                if entry_price:
                    if current["entry_price"] <= 0:
                        current["entry_price"] = entry_price
                    elif abs(entry_price - current["entry_price"]) > PRICE_TOLERANCE:
                        discrepancies.append(
                            f"[{symbol}] position_restored entry mismatch "
                            f"({entry_price:.2f} vs active {current['entry_price']:.2f})"
                        )
                if amount:
                    if current["amount"] <= 0:
                        current["amount"] = amount
                    elif abs(amount - current["amount"]) > QUANTITY_TOLERANCE:
                        discrepancies.append(
                            f"[{symbol}] position_restored quantity mismatch "
                            f"({amount:.4f} vs active {current['amount']:.4f})"
                        )
                continue
            if symbol in active:
                discrepancies.append(
                    f"[{symbol}] position_opened (run {event.get('run_id')}) fired "
                    f"while {symbol} already had a position open "
                    f"(run {active[symbol].get('run_id')})"
                )
            active[symbol] = {
                "run_id": event.get("run_id"),
                "opened_at": payload.get("opened_at") or event.get("ts"),
                "entry_price": entry_price,
                "amount": amount,
                "symbol": symbol,
            }
        elif etype == "position_closed":
            is_partial = bool(payload.get("partial"))
            position = active.get(symbol)
            if position is None:
                discrepancies.append(
                    f"[{symbol}] position_closed (run {event.get('run_id')}) fired "
                    f"with no open position for that symbol"
                )
                continue
            closed_amount = float(
                payload.get("quantity") or payload.get("amount")
                or payload.get("qty") or position["amount"]
            )
            if is_partial and not any(
                payload.get(key) is not None for key in ("quantity", "amount", "qty")
            ):
                discrepancies.append(
                    f"[{symbol}] legacy partial close has no closed quantity"
                )
            reconstructed.setdefault(symbol, []).append({
                "symbol": symbol,
                "amount": closed_amount,
                "entry_price": position["entry_price"],
                "exit_price": float(
                    payload.get("exit_price") or payload.get("exit")
                    or payload.get("price") or 0.0
                ),
                "opened_at": position["opened_at"],
                "closed_at": event.get("ts"),
            })
            if is_partial:
                position["amount"] = float(
                    payload.get("remaining_quantity")
                    if payload.get("remaining_quantity") is not None
                    else max(0.0, position["amount"] - closed_amount)
                )
            else:
                active.pop(symbol, None)

    # Whatever is still in `active` is a position open right now. That is not a
    # discrepancy — it simply has no closed trade to match — so it is reported
    # in stats["open_at_audit"] and left out of the counts.

    db_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    for trade in db_trades:
        db_by_symbol.setdefault(_symbol_of(trade), []).append(trade)
    for trades in db_by_symbol.values():
        trades.sort(key=lambda t: str(t.get("closed_at") or ""))

    matches = 0
    total_reconstructed = 0
    total_db = 0
    per_symbol: Dict[str, Dict[str, int]] = {}

    for symbol in sorted(set(reconstructed) | set(db_by_symbol)):
        from_events = reconstructed.get(symbol, [])
        from_db = db_by_symbol.get(symbol, [])
        total_reconstructed += len(from_events)
        total_db += len(from_db)
        symbol_matches = 0

        if len(from_events) != len(from_db):
            discrepancies.append(
                f"[{symbol}] count mismatch: {len(from_events)} trades "
                f"reconstructed from events, {len(from_db)} in the database"
            )

        # Aligned within the symbol. Comparing across symbols by position, as
        # this did before, means one extra event on one pair shifts every later
        # comparison on every other pair.
        for index in range(min(len(from_events), len(from_db))):
            event_trade = from_events[index]
            db_trade = from_db[index]
            price_diff = abs(event_trade["exit_price"]
                             - float(db_trade.get("exit_price") or 0.0))
            qty_diff = abs(event_trade["amount"]
                           - float(db_trade.get("amount") or 0.0))
            if price_diff > PRICE_TOLERANCE:
                discrepancies.append(
                    f"[{symbol}] trade {index}: exit price mismatch "
                    f"(events {event_trade['exit_price']:.2f}, "
                    f"db {float(db_trade.get('exit_price') or 0.0):.2f})"
                )
            if qty_diff > QUANTITY_TOLERANCE:
                discrepancies.append(
                    f"[{symbol}] trade {index}: quantity mismatch "
                    f"(events {event_trade['amount']:.4f}, "
                    f"db {float(db_trade.get('amount') or 0.0):.4f})"
                )
            if price_diff <= PRICE_TOLERANCE and qty_diff <= QUANTITY_TOLERANCE:
                symbol_matches += 1

        matches += symbol_matches
        per_symbol[symbol] = {
            "reconstructed": len(from_events),
            "database": len(from_db),
            "matching": symbol_matches,
        }

    stats = {
        "epoch": epoch or "",
        "events_analyzed": len(all_events),
        "reconstructed_count": total_reconstructed,
        "database_count": total_db,
        "matching_trades": matches,
        "discrepancies_count": len(discrepancies),
        "symbols": sorted(per_symbol),
        "per_symbol": per_symbol,
        "open_at_audit": sorted(active),
        "epoch_seeded_symbols": sorted(epoch_seeded_symbols),
    }
    return len(discrepancies) == 0, discrepancies, stats
