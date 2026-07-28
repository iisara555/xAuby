"""Telegram message layouts for multi-pair trading (2+ active whitelist pairs)."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from xauby.notifications.report_formatter import build_period_report_message
from xauby.track_record.generator import generate_report
from xauby.meta import PRODUCT_NAME


def _pair_regime_one_liner(regime: Optional[Dict[str, Any]]) -> str:
    if not regime:
        return "regime n/a"
    name = regime.get("regime", "UNKNOWN")
    score = regime.get("gold_score", 50)
    return f"{name} {score}/100"


def format_multi_regime(
    symbols: Sequence[str],
    get_regime: Callable[[str], Optional[Dict[str, Any]]],
    *,
    max_reasons_per_pair: int = 2,
) -> str:
    lines = [f"📊 *Market Regime* — `{len(symbols)}` pair(s)"]
    for sym in symbols:
        reg = get_regime(sym)
        lines.append(f"\n`{sym}`")
        if not reg:
            lines.append("  _No regime data_")
            continue
        lines.append(
            f"  `{reg.get('regime', 'UNKNOWN')}` │ `{reg.get('gold_score', 50)}/100` "
            f"│ `{reg.get('trend', 'NEUTRAL')}` │ `{reg.get('macro_bias', 'NEUTRAL')}`"
        )
        for item in (reg.get("reasons") or [])[:max_reasons_per_pair]:
            mark = "✓" if item.get("supportive") else "✗"
            lines.append(f"  {mark} {item.get('label', '')}")
    return "\n".join(lines)


def format_multi_last_trades(
    symbols: Sequence[str],
    get_trades: Callable[[str, int], List[Dict[str, Any]]],
    *,
    limit_per_pair: int = 2,
) -> str:
    lines = [f"📋 *Recent Trades* — `{len(symbols)}` pair(s)"]
    any_trades = False
    for sym in symbols:
        trades = get_trades(sym, limit_per_pair)
        lines.append(f"\n`{sym}`")
        if not trades:
            lines.append("  _No closed trades_")
            continue
        any_trades = True
        for t in trades[:limit_per_pair]:
            pnl = float(t.get("net_pnl") or 0.0)
            sign = "+" if pnl >= 0 else ""
            dt = str(t.get("closed_at") or "")[:16]
            trigger = t.get("trigger") or "N/A"
            lines.append(f"  • `{dt}` {trigger}: `{sign}{pnl:.2f} USDT`")
    if not any_trades:
        return "No closed trades yet."
    return "\n".join(lines)


def format_multi_pnl(
    symbols: Sequence[str],
    get_trades: Callable[[Optional[str], int], List[Dict[str, Any]]],
    *,
    periods: Tuple[int, ...] = (7, 30),
    initial_balance: float = 1000.0,
) -> str:
    all_trades = get_trades(None, 5000)
    lines = [f"📈 *PnL* — `{len(symbols)}` pair(s)"]
    for days in periods:
        portfolio = generate_report(
            all_trades, days, f"{days}-Day", initial_balance=initial_balance
        )
        sign = "+" if portfolio.net_pnl >= 0 else ""
        lines.append(
            f"\n*{days}-Day Portfolio*\n"
            f"Trades: `{portfolio.total_trades}` │ WR: `{portfolio.win_rate:.1f}%` │ "
            f"Net: `{sign}{portfolio.net_pnl:.2f} USDT`"
        )
        for sym in symbols:
            sym_trades = [t for t in all_trades if str(t.get("symbol", "")).upper() == sym]
            report = generate_report(
                sym_trades, days, f"{days}-Day", initial_balance=initial_balance
            )
            s = "+" if report.net_pnl >= 0 else ""
            lines.append(
                f"  • `{sym}`: `{report.total_trades}` │ `{s}{report.net_pnl:.2f} USDT`"
            )
    return "\n".join(lines)


def format_multi_heartbeat(
    symbols: Sequence[str],
    equity: float,
    get_price: Callable[[str], float],
    get_position_line: Callable[[str], str],
    get_regime: Callable[[str], Optional[Dict[str, Any]]],
) -> str:
    lines = [
        f"💚 *{PRODUCT_NAME} Heartbeat* — `{len(symbols)}` pair(s)",
        f"• Equity: `{equity:.2f} USDT`",
        f"• Status: `Running normally`",
    ]
    for sym in symbols:
        price = get_price(sym)
        pos = get_position_line(sym)
        reg = _pair_regime_one_liner(get_regime(sym))
        lines.append(f"• `{sym}` `{price:.2f}` │ {pos} │ {reg}")
    return "\n".join(lines)


def build_multi_daily_digest(
    symbols: Sequence[str],
    equity: float,
    get_trades: Callable[[Optional[str], int], List[Dict[str, Any]]],
    get_position_line: Callable[[str], str],
    get_regime: Callable[[str], Optional[Dict[str, Any]]],
    *,
    initial_balance: float = 1000.0,
) -> str:
    all_trades = get_trades(None, 5000)
    portfolio = generate_report(
        all_trades, 1, "24h", initial_balance=initial_balance
    )
    sign = "+" if portfolio.net_pnl >= 0 else ""
    lines = [
        f"📅 *Daily Digest* — `{len(symbols)}` pair(s)",
        f"Equity: `{equity:.2f} USDT`",
        (
            f"*24h Portfolio* — Trades: `{portfolio.total_trades}` │ "
            f"WR: `{portfolio.win_rate:.1f}%` │ Net: `{sign}{portfolio.net_pnl:.2f} USDT`"
        ),
        "",
        "*Per pair*",
    ]
    for sym in symbols:
        sym_trades = [t for t in all_trades if str(t.get("symbol", "")).upper() == sym]
        day = generate_report(
            sym_trades, 1, "24h", initial_balance=initial_balance
        )
        ds = "+" if day.net_pnl >= 0 else ""
        pos = get_position_line(sym)
        lines.append(
            f"• `{sym}`: `{day.total_trades}` trades │ `{ds}{day.net_pnl:.2f} USDT` │ "
            f"{pos} │ `{_pair_regime_one_liner(get_regime(sym))}`"
        )
    return "\n".join(lines)


def build_multi_weekly_review(
    symbols: Sequence[str],
    get_trades: Callable[[Optional[str], int], List[Dict[str, Any]]],
    get_regime: Callable[[str], Optional[Dict[str, Any]]],
    period_days: int = 7,
    *,
    initial_balance: float = 1000.0,
) -> str:
    all_trades = get_trades(None, 5000)
    focus_regime = get_regime(symbols[0]) if symbols else None
    title = f"Weekly Review ({len(symbols)} pairs)"
    body = build_period_report_message(
        all_trades, period_days, title, focus_regime,
        initial_balance=initial_balance,
    )
    lines = [body, "", "*Per pair*"]
    for sym in symbols:
        sym_trades = [t for t in all_trades if str(t.get("symbol", "")).upper() == sym]
        report = generate_report(
            sym_trades,
            period_days,
            f"{period_days}-Day",
            initial_balance=initial_balance,
        )
        sign = "+" if report.net_pnl >= 0 else ""
        lines.append(
            f"• `{sym}`: `{report.total_trades}` trades │ WR `{report.win_rate:.1f}%` │ "
            f"Net `{sign}{report.net_pnl:.2f} USDT` │ {_pair_regime_one_liner(get_regime(sym))}"
        )
    return "\n".join(lines)
