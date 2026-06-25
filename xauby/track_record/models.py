from dataclasses import dataclass, asdict
from typing import List, Dict, Any

@dataclass
class TrackRecordEntry:
    strategy_name: str
    symbol: str
    entry_time: str
    exit_time: str
    pnl: float
    return_pct: float
    drawdown_pct: float
    duration_seconds: float
    regime_at_entry: str
    regime_at_exit: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class PerformanceReport:
    report_name: str
    period_days: int
    total_trades: int
    win_rate: float
    net_pnl: float
    profit_factor: float
    max_drawdown_pct: float
    average_duration_hours: float
    entries: List[TrackRecordEntry]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_name": self.report_name,
            "period_days": self.period_days,
            "total_trades": self.total_trades,
            "win_rate": self.win_rate,
            "net_pnl": self.net_pnl,
            "profit_factor": self.profit_factor,
            "max_drawdown_pct": self.max_drawdown_pct,
            "average_duration_hours": self.average_duration_hours,
            "entries": [e.to_dict() for e in self.entries]
        }
