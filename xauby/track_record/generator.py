from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from xauby.track_record.models import TrackRecordEntry, PerformanceReport
from xauby.analytics.calculator import calculate_metrics, trade_span_years

def _parse_date(date_str: Any) -> datetime:
    if not date_str:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        # Strip Z if present and parse
        ds = str(date_str).replace("Z", "")
        # Add timezone info
        return datetime.fromisoformat(ds).replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.fromtimestamp(0, tz=timezone.utc)

def generate_report(
    trades: List[Dict[str, Any]],
    period_days: int,
    report_name: str,
    *,
    initial_balance: float = 1000.0,
    as_of: Optional[datetime] = None,
    period_start: Optional[datetime] = None,
) -> PerformanceReport:
    """Generate a performance report for a specific period (in days) from the trades list."""
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = period_start or (now - timedelta(days=period_days))
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)

    filtered_trades = []
    for t in trades:
        closed_at = t.get("closed_at_dt") or _parse_date(t.get("closed_at"))
        if closed_at >= cutoff:
            filtered_trades.append(t)

    # Use analytics calculator to get metrics
    metrics = calculate_metrics(
        filtered_trades,
        initial_balance=initial_balance,
        years=trade_span_years(filtered_trades),
    )

    # Running drawdown at each trade's close, chronologically. The field used to
    # be hardcoded to 0.0 under a comment saying it was "calculated
    # chronologically at entry level" — it was not calculated anywhere, so every
    # entry in every published track record reported no drawdown.
    chronological = sorted(
        filtered_trades,
        key=lambda t: t.get("closed_at_dt") or _parse_date(t.get("closed_at")),
    )
    running_drawdown: Dict[int, float] = {}
    equity = float(initial_balance)
    peak = float(initial_balance)
    for t in chronological:
        equity += float(t.get("net_pnl", 0.0))
        peak = max(peak, equity)
        # Relative to the peak equity reached so far, including the real
        # starting balance supplied by the runtime metrics context.
        running_drawdown[id(t)] = (
            (peak - equity) / peak * 100.0 if peak > 0 else 0.0
        )

    # Convert trades to TrackRecordEntry instances
    entries = []
    total_duration_seconds = 0.0
    for t in filtered_trades:
        opened_at = t.get("opened_at_dt") or _parse_date(t.get("opened_at"))
        closed_at = t.get("closed_at_dt") or _parse_date(t.get("closed_at"))

        duration = (closed_at - opened_at).total_seconds()
        total_duration_seconds += max(0.0, duration)

        pnl = float(t.get("net_pnl", 0.0))
        entry_cost = float(t.get("entry_cost", 1.0)) or 1.0
        ret_pct = float(t.get("net_pnl_pct", 0.0)) or (pnl / entry_cost * 100.0)

        entries.append(TrackRecordEntry(
            strategy_name=str(t.get("strategy_name") or t.get("trigger") or "xauby_actionzone"),
            symbol=str(t.get("symbol") or "XAUTUSDT"),
            entry_time=str(t.get("opened_at") or ""),
            exit_time=str(t.get("closed_at") or ""),
            pnl=pnl,
            return_pct=round(ret_pct, 4),
            drawdown_pct=round(running_drawdown.get(id(t), 0.0), 4),
            duration_seconds=duration,
            regime_at_entry=str(t.get("entry_regime") or "NORMAL"),
            regime_at_exit=str(t.get("exit_regime") or "NORMAL"),
            mae_pct=round(float(t.get("mae_pct") or 0.0), 4),
            mfe_pct=round(float(t.get("mfe_pct") or 0.0), 4),
            excursion_measured=bool(t.get("excursion_measured", False)),
        ))

    # Calculate average duration
    avg_duration_hours = (total_duration_seconds / len(filtered_trades) / 3600.0) if filtered_trades else 0.0

    return PerformanceReport(
        report_name=report_name,
        period_days=period_days,
        total_trades=len(filtered_trades),
        win_rate=round(metrics.win_rate, 2),
        net_pnl=round(metrics.net_pnl, 4),
        profit_factor=round(metrics.profit_factor, 2),
        max_drawdown_pct=round(metrics.max_drawdown_pct, 2),
        average_duration_hours=round(avg_duration_hours, 2),
        entries=entries,
        average_mae_pct=round(metrics.average_mae_pct, 4),
        average_mfe_pct=round(metrics.average_mfe_pct, 4),
        excursion_coverage_pct=round(metrics.excursion_coverage_pct, 2),
    )

def generate_comparison_report(
    trades: List[Dict[str, Any]], *, initial_balance: float = 1000.0
) -> Dict[str, PerformanceReport]:
    """Group trades by strategy_name (or trigger) and generate a performance report for each."""
    by_strategy: Dict[str, List[Dict[str, Any]]] = {}
    for t in trades:
        strat = t.get("strategy_name")
        if not strat:
            # Fallback for legacy trades
            trigger = str(t.get("trigger") or "")
            if "Stop Loss" in trigger or "ZONE_RED" in trigger:
                strat = "xauby_actionzone"
            else:
                strat = trigger or "xauby_actionzone"
        by_strategy.setdefault(strat, []).append(t)

    reports = {}
    for strat, strat_trades in by_strategy.items():
        reports[strat] = generate_report(
            strat_trades,
            3650,
            f"Strategy Comparison: {strat}",
            initial_balance=initial_balance,
        )
    return reports
