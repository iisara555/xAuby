import math
from typing import List, Dict, Any
from xauby.analytics.models import TradingPerformanceMetrics

def calculate_metrics(trades: List[Dict[str, Any]], initial_balance: float = 1000.0) -> TradingPerformanceMetrics:
    """Calculate 15 trading performance metrics from a list of closed trades.
    
    Trades must be sorted chronologically by closed_at (oldest first).
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
            consecutive_losses=0
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

    # Drawdown calculations
    equity = initial_balance
    equity_curve = [equity]
    for pnl in pnl_list:
        equity += pnl
        equity_curve.append(equity)

    peak = initial_balance
    max_dd_pct = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = ((peak - eq) / peak) * 100.0 if peak > 0 else 0.0
        if dd > max_dd_pct:
            max_dd_pct = dd

    # Sharpe and Sortino ratios (using returns pct list)
    mean_return = sum(return_pct_list) / len(return_pct_list) if return_pct_list else 0.0
    
    # Standard deviation of returns
    variance = sum((r - mean_return) ** 2 for r in return_pct_list) / len(return_pct_list) if return_pct_list else 0.0
    std_dev = math.sqrt(variance) if variance > 0 else 0.0
    sharpe_ratio = mean_return / std_dev if std_dev > 0 else 0.0

    # Downside deviation (for Sortino, using 0 as target return)
    downside_returns = [min(0.0, r) for r in return_pct_list]
    downside_variance = sum(r ** 2 for r in downside_returns) / len(downside_returns) if downside_returns else 0.0
    downside_std_dev = math.sqrt(downside_variance) if downside_variance > 0 else 0.0
    sortino_ratio = mean_return / downside_std_dev if downside_std_dev > 0 else 0.0

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
        consecutive_losses=consecutive_losses
    )
