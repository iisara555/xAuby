from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from xauby.track_record.generator import generate_report
from xauby.track_record.models import PerformanceReport


def _pf_str(value: float) -> str:
    if value == float("inf"):
        return "inf"
    return f"{value:.2f}x"


def _regime_line(regime: Optional[Dict[str, Any]]) -> str:
    if not regime:
        return "Regime: `UNKNOWN`"
    name = regime.get("regime", "UNKNOWN")
    score = regime.get("gold_score", 50)
    return f"Regime now: `{name}` ({score}/100)"


def format_report_telegram(
    report: PerformanceReport,
    title: str,
    regime: Optional[Dict[str, Any]] = None,
) -> str:
    sign = "+" if report.net_pnl >= 0 else ""
    lines = [
        f"📊 *{title}* ({report.period_days}d)",
        f"Trades: `{report.total_trades}` │ WR: `{report.win_rate:.1f}%` │ Net: `{sign}{report.net_pnl:.2f} USDT`",
        f"PF: `{_pf_str(report.profit_factor)}` │ Max DD: `{report.max_drawdown_pct:.2f}%`",
        _regime_line(regime),
    ]
    if report.excursion_coverage_pct > 0:
        lines.insert(
            3,
            f"MAE/MFE avg: `{report.average_mae_pct:.2f}% / "
            f"{report.average_mfe_pct:.2f}%` │ coverage `{report.excursion_coverage_pct:.0f}%`",
        )
    return "\n".join(lines)


def format_weekly_review_md(
    report: PerformanceReport,
    regime: Optional[Dict[str, Any]],
    symbol: str,
    generated_at: Optional[datetime] = None,
) -> str:
    when = (generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    sign = "+" if report.net_pnl >= 0 else ""
    lines = [
        f"# Weekly Review — {symbol}",
        f"Generated: {when}",
        "",
        f"- Period: {report.period_days} days ({report.report_name})",
        f"- Trades: {report.total_trades}",
        f"- Win rate: {report.win_rate:.1f}%",
        f"- Net PnL: {sign}{report.net_pnl:.4f} USDT",
        f"- Profit factor: {_pf_str(report.profit_factor)}",
        f"- Max drawdown: {report.max_drawdown_pct:.2f}%",
        f"- Avg duration: {report.average_duration_hours:.1f}h",
    ]
    if report.excursion_coverage_pct > 0:
        lines.extend([
            f"- Average MAE: {report.average_mae_pct:.2f}%",
            f"- Average MFE: {report.average_mfe_pct:.2f}%",
            f"- Excursion coverage: {report.excursion_coverage_pct:.1f}%",
        ])
    if regime:
        lines.extend([
            "",
            "## Market Regime",
            f"- Regime: {regime.get('regime', 'UNKNOWN')}",
            f"- Regime score: {regime.get('gold_score', 50)}/100",
            f"- Trend: {regime.get('trend', 'NEUTRAL')}",
            f"- Macro bias: {regime.get('macro_bias', 'NEUTRAL')}",
        ])
    return "\n".join(lines) + "\n"


def build_period_report_message(
    trades: List[Dict[str, Any]],
    period_days: int,
    title: str,
    regime: Optional[Dict[str, Any]] = None,
    *,
    initial_balance: float = 1000.0,
) -> str:
    report = generate_report(
        trades, period_days, f"{period_days}-Day",
        initial_balance=initial_balance,
    )
    return format_report_telegram(report, title, regime)


def build_daily_digest_message(
    trades: List[Dict[str, Any]],
    symbol: str,
    equity: float,
    position_line: str,
    regime: Optional[Dict[str, Any]] = None,
    *,
    initial_balance: float = 1000.0,
) -> str:
    report = generate_report(
        trades, 1, "24h", initial_balance=initial_balance
    )
    sign = "+" if report.net_pnl >= 0 else ""
    return (
        f"📅 *Daily Digest* — `{symbol}`\n"
        f"24h Trades: `{report.total_trades}` │ WR: `{report.win_rate:.1f}%` │ Net: `{sign}{report.net_pnl:.2f} USDT`\n"
        f"Equity: `{equity:.2f} USDT` │ Avg hold: `{report.average_duration_hours:.1f}h`\n"
        f"Position: `{position_line}`\n"
        f"{_regime_line(regime)}"
    )
