#!/usr/bin/env python3
"""Report per-pair live/backtest config parity.

This is intentionally read-only. It resolves the same pair registry and
effective trading config used by live/backtest code, then optionally runs
focused backtests for each active pair.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List

from xauby.backtest.service import run_focused_backtest
from xauby.observability.replay_validation import load_bot_config
from xauby.runtime.pair_registry import PairRegistry
from xauby.runtime.trading_config import resolve_trading_config


STATE_PATH = os.path.join("core", "logs", "xauby_bot_state.json")


def _load_state() -> Dict[str, Any]:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _stat_subset(stats: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "net_profit_pct": stats.get("net_profit_pct"),
        "profit_factor": stats.get("profit_factor"),
        "max_drawdown_pct": stats.get("max_drawdown_pct"),
        "total_trades": stats.get("total_trades"),
        "win_rate": stats.get("win_rate"),
    }


def build_report(*, include_backtest: bool = False) -> Dict[str, Any]:
    cfg = load_bot_config()
    state = _load_state()
    registry = PairRegistry(cfg)
    pairs = registry.load(None)

    rows: List[Dict[str, Any]] = []
    for pair in pairs:
        eff = resolve_trading_config(
            cfg,
            pair.strategy_name,
            symbol=pair.symbol,
            for_live=True,
        )
        uses_regime = bool(eff.strategy.get("use_d1_regime_filter", False))
        registry_regime = pair.regime_timeframe or ""
        effective_regime = eff.confirm_timeframe or ""
        row: Dict[str, Any] = {
            "symbol": pair.symbol,
            "enabled": pair.enabled,
            "strategy": pair.strategy_name,
            "registry_primary_tf": pair.primary_timeframe,
            "registry_confirm_tf": pair.confirm_timeframe,
            "effective_primary_tf": eff.primary_timeframe,
            "effective_confirm_tf": eff.confirm_timeframe,
            "risk_pct": eff.portfolio.get("risk_pct"),
            "max_position_per_trade_pct": eff.portfolio.get("max_position_per_trade_pct"),
            "strategy_config": {
                k: eff.strategy.get(k)
                for k in (
                    "sl_atr_mult",
                    "trailing_atr_mult",
                    "breakeven_sl_enabled",
                    "use_d1_regime_filter",
                    "rsi_min",
                    "rsi_max",
                    "vol_min_ratio",
                    "ema_fast",
                    "ema_slow",
                    "reclaim_window",
                    "mss_lookback",
                    "require_mss",
                )
                if k in eff.strategy
            },
            "uses_regime": uses_regime,
            "parity_ok": (
                pair.strategy_name == eff.strategy_name
                and pair.primary_timeframe == eff.primary_timeframe
                and (not uses_regime or registry_regime == effective_regime)
            ),
        }
        if include_backtest:
            bt = run_focused_backtest(
                pair.symbol,
                strategy_name=pair.strategy_name,
                engine_config=cfg,
            )
            row["backtest"] = {
                "ok": bt.meta.run_ok,
                "error": bt.meta.error,
                "bars": bt.meta.bars,
                "data_symbol": bt.meta.data_symbol,
                "used_data_proxy": bt.meta.used_data_proxy,
                "stats": _stat_subset(bt.stats or {}),
                "config_used": bt.meta.config_used,
            }
        rows.append(row)

    return {
        "state": {
            "pid": state.get("pid"),
            "run_id": state.get("run_id"),
            "simulate_only": state.get("simulate_only"),
            "read_only": state.get("read_only"),
            "focus_symbol": state.get("focus_symbol"),
            "open_positions": state.get("open_positions"),
        },
        "pairs": rows,
    }


def print_human(report: Dict[str, Any]) -> None:
    state = report.get("state") or {}
    print("xAuby Live/Backtest Parity Report")
    print(f"State: pid={state.get('pid')} run_id={state.get('run_id')} simulate_only={state.get('simulate_only')} read_only={state.get('read_only')}")
    print("")
    for row in report.get("pairs") or []:
        mark = "OK" if row.get("parity_ok") else "CHECK"
        print(
            f"[{mark}] {row['symbol']} strategy={row['strategy']} "
            f"tf={row['effective_primary_tf']}/{row.get('effective_confirm_tf') or '-'} "
            f"risk_pct={row.get('risk_pct')}"
        )
        bt = row.get("backtest")
        if bt:
            stats = bt.get("stats") or {}
            print(
                "     backtest: "
                f"net={float(stats.get('net_profit_pct') or 0):+.2f}% "
                f"pf={float(stats.get('profit_factor') or 0):.2f} "
                f"dd={float(stats.get('max_drawdown_pct') or 0):.2f}% "
                f"trades={int(stats.get('total_trades') or 0)}"
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backtest", action="store_true", help="Run focused backtest for each active pair.")
    ap.add_argument("--json", action="store_true", help="Emit JSON.")
    args = ap.parse_args()
    report = build_report(include_backtest=args.backtest)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
