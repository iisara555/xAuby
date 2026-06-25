#!/usr/bin/env python3
"""Evaluate candidate strategies per pair and optionally apply the best one.

The default mode is dry-run. Use --apply only after reviewing the report.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xauby.backtest.service import run_focused_backtest
from xauby.observability.replay_validation import load_bot_config
from xauby.runtime.trading_config import strategy_name_for_symbol


def _norm_symbol(symbol: str) -> str:
    return str(symbol or "").upper().replace("_", "")


def _candidate_strategies(cfg: Dict[str, Any], explicit: str) -> List[str]:
    if explicit:
        return [s.strip() for s in explicit.split(",") if s.strip()]
    names = list(((cfg.get("strategy") or {}).get("config") or {}).keys())
    active = str((cfg.get("strategy") or {}).get("active") or "").strip()
    if active and active not in names:
        names.insert(0, active)
    return names or ["cdc_action_zone"]


def _score(stats: Dict[str, Any]) -> float:
    pf = float(stats.get("profit_factor", 0.0) or 0.0)
    net = float(stats.get("net_profit_pct", 0.0) or 0.0)
    dd = abs(float(stats.get("max_drawdown_pct", 0.0) or 0.0))
    trades = int(stats.get("total_trades", 0) or 0)
    if trades <= 0:
        return -9999.0
    # Profit factor wins, but avoid picking a high-PF tiny sample with bad net/DD.
    return (pf * 100.0) + net - (dd * 0.5) + min(trades, 30) * 0.1


def _passes_gate(stats: Dict[str, Any], *, min_pf: float, min_trades: int, max_dd: float) -> bool:
    return (
        float(stats.get("profit_factor", 0.0) or 0.0) >= min_pf
        and int(stats.get("total_trades", 0) or 0) >= min_trades
        and abs(float(stats.get("max_drawdown_pct", 0.0) or 0.0)) <= max_dd
    )


def _apply_strategy(config_path: str, symbol: str, strategy_name: str) -> None:
    with open(config_path, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    strat = doc.setdefault("strategy", {})
    symbols = strat.setdefault("symbols", {})
    block = symbols.setdefault(symbol, {})
    block["strategy"] = strategy_name
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, sort_keys=False, allow_unicode=False)


def apply_strategy_symbol_params(
    config_path: str,
    symbol: str,
    params: Dict[str, Any],
    *,
    strategy_name: str = "",
) -> None:
    """Apply symbol-level strategy params to bot_config.yaml."""
    sym = _norm_symbol(symbol)
    with open(config_path, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    strat = doc.setdefault("strategy", {})
    symbols = strat.setdefault("symbols", {})
    block = symbols.setdefault(sym, {})
    if strategy_name:
        block["strategy"] = strategy_name
    for key, value in params.items():
        if key not in {"strategy", "strategy_name"}:
            block[key] = value
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, sort_keys=False, allow_unicode=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, help="Symbol, e.g. XAUTUSDT or BTCUSDT")
    parser.add_argument("--config", default="bot_config.yaml")
    parser.add_argument("--candidates", default="", help="Comma-separated strategy names")
    parser.add_argument("--min-pf", type=float, default=1.05)
    parser.add_argument("--min-trades", type=int, default=5)
    parser.add_argument("--max-dd", type=float, default=10.0)
    parser.add_argument("--apply", action="store_true", help="Apply best passing strategy to config")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    symbol = _norm_symbol(args.symbol)
    cfg = load_bot_config(args.config)
    current = strategy_name_for_symbol(cfg, symbol)
    rows: List[Dict[str, Any]] = []

    for name in _candidate_strategies(cfg, args.candidates):
        result = run_focused_backtest(
            symbol,
            strategy_name=name,
            engine_config=cfg,
            bot_state=None,
        )
        stats = result.stats or {}
        row = {
            "strategy_name": name,
            "run_ok": bool(result.meta.run_ok),
            "error": result.meta.error,
            "score": _score(stats) if result.meta.run_ok else -9999.0,
            "passes_gate": _passes_gate(
                stats,
                min_pf=args.min_pf,
                min_trades=args.min_trades,
                max_dd=args.max_dd,
            ) if result.meta.run_ok else False,
            "net_profit_pct": stats.get("net_profit_pct"),
            "profit_factor": stats.get("profit_factor"),
            "win_rate": stats.get("win_rate"),
            "total_trades": stats.get("total_trades"),
            "max_drawdown_pct": stats.get("max_drawdown_pct"),
            "bars": result.meta.bars,
            "data_symbol": result.meta.data_symbol,
            "used_data_proxy": result.meta.used_data_proxy,
        }
        rows.append(row)

    rows.sort(key=lambda r: r["score"], reverse=True)
    best = rows[0] if rows else {}
    applied = False
    if args.apply and best and best.get("passes_gate"):
        _apply_strategy(args.config, symbol, str(best["strategy_name"]))
        applied = True

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "current_strategy": current,
        "best_strategy": best.get("strategy_name") if best else "",
        "applied": applied,
        "gate": {
            "min_pf": args.min_pf,
            "min_trades": args.min_trades,
            "max_dd": args.max_dd,
        },
        "results": rows,
    }

    os.makedirs("core", exist_ok=True)
    out_path = os.path.join("core", f"strategy_selection_{symbol.lower()}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Strategy selection for {symbol} (current={current})")
        for r in rows:
            gate = "PASS" if r["passes_gate"] else "fail"
            print(
                f"  {r['strategy_name']:<24} {gate:<4} "
                f"PF={float(r.get('profit_factor') or 0):.2f} "
                f"Net={float(r.get('net_profit_pct') or 0):+.2f}% "
                f"DD={float(r.get('max_drawdown_pct') or 0):.2f}% "
                f"Trades={int(r.get('total_trades') or 0)}"
            )
        print(f"Report: {out_path}")
        if args.apply:
            print("Applied." if applied else "Not applied: best candidate did not pass gate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
