"""Backtest summary and parity-check presenter for TUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from xauby.backtest.best_params import load_best_params
from xauby.backtest.constants import PHONE_MAX_TRADES
from xauby.backtest.runtime_config import extract_checklist_config
from xauby.backtest.service import BacktestRunMeta, BacktestRunResult
from xauby.strategies.registry import normalize_strategy_name
from xauby.utils.colors import C_BOLD, C_GREEN, C_MUTED, C_PRIMARY, C_RED, C_RESET, C_YELLOW

_PHONE_BREAKPOINT = 75


def _is_cdc_family(strategy_name: str) -> bool:
    """CDC ActionZone owns the RSI/Vol/Fresh-zone/AP checklist gates.

    Other registry strategies (SuperTrend, BB+RSI, …) do not use those gates,
    so showing them produces misleading values like ``RSI 0/100`` / ``Vol >=0.0``.
    """
    return normalize_strategy_name(strategy_name).lower() == "xauby_actionzone"


@dataclass
class ParityCheck:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class BacktestViewModel:
    symbol: str
    strategy_name: str
    period_label: str
    total_trades: int
    net_return_pct: float
    profit_factor: float
    win_rate_pct: float
    max_drawdown_pct: float
    parity_checks: List[ParityCheck] = field(default_factory=list)
    confidence: int = 0
    status: str = "idle"  # idle | running | done | error
    error: str = ""
    primary_tf: str = ""
    regime_tf: str = ""
    wins: int = 0
    losses: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    initial_balance: float = 0.0
    final_balance: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    best_params: Dict[str, Any] = field(default_factory=dict)
    config_used: Dict[str, Any] = field(default_factory=dict)
    data_symbol: str = ""
    used_data_proxy: bool = False
    trades: List[Dict[str, Any]] = field(default_factory=list)


def build_parity_checks(meta: BacktestRunMeta, stats: Dict[str, Any]) -> List[ParityCheck]:
    checks: List[ParityCheck] = []

    analyze_ok = meta.run_ok and bool(stats)
    checks.append(
        ParityCheck(
            name="Analyze()",
            passed=analyze_ok,
            detail="StrategyRunner.run() plugin path" if analyze_ok else meta.error or "run failed",
        )
    )

    if meta.use_d1_regime_filter:
        d1_ok = meta.run_ok and meta.regime_bars > 0
        checks.append(
            ParityCheck(
                name="D1 Filter",
                passed=d1_ok,
                detail=f"{meta.regime_tf or '1d'} candles loaded" if d1_ok else "regime data missing",
            )
        )
    else:
        checks.append(
            ParityCheck(
                name="D1 Filter",
                passed=True,
                detail="filter disabled (N/A)",
            )
        )

    atr_ok = meta.run_ok and bool(stats)
    err_lower = (meta.error or "").lower()
    if "atr" in err_lower or "volatility" in err_lower:
        atr_ok = False
    checks.append(
        ParityCheck(
            name="ATR",
            passed=atr_ok,
            detail="signal.volatility path" if atr_ok else meta.error or "ATR path error",
        )
    )

    fees_ok = meta.fee_pct > 0
    checks.append(
        ParityCheck(
            name="Fees",
            passed=fees_ok,
            detail=f"fee_pct={meta.fee_pct:.4f}" if fees_ok else "fee_pct is zero",
        )
    )

    cu = meta.config_used or {}
    gates = dict(cu.get("checklist_gates") or extract_checklist_config(cu))
    gates_ok = meta.run_ok and bool(gates)
    last_chk = cu.get("last_checklist") or stats.get("last_checklist") or []
    if isinstance(last_chk, list) and last_chk:
        ok_count = sum(1 for item in last_chk if item.get("ok"))
        gate_detail = (
            f"RSI {gates.get('rsi_min', 0):.0f}-{gates.get('rsi_max', 0):.0f} "
            f"Vol>={gates.get('vol_min_ratio', 0):.1f} "
            f"Fresh={'on' if gates.get('require_fresh_zone') else 'off'}"
            f"/{gates.get('fresh_zone_window', 0)} "
            f"AP={gates.get('ap_smoothing', 1)} "
            f"| last bar {ok_count}/{len(last_chk)} OK"
        )
    else:
        gate_detail = (
            f"RSI {gates.get('rsi_min', 0):.0f}-{gates.get('rsi_max', 0):.0f} "
            f"Vol>={gates.get('vol_min_ratio', 0):.1f} "
            f"Fresh={'on' if gates.get('require_fresh_zone') else 'off'}"
            f"/{gates.get('fresh_zone_window', 0)} "
            f"AP={gates.get('ap_smoothing', 1)}"
        )
    checks.append(
        ParityCheck(
            name="Checklist",
            passed=gates_ok,
            detail=gate_detail if gates_ok else meta.error or "checklist gates missing",
        )
    )

    return checks


def confidence_score(checks: List[ParityCheck]) -> int:
    per_check = max(1, 100 // max(len(checks), 1))
    return min(100, sum(per_check for c in checks if c.passed))


def build_backtest_view_model(result: Optional[BacktestRunResult], *, status: str = "idle") -> BacktestViewModel:
    if result is None or status == "idle":
        return BacktestViewModel(
            symbol="",
            strategy_name="",
            period_label="",
            total_trades=0,
            net_return_pct=0.0,
            profit_factor=0.0,
            win_rate_pct=0.0,
            max_drawdown_pct=0.0,
            status="idle",
        )

    meta = result.meta
    stats = result.stats or {}
    strategy_name = normalize_strategy_name(meta.strategy_name)

    if not meta.run_ok:
        checks = build_parity_checks(meta, stats)
        return BacktestViewModel(
            symbol=meta.symbol,
            strategy_name=strategy_name,
            period_label=_period_str(meta),
            total_trades=0,
            net_return_pct=0.0,
            profit_factor=0.0,
            win_rate_pct=0.0,
            max_drawdown_pct=0.0,
            parity_checks=checks,
            confidence=confidence_score(checks),
            status="error",
            error=meta.error,
            primary_tf=meta.primary_tf,
            regime_tf=meta.regime_tf or "off",
            config_used=dict(meta.config_used or {}),
            data_symbol=meta.data_symbol or meta.symbol,
            used_data_proxy=bool(meta.used_data_proxy),
        )

    checks = build_parity_checks(meta, stats)
    period = _period_str(meta)
    pf = float(stats.get("profit_factor", 0.0) or 0.0)

    return BacktestViewModel(
        symbol=meta.symbol,
        strategy_name=strategy_name,
        period_label=period,
        total_trades=int(stats.get("total_trades", 0) or 0),
        net_return_pct=float(stats.get("net_profit_pct", 0.0) or 0.0),
        profit_factor=pf,
        win_rate_pct=float(stats.get("win_rate", 0.0) or 0.0),
        max_drawdown_pct=float(stats.get("max_drawdown_pct", 0.0) or 0.0),
        parity_checks=checks,
        confidence=confidence_score(checks),
        status=status,
        primary_tf=meta.primary_tf,
        regime_tf=meta.regime_tf or "off",
        wins=int(stats.get("wins", 0) or 0),
        losses=int(stats.get("losses", 0) or 0),
        gross_profit=float(stats.get("gross_profit", 0.0) or 0.0),
        gross_loss=float(stats.get("gross_loss", 0.0) or 0.0),
        initial_balance=float(stats.get("initial_balance", 0.0) or 0.0),
        final_balance=float(stats.get("final_balance", 0.0) or 0.0),
        avg_win=float(stats.get("avg_win", 0.0) or 0.0),
        avg_loss=float(stats.get("avg_loss", 0.0) or 0.0),
        best_params=load_best_params(meta.symbol),
        config_used=dict(meta.config_used or {}),
        data_symbol=meta.data_symbol or meta.symbol,
        used_data_proxy=bool(meta.used_data_proxy),
        trades=stats.get("trades", []),
    )


def _data_source_line(vm: BacktestViewModel, *, compact: bool) -> Optional[str]:
    if not vm.used_data_proxy or not vm.data_symbol:
        return None
    if vm.data_symbol.upper() == (vm.symbol or "").upper():
        return None
    if compact:
        return (
            f"{C_MUTED}Candles: {vm.data_symbol} "
            f"(proxy — {vm.symbol} history short){C_RESET}"
        )
    return (
        f"{C_BOLD}Data Source:{C_RESET} {C_YELLOW}{vm.data_symbol}{C_RESET} "
        f"{C_MUTED}(proxy — limited {vm.symbol} history){C_RESET}"
    )


def _period_str(meta: BacktestRunMeta) -> str:
    if meta.period_start and meta.period_end:
        return f"{meta.period_start} → {meta.period_end}"
    return "—"


def _fmt_pct(value: float, *, signed: bool = True) -> str:
    if signed:
        return f"{value:+.1f}%"
    return f"{value:.1f}%"


def _parity_status_line(check: ParityCheck) -> str:
    if check.passed:
        status = f"{C_GREEN}PASS{C_RESET}"
    else:
        status = f"{C_RED}FAIL{C_RESET}"
    return f"{check.name:<14} {status}"


def _is_compact(width: int) -> bool:
    return max(38, width) < _PHONE_BREAKPOINT


def format_idle_summary_lines(symbol: str, width: int) -> List[str]:
    sym = (symbol or "—").upper().replace("_", "")
    if _is_compact(width):
        return [
            f"{C_BOLD}{sym}{C_RESET}",
            f"{C_MUTED}[R] replay  [G] opt  [O] apply{C_RESET}",
        ]
    lines = [
        f"{C_BOLD}Pair:{C_RESET} {C_PRIMARY}{sym}{C_RESET}",
        "",
        f"{C_MUTED}Press [R] replay · [G] optimize · [O] apply best{C_RESET}",
    ]
    return lines


def format_running_summary_lines(
    symbol: str,
    width: int,
    *,
    mode: str = "replay",
    compact: bool = False,
) -> List[str]:
    sym = (symbol or "—").upper().replace("_", "")
    phone = compact or _is_compact(width)
    if mode == "optimize":
        if phone:
            return [
                f"{C_YELLOW}Optimizing {sym}...{C_RESET}",
                f"{C_MUTED}Compact grid (16 runs).{C_RESET}",
            ]
        return [
            f"{C_YELLOW}Optimizing {sym}...{C_RESET}",
            f"{C_MUTED}Grid search may take several minutes.{C_RESET}",
        ]
    if phone:
        return [f"{C_YELLOW}Running {sym}...{C_RESET}"]
    return [
        f"{C_YELLOW}Running backtest for {sym}...{C_RESET}",
        f"{C_MUTED}May take 1-2 min while candles load and replay runs.{C_RESET}",
    ]


def format_summary_lines(vm: BacktestViewModel, width: int, *, compact: Optional[bool] = None) -> List[str]:
    phone = compact if compact is not None else _is_compact(width)
    if vm.status == "idle":
        return format_idle_summary_lines(vm.symbol, width)
    if vm.status == "running":
        return format_running_summary_lines(vm.symbol, width, compact=phone)

    if vm.status == "error":
        lines = [
            f"{C_BOLD}Strategy:{C_RESET} {vm.strategy_name or '—'}",
            f"{C_BOLD}Symbol:{C_RESET} {C_PRIMARY}{vm.symbol}{C_RESET}",
            "",
            f"{C_RED}Error: {vm.error}{C_RESET}",
        ]
        return lines

    net_color = C_GREEN if vm.net_return_pct >= 0 else C_RED
    regime_label = vm.regime_tf if vm.regime_tf and vm.regime_tf != "off" else "off"

    if phone:
        strat_short = (vm.strategy_name or "—")[:14]
        lines = [
            f"{C_BOLD}{vm.symbol}{C_RESET} · {strat_short}",
            f"TF {vm.primary_tf or '—'}/{regime_label} · {vm.period_label}",
            f"Bal {vm.initial_balance:.0f}→{vm.final_balance:.0f} · "
            f"T {vm.total_trades} (W{vm.wins}/L{vm.losses})",
            f"Net {net_color}{_fmt_pct(vm.net_return_pct)}{C_RESET} · "
            f"PF {vm.profit_factor:.2f} · WR {vm.win_rate_pct:.0f}%",
            f"DD {C_RED}{_fmt_pct(-vm.max_drawdown_pct, signed=True)}{C_RESET}",
        ]
        proxy_line = _data_source_line(vm, compact=True)
        if proxy_line:
            lines.append(proxy_line)
        if vm.config_used:
            cu = vm.config_used
            gates = cu.get("checklist_gates") or cu
            if _is_cdc_family(vm.strategy_name):
                lines.append(
                    f"Cfg SL{cu.get('sl_atr_mult')} RSI"
                    f"{gates.get('rsi_min', 0.0):.0f}-{gates.get('rsi_max', 0.0):.0f} "
                    f"Vol>={gates.get('vol_min_ratio')} "
                    f"Fresh{'on' if gates.get('require_fresh_zone', True) else 'off'}"
                    f"/{gates.get('fresh_zone_window', 3)}"
                )
            else:
                lines.append(
                    f"Cfg SL{cu.get('sl_atr_mult')} Trail{cu.get('trailing_atr_mult')}"
                )
        if vm.best_params:
            bp = vm.best_params
            lines.append(
                f"Best SL{bp.get('sl_atr_mult')} NP"
                f"{C_GREEN}+{bp.get('net_profit_pct', 0.0):.1f}%{C_RESET}"
            )
        return lines

    lines = [
        f"{C_BOLD}Strategy:{C_RESET} {vm.strategy_name}",
        f"{C_BOLD}Symbol:{C_RESET} {C_PRIMARY}{vm.symbol}{C_RESET}",
        f"{C_BOLD}Timeframes:{C_RESET} {vm.primary_tf or '—'} / {regime_label}",
    ]
    proxy_line = _data_source_line(vm, compact=False)
    if proxy_line:
        lines.append(proxy_line)
    lines.extend([
        "",
        f"{C_BOLD}Period:{C_RESET}",
        f"  {vm.period_label}",
        "",
        f"{C_BOLD}Start Bal:{C_RESET} {vm.initial_balance:.2f} USDT",
        f"{C_BOLD}End Bal:{C_RESET}   {vm.final_balance:.2f} USDT",
        "",
        f"{C_BOLD}Trades:{C_RESET} {vm.total_trades} (W: {C_GREEN}{vm.wins}{C_RESET} | L: {C_RED}{vm.losses}{C_RESET})",
        "",
        f"{C_BOLD}Net Return:{C_RESET} {net_color}{_fmt_pct(vm.net_return_pct)}{C_RESET}",
        f"{C_BOLD}Profit Factor:{C_RESET} {vm.profit_factor:.2f}",
        f"{C_BOLD}Win Rate:{C_RESET} {vm.win_rate_pct:.0f}%",
        "",
        f"{C_BOLD}Gross Profit:{C_RESET} {C_GREEN}+{vm.gross_profit:.2f} USDT{C_RESET}",
        f"{C_BOLD}Gross Loss:{C_RESET}   {C_RED}-{vm.gross_loss:.2f} USDT{C_RESET}",
        f"{C_BOLD}Avg Win/Loss:{C_RESET} {C_GREEN}+{vm.avg_win:.2f}{C_RESET} / {C_RED}-{vm.avg_loss:.2f} USDT{C_RESET}",
        "",
        f"{C_BOLD}Max DD:{C_RESET} {C_RED}{_fmt_pct(-vm.max_drawdown_pct, signed=True)}{C_RESET}",
    ])

    if vm.config_used:
        cu = vm.config_used
        gates = cu.get("checklist_gates") or cu
        fresh_on = bool(gates.get("require_fresh_zone", True))
        lines.extend([
            "",
            f"{C_BOLD}Config Used (This Run):{C_RESET}",
            f"  SL ATR Mult: {C_GREEN}{cu.get('sl_atr_mult')}{C_RESET}",
            f"  Trailing:    {C_GREEN}{cu.get('trailing_atr_mult')}{C_RESET}",
        ])
        if _is_cdc_family(vm.strategy_name):
            lines.extend([
                f"  RSI Min/Max: {C_GREEN}{gates.get('rsi_min', 0.0):.0f}/{gates.get('rsi_max', 0.0):.0f}{C_RESET}",
                f"  Vol Ratio:   {C_GREEN}>={gates.get('vol_min_ratio')}{C_RESET}",
                f"  Fresh Zone:  {C_GREEN}{'on' if fresh_on else 'off'}/{gates.get('fresh_zone_window', 3)}{C_RESET}",
                f"  AP Smooth:   {C_GREEN}{gates.get('ap_smoothing', 1)}{C_RESET}",
            ])
        lines.append(
            f"  Net Return:  {C_GREEN}{_fmt_pct(float(cu.get('net_profit_pct', vm.net_return_pct) or 0.0))}{C_RESET}"
        )
        last_chk = cu.get("last_checklist") or []
        if isinstance(last_chk, list) and last_chk:
            lines.append("")
            lines.append(f"{C_BOLD}Checklist (last bar):{C_RESET}")
            for item in last_chk[:8]:
                label = str(item.get("label") or "—")
                ok = bool(item.get("ok"))
                mark = f"{C_GREEN}OK{C_RESET}" if ok else f"{C_RED}X{C_RESET}"
                val = item.get("value", "")
                lines.append(f"  {mark} {label}: {val}")

    if vm.best_params:
        bp = vm.best_params
        lines.extend([
            "",
            f"{C_BOLD}Recommended Config (Best Profit — {vm.symbol}):{C_RESET}",
            f"  SL ATR Mult: {C_GREEN}{bp.get('sl_atr_mult')}{C_RESET}",
            f"  Trailing:    {C_GREEN}{bp.get('trailing_atr_mult')}{C_RESET}",
        ])
        if _is_cdc_family(vm.strategy_name):
            lines.extend([
                f"  RSI Min/Max: {C_GREEN}{bp.get('rsi_min', 0.0):.0f}/{bp.get('rsi_max', 0.0):.0f}{C_RESET}",
                f"  Vol Ratio:   {C_GREEN}>={bp.get('vol_min_ratio')}{C_RESET}",
            ])
        lines.append(
            f"  Net Profit:  {C_GREEN}+{bp.get('net_profit_pct', 0.0):.1f}%{C_RESET} (PF: {C_GREEN}{bp.get('profit_factor', 0.0):.2f}{C_RESET})"
        )

    return lines


def format_parity_lines(vm: BacktestViewModel, width: int, *, compact: Optional[bool] = None) -> List[str]:
    if vm.status in ("idle", "running"):
        return [
            f"{C_MUTED}Parity checks run after backtest completes.{C_RESET}",
        ]

    phone = compact if compact is not None else _is_compact(width)
    lines: List[str] = []
    for check in vm.parity_checks:
        if phone:
            mark = f"{C_GREEN}OK{C_RESET}" if check.passed else f"{C_RED}X{C_RESET}"
            short = check.name.replace("()", "")
            lines.append(f"{short:<8} {mark}")
        else:
            lines.append(_parity_status_line(check))

    conf_color = C_GREEN if vm.confidence >= 75 else (C_YELLOW if vm.confidence >= 50 else C_RED)
    if phone:
        lines.append(f"Conf {conf_color}{vm.confidence}/100{C_RESET}")
    else:
        lines.append("")
        lines.append(f"{C_BOLD}Confidence{C_RESET}      {conf_color}{vm.confidence}/100{C_RESET}")
    return lines
