from dataclasses import dataclass
from typing import Dict, Any

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
        }
