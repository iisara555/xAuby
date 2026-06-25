from typing import List, Dict, Any, Tuple, Optional
from xauby.database.db import LiteDB

def audit_track_record(db: LiteDB) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Self-audits the track record database by replaying position lifecycle events.
    
    Returns:
        (success, discrepancies_list, stats_dict)
    """
    discrepancies = []
    
    # Query all position lifecycle events
    opened_events = db.query_events(event_type="position_opened", limit=10000, order="asc")
    closed_events = db.query_events(event_type="position_closed", limit=10000, order="asc")
    
    # Combine and sort chronologically
    all_events = sorted(opened_events + closed_events, key=lambda e: (e.get("ts", ""), e.get("seq", 0)))
    
    # Reconstruct trades from events
    reconstructed_trades: List[Dict[str, Any]] = []
    active_position: Optional[Dict[str, Any]] = None
    
    for ev in all_events:
        etype = ev.get("event_type")
        payload = ev.get("payload") or {}
        
        if etype == "position_opened":
            if active_position is not None:
                discrepancies.append(
                    f"Anomaly: position_opened event for run {ev.get('run_id')} "
                    f"fired while a position was already open (run {active_position.get('run_id')})."
                )
            active_position = {
                "run_id": ev.get("run_id"),
                "opened_at": ev.get("ts"),
                "entry_price": float(payload.get("entry_price") or payload.get("price") or 0.0),
                "amount": float(payload.get("quantity") or payload.get("amount") or 0.0),
                "symbol": ev.get("symbol")
            }
        elif etype == "position_closed":
            if active_position is None:
                discrepancies.append(
                    f"Anomaly: position_closed event for run {ev.get('run_id')} "
                    f"fired with no active open position."
                )
                continue
            
            # Record reconstructed closed trade
            reconstructed_trades.append({
                "symbol": active_position["symbol"],
                "amount": active_position["amount"],
                "entry_price": active_position["entry_price"],
                "exit_price": float(payload.get("exit_price") or payload.get("price") or 0.0),
                "opened_at": active_position["opened_at"],
                "closed_at": ev.get("ts")
            })
            active_position = None

    # Get actual closed trades from SQLite
    db_trades = db.get_closed_trades(limit=1000)
    # Sort actual trades oldest first to align with reconstructed
    db_trades_sorted = sorted(db_trades, key=lambda t: t.get("closed_at", ""))

    # Compare lists
    len_recon = len(reconstructed_trades)
    len_db = len(db_trades_sorted)
    
    if len_recon != len_db:
        discrepancies.append(
            f"Count mismatch: Reconstructed {len_recon} trades from event log, "
            f"but database has {len_db} closed trades."
        )

    # Cross-reference aligned entries
    aligned_count = min(len_recon, len_db)
    matches = 0
    for i in range(aligned_count):
        rt = reconstructed_trades[i]
        dt = db_trades_sorted[i]
        
        # Check tolerance on prices and amounts
        price_diff = abs(rt["exit_price"] - dt.get("exit_price", 0.0))
        qty_diff = abs(rt["amount"] - dt.get("amount", 0.0))
        
        if price_diff > 0.01:
            discrepancies.append(
                f"Trade index {i}: Exit price mismatch (Event: {rt['exit_price']:.2f}, DB: {dt.get('exit_price'):.2f})"
            )
        if qty_diff > 0.0001:
            discrepancies.append(
                f"Trade index {i}: Quantity mismatch (Event: {rt['amount']:.4f}, DB: {dt.get('amount'):.4f})"
            )
        
        if price_diff <= 0.01 and qty_diff <= 0.0001:
            matches += 1

    success = len(discrepancies) == 0
    
    stats = {
        "events_analyzed": len(all_events),
        "reconstructed_count": len_recon,
        "database_count": len_db,
        "matching_trades": matches,
        "discrepancies_count": len(discrepancies)
    }
    
    return success, discrepancies, stats
