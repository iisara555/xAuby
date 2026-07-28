from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from xauby.analytics.models import TradingPerformanceMetrics
from xauby.analytics.risk import MetricBasis, risk_from_trade_pnls


def position_excursions_pct(
    *,
    entry_price: float,
    highest_price_seen: float,
    lowest_price_seen: float,
    position_side: str,
    exit_price: Optional[float] = None,
) -> tuple[float, float]:
    """Return (MAE%, MFE%) from the observed range of one position.

    The entry and exit are included in the range so a same-tick close still has
    defensible evidence.  Values are positive magnitudes for both LONG/SHORT.
    """
    entry = float(entry_price or 0.0)
    if entry <= 0:
        return 0.0, 0.0
    observed = [entry]
    high = float(highest_price_seen or 0.0)
    low = float(lowest_price_seen or 0.0)
    exit_value = float(exit_price or 0.0)
    if high > 0:
        observed.append(high)
    if low > 0:
        observed.append(low)
    if exit_value > 0:
        observed.append(exit_value)
    observed_high = max(observed)
    observed_low = min(observed)
    if str(position_side or "LONG").upper() == "SHORT":
        mae = max(0.0, (observed_high - entry) / entry * 100.0)
        mfe = max(0.0, (entry - observed_low) / entry * 100.0)
    else:
        mae = max(0.0, (entry - observed_low) / entry * 100.0)
        mfe = max(0.0, (observed_high - entry) / entry * 100.0)
    return mae, mfe


def trade_span_years(trades: List[Dict[str, Any]]) -> Optional[float]:
    """Observed entry-to-exit span for honest per-trade annualisation.

    The UI previously omitted ``years`` and therefore displayed a raw
    per-trade Sharpe beside annualised backtest ratios.  Derive the span from
    the data we actually have; return ``None`` rather than guessing when fewer
    than two usable timestamps exist.
    """
    points: List[datetime] = []
    for trade in trades:
        for key in ("opened_at", "closed_at"):
            raw = trade.get(key)
            if not raw:
                continue
            try:
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                points.append(dt.astimezone(timezone.utc))
            except (TypeError, ValueError):
                continue
    if len(points) < 2:
        return None
    span_seconds = (max(points) - min(points)).total_seconds()
    return span_seconds / (365.25 * 24 * 3600) if span_seconds > 0 else None

def calculate_metrics(
    trades: List[Dict[str, Any]],
    initial_balance: float = 1000.0,
    *,
    years: Optional[float] = None,
) -> TradingPerformanceMetrics:
    """Calculate 15 trading performance metrics from a list of closed trades.

    Trades must be sorted chronologically by closed_at (oldest first).

    Sharpe, Sortino and drawdown come from :mod:`xauby.analytics.risk`, the same
    module the backtest uses, and the result carries a ``basis`` describing what
    was measured (roadmap P1.5). They were three separate implementations here
    before, none of which matched the backtest's:

    * returns were taken from ``net_pnl_pct`` — the return on the *trade*, not
      on the account — so the ratio was scaled by position sizing;
    * downside deviation clipped positive returns to zero and divided by the
      count of all returns, so profitable trades inflated Sortino;
    * nothing was annualised, while the backtest annualises, so the two Sharpes
      differed by a factor of sqrt(periods per year) before any of the above.

    ``years`` is the span the trades cover. Supply it to annualise; without it
    the ratios stay per-trade and ``basis.annualised`` says so, rather than
    being annualised by a guessed periodicity.
    """
    # Initialize default metrics if there are no trades
    if not trades:
        return TradingPerformanceMetrics(
            total_return_pct=0.0,
            net_pnl=0.0,
            gross_profit=0.0,
            gross_loss=0.0,
            win_rate=0.0,
            profit_factor=0.0,
            expectancy=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            risk_reward_ratio=0.0,
            max_drawdown_pct=0.0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            consecutive_wins=0,
            consecutive_losses=0,
            basis=MetricBasis(
                sampling="per-trade", annualised=False,
                return_basis="equity-relative", drawdown_source="trade-close",
            ),
        )

    # Sort trades chronologically to ensure correct drawdown and streak calculations
    # Handle string timestamps correctly
    def get_closed_at(t: Dict[str, Any]) -> str:
        return str(t.get("closed_at") or "")

    sorted_trades = sorted(trades, key=get_closed_at)

    net_pnl = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    wins = 0
    losses = 0
    pnl_list = []
    return_pct_list = []
    measured_excursions = [
        t for t in sorted_trades if bool(t.get("excursion_measured", False))
    ]
    average_mae_pct = (
        sum(float(t.get("mae_pct") or 0.0) for t in measured_excursions)
        / len(measured_excursions)
        if measured_excursions else 0.0
    )
    average_mfe_pct = (
        sum(float(t.get("mfe_pct") or 0.0) for t in measured_excursions)
        / len(measured_excursions)
        if measured_excursions else 0.0
    )
    excursion_coverage_pct = (
        len(measured_excursions) / len(sorted_trades) * 100.0
        if sorted_trades else 0.0
    )

    for t in sorted_trades:
        pnl = float(t.get("net_pnl", 0.0))
        pnl_list.append(pnl)
        
        # PnL percentage return relative to entry cost
        entry_cost = float(t.get("entry_cost", 0.0))
        pnl_pct = float(t.get("net_pnl_pct", 0.0))
        if pnl_pct == 0.0 and entry_cost > 0:
            pnl_pct = (pnl / entry_cost) * 100.0
        return_pct_list.append(pnl_pct)

        net_pnl += pnl
        if pnl > 0:
            gross_profit += pnl
            wins += 1
        elif pnl < 0:
            gross_loss += abs(pnl)
            losses += 1
        else:
            # Flat trades count towards total but not wins/losses in traditional sense
            pass

    total_trades = len(sorted_trades)
    win_rate = (wins / total_trades) * 100.0 if total_trades > 0 else 0.0
    
    # Profit Factor
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 1.0)
    if gross_loss == 0.0 and gross_profit > 0:
        profit_factor = float('inf') # represent infinity/unbeaten case

    # Expectancy
    expectancy = net_pnl / total_trades if total_trades > 0 else 0.0

    # Averages
    avg_win = gross_profit / wins if wins > 0 else 0.0
    avg_loss = gross_loss / losses if losses > 0 else 0.0
    risk_reward_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0

    # Risk metrics through the shared module, so "Sharpe" on the dashboard and
    # "Sharpe" in a backtest are the same estimator on the same return basis.
    # Drawdown here is still trade-close only — the excursion between entry and
    # exit is not in this data — which `basis` states rather than glossing over.
    risk = risk_from_trade_pnls(pnl_list, initial_balance, years=years)
    max_dd_pct = risk.max_drawdown_pct
    sharpe_ratio = risk.sharpe
    sortino_ratio = risk.sortino

    # Consecutive wins & losses
    consecutive_wins = 0
    consecutive_losses = 0
    curr_wins = 0
    curr_losses = 0

    for pnl in pnl_list:
        if pnl > 0:
            curr_wins += 1
            curr_losses = 0
            if curr_wins > consecutive_wins:
                consecutive_wins = curr_wins
        elif pnl < 0:
            curr_losses += 1
            curr_wins = 0
            if curr_losses > consecutive_losses:
                consecutive_losses = curr_losses
        else:
            # flat trade breaks both streaks
            curr_wins = 0
            curr_losses = 0

    # Total return relative to initial balance
    total_return_pct = (net_pnl / initial_balance) * 100.0

    return TradingPerformanceMetrics(
        total_return_pct=total_return_pct,
        net_pnl=net_pnl,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        win_rate=win_rate,
        profit_factor=profit_factor,
        expectancy=expectancy,
        avg_win=avg_win,
        avg_loss=avg_loss,
        risk_reward_ratio=risk_reward_ratio,
        max_drawdown_pct=max_dd_pct,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        consecutive_wins=consecutive_wins,
        consecutive_losses=consecutive_losses,
        basis=risk.basis,
        average_mae_pct=average_mae_pct,
        average_mfe_pct=average_mfe_pct,
        excursion_coverage_pct=excursion_coverage_pct,
    )
