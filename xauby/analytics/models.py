from dataclasses import dataclass, field
from typing import Dict, Any, Optional

from xauby.analytics.risk import MetricBasis

@dataclass
class TradingPerformanceMetrics:
    total_return_pct: float
    net_pnl: float
    gross_profit: float
    gross_loss: float
    win_rate: float
    profit_factor: float
    expectancy: float
    avg_win: float
    avg_loss: float
    risk_reward_ratio: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    consecutive_wins: int
    consecutive_losses: int
    #: What sharpe_ratio / sortino_ratio / max_drawdown_pct were computed from.
    #: Present so a live number is never silently compared with a backtest one
    #: measured on a different sampling unit — see xauby/analytics/risk.py.
    basis: Optional[MetricBasis] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_return_pct": self.total_return_pct,
            "net_pnl": self.net_pnl,
            "gross_profit": self.gross_profit,
            "gross_loss": self.gross_loss,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "expectancy": self.expectancy,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "risk_reward_ratio": self.risk_reward_ratio,
            "max_drawdown_pct": self.max_drawdown_pct,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "consecutive_wins": self.consecutive_wins,
            "consecutive_losses": self.consecutive_losses,
            "basis": self.basis.to_dict() if self.basis else None,
        }
