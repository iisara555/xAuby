"""Typed backtest metrics and result extraction helpers."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class BacktestMetrics:
    """Structured backtest result metrics."""

    net_profit_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    initial_balance: float = 0.0
    final_balance: float = 0.0
    bars: int = 0
    # Risk-adjusted metrics (annualized where applicable). Computed from the
    # per-bar mark-to-market equity curve, so they reflect intra-trade swings.
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    cagr_pct: float = 0.0
    exposure_pct: float = 0.0
    avg_holding_bars: float = 0.0
    max_consecutive_losses: int = 0
    checklist_config: Dict[str, Any] = field(default_factory=dict)
    last_checklist: List[Dict[str, Any]] = field(default_factory=list)
    trades: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to flat dict for backward compatibility."""
        return {
            "net_profit_pct": self.net_profit_pct,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "max_drawdown_pct": self.max_drawdown_pct,
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "gross_profit": self.gross_profit,
            "gross_loss": self.gross_loss,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "initial_balance": self.initial_balance,
            "final_balance": self.final_balance,
            "bars": self.bars,
            "sharpe": self.sharpe,
            "sortino": self.sortino,
            "calmar": self.calmar,
            "cagr_pct": self.cagr_pct,
            "exposure_pct": self.exposure_pct,
            "avg_holding_bars": self.avg_holding_bars,
            "max_consecutive_losses": self.max_consecutive_losses,
            "checklist_config": self.checklist_config,
            "last_checklist": self.last_checklist,
            "trades": self.trades,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "BacktestMetrics":
        """Hydrate from flat dict (e.g. legacy stats)."""
        d = data or {}
        return cls(
            net_profit_pct=float(d.get("net_profit_pct", 0.0) or 0.0),
            win_rate=float(d.get("win_rate", 0.0) or 0.0),
            profit_factor=float(d.get("profit_factor", 0.0) or 0.0),
            max_drawdown_pct=float(d.get("max_drawdown_pct", 0.0) or 0.0),
            total_trades=int(d.get("total_trades", 0) or 0),
            wins=int(d.get("wins", 0) or 0),
            losses=int(d.get("losses", 0) or 0),
            gross_profit=float(d.get("gross_profit", 0.0) or 0.0),
            gross_loss=float(d.get("gross_loss", 0.0) or 0.0),
            avg_win=float(d.get("avg_win", 0.0) or 0.0),
            avg_loss=float(d.get("avg_loss", 0.0) or 0.0),
            initial_balance=float(d.get("initial_balance", 0.0) or 0.0),
            final_balance=float(d.get("final_balance", 0.0) or 0.0),
            bars=int(d.get("bars", 0) or 0),
            sharpe=float(d.get("sharpe", 0.0) or 0.0),
            sortino=float(d.get("sortino", 0.0) or 0.0),
            calmar=float(d.get("calmar", 0.0) or 0.0),
            cagr_pct=float(d.get("cagr_pct", 0.0) or 0.0),
            exposure_pct=float(d.get("exposure_pct", 0.0) or 0.0),
            avg_holding_bars=float(d.get("avg_holding_bars", 0.0) or 0.0),
            max_consecutive_losses=int(d.get("max_consecutive_losses", 0) or 0),
            checklist_config=dict(d.get("checklist_config") or {}),
            last_checklist=list(d.get("last_checklist") or []),
            trades=list(d.get("trades") or []),
        )


def compute_risk_metrics(
    *,
    equity_curve: Optional[List[float]],
    trades: List[Any],
    initial_balance: float,
    final_balance: float,
    max_drawdown_pct: float,
    periods_per_year: float = 0.0,
    bars_in_position: int = 0,
    total_bars: int = 0,
    tf_seconds: int = 0,
) -> Dict[str, Any]:
    """Risk-adjusted stats from the per-bar equity curve and trade list.

    Sharpe / Sortino are annualized with ``sqrt(periods_per_year)``; Calmar is
    CAGR / max-drawdown. Everything degrades gracefully to 0.0 when there is
    not enough data (no curve, single bar, zero variance), so callers never
    have to guard.
    """
    out: Dict[str, Any] = {
        "sharpe": 0.0, "sortino": 0.0, "calmar": 0.0, "cagr_pct": 0.0,
        "exposure_pct": 0.0, "avg_holding_bars": 0.0, "max_consecutive_losses": 0,
    }

    curve = [float(e) for e in (equity_curve or []) if e is not None]
    rets: List[float] = []
    for prev, cur in zip(curve, curve[1:]):
        if prev > 0:
            rets.append((cur - prev) / prev)

    ann = math.sqrt(periods_per_year) if periods_per_year > 0 else 0.0
    if len(rets) > 1 and ann > 0:
        mean = statistics.fmean(rets)
        sd = statistics.pstdev(rets)
        if sd > 0:
            out["sharpe"] = round(mean / sd * ann, 3)
        downside = [r for r in rets if r < 0]
        dsd = statistics.pstdev(downside) if len(downside) > 1 else (
            abs(downside[0]) if downside else 0.0
        )
        if dsd > 0:
            out["sortino"] = round(mean / dsd * ann, 3)

    if periods_per_year > 0 and len(curve) > 1 and initial_balance > 0 and final_balance > 0:
        years = len(curve) / periods_per_year
        if years > 0:
            out["cagr_pct"] = round(
                ((final_balance / initial_balance) ** (1.0 / years) - 1.0) * 100.0, 3
            )
    if max_drawdown_pct > 0:
        out["calmar"] = round(out["cagr_pct"] / max_drawdown_pct, 3)

    if total_bars > 0:
        out["exposure_pct"] = round(bars_in_position / total_bars * 100.0, 2)

    if trades and tf_seconds > 0:
        holds = [
            (t.exit_time - t.entry_time) / tf_seconds
            for t in trades
            if t.exit_time >= t.entry_time
        ]
        if holds:
            out["avg_holding_bars"] = round(sum(holds) / len(holds), 2)

    streak = worst = 0
    for t in trades:
        if t.pnl < 0:
            streak += 1
            worst = max(worst, streak)
        else:
            streak = 0
    out["max_consecutive_losses"] = worst
    return out


def build_metrics_from_replay(
    trades: List[Any],
    initial_balance: float,
    final_balance: float,
    bars: int,
    checklist_config: Dict[str, Any],
    last_checklist: List[Dict[str, Any]],
    equity_curve: Optional[List[float]] = None,
    *,
    periods_per_year: float = 0.0,
    bars_in_position: int = 0,
    total_bars: int = 0,
    tf_seconds: int = 0,
) -> BacktestMetrics:
    """Build a BacktestMetrics instance from replay outputs."""
    from xauby.observability.replay import ReplayEngine

    base_stats = ReplayEngine.stats(trades, initial_balance, final_balance, equity_curve)
    wins = sum(1 for t in trades if t.pnl > 0)
    losses = len(trades) - wins
    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))

    risk = compute_risk_metrics(
        equity_curve=equity_curve,
        trades=trades,
        initial_balance=initial_balance,
        final_balance=final_balance,
        max_drawdown_pct=float(base_stats.get("max_drawdown_pct", 0.0) or 0.0),
        periods_per_year=periods_per_year,
        bars_in_position=bars_in_position,
        total_bars=total_bars,
        tf_seconds=tf_seconds,
    )

    trade_dicts = [
        {
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "pnl": t.pnl,
            "pnl_pct": t.pnl_pct,
            "trigger": t.trigger,
        }
        for t in trades
    ]

    return BacktestMetrics(
        net_profit_pct=float(base_stats.get("net_profit_pct", 0.0) or 0.0),
        win_rate=float(base_stats.get("win_rate", 0.0) or 0.0),
        profit_factor=float(base_stats.get("profit_factor", 0.0) or 0.0),
        max_drawdown_pct=float(base_stats.get("max_drawdown_pct", 0.0) or 0.0),
        total_trades=int(base_stats.get("total_trades", 0) or 0),
        avg_win=float(base_stats.get("avg_win", 0.0) or 0.0),
        avg_loss=float(base_stats.get("avg_loss", 0.0) or 0.0),
        wins=wins,
        losses=losses,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        initial_balance=initial_balance,
        final_balance=final_balance,
        bars=bars,
        sharpe=risk["sharpe"],
        sortino=risk["sortino"],
        calmar=risk["calmar"],
        cagr_pct=risk["cagr_pct"],
        exposure_pct=risk["exposure_pct"],
        avg_holding_bars=risk["avg_holding_bars"],
        max_consecutive_losses=risk["max_consecutive_losses"],
        checklist_config=dict(checklist_config),
        last_checklist=list(last_checklist),
        trades=trade_dicts,
    )


def extract_optimizer_entry(
    result: Any,
    override: Dict[str, Any],
) -> Dict[str, Any]:
    """Extract a standardized optimizer result entry from a BacktestRunResult."""
    meta = result.meta
    stats = result.stats or {}
    config_used = meta.config_used or {}

    entry: Dict[str, Any] = dict(override)
    entry["trailing_atr_mult"] = float(
        config_used.get("trailing_atr_mult", 1.5)
    )
    entry["breakeven_sl_enabled"] = bool(
        config_used.get("breakeven_sl_enabled", False)
    )
    entry["breakeven_activation_atr_mult"] = float(
        config_used.get("breakeven_activation_atr_mult", 1.5)
    )
    entry["breakeven_buffer_atr_mult"] = float(
        config_used.get("breakeven_buffer_atr_mult", 0.1)
    )
    entry["net_profit_pct"] = float(stats.get("net_profit_pct", 0.0) or 0.0)
    entry["win_rate"] = float(stats.get("win_rate", 0.0) or 0.0)
    entry["total_trades"] = int(stats.get("total_trades", 0) or 0)
    entry["mdd"] = float(stats.get("max_drawdown_pct", 0.0) or 0.0)
    entry["profit_factor"] = float(stats.get("profit_factor", 0.0) or 0.0)
    entry["data_symbol"] = meta.data_symbol or meta.symbol
    entry["used_data_proxy"] = bool(meta.used_data_proxy)
    return entry
