#!/usr/bin/env python3
"""Optimize per-pair strategy params and write to bot_config.yaml."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xauby.backtest.engine import optimize_pair_strategy_extended
from xauby.backtest.service import run_focused_backtest
from scripts.select_pair_strategy import apply_strategy_symbol_params
from xauby.runtime.symbol_resolver import active_symbols_from_config
from xauby.runtime.trading_config import strategy_name_for_symbol
from xauby.runtime.strategy_pair_config import backtest_data_proxy_for_symbol
from xauby.observability.replay_validation import load_bot_config


def _default_pairs() -> list[str]:
    try:
        return active_symbols_from_config()
    except Exception:
        return []

# Keys persisted to strategy.symbols.<SYMBOL> (exclude backtest metrics)
PERSIST_KEYS = (
    "ap_smoothing",
    "require_fresh_zone",
    "fresh_zone_window",
    "sl_atr_mult",
    "rsi_min",
    "rsi_max",
    "vol_min_ratio",
    "trailing_atr_mult",
    "breakeven_sl_enabled",
    "breakeven_activation_atr_mult",
    "breakeven_buffer_atr_mult",
    "backtest_data_proxy",
)


def _persist_params(symbol: str, best: dict) -> dict:
    params = {k: best[k] for k in PERSIST_KEYS if k in best}
    # Preserve any backtest proxy already configured for this symbol (e.g. an
    # illiquid pair backtested against a liquid analogue) instead of hardcoding
    # a per-asset mapping here.
    configured_proxy = backtest_data_proxy_for_symbol(symbol)
    if configured_proxy:
        params.setdefault("backtest_data_proxy", configured_proxy)
    cfg = load_bot_config()
    apply_strategy_symbol_params(
        "bot_config.yaml",
        symbol,
        params,
        strategy_name=strategy_name_for_symbol(cfg, symbol),
    )
    return params


def main() -> None:
    report = {}
    pairs = _default_pairs()
    if not pairs:
        cfg = load_bot_config()
        pairs = list((cfg.get("data") or {}).get("pairs") or [])
    for sym in pairs:
        print(f"\n=== Optimizing {sym} ===", flush=True)

        def progress(i: int, total: int) -> None:
            if i % 20 == 0 or i == total:
                print(f"  {i}/{total}", flush=True)

        best = optimize_pair_strategy_extended(sym, progress_callback=progress, save=True)
        if not best:
            print(f"No results for {sym}")
            continue
        if best.get("admissible") is False:
            # The optimizer declined: the sample could not support a selection.
            # Reported rather than skipped silently, because "no result" and
            # "the result was a coin flip" used to look identical from here.
            verdict = best.get("verdict") or {}
            print(f"DECLINED {sym}: {verdict.get('reason', 'unspecified')}", flush=True)
            report[sym] = {"declined": verdict}
            continue

        params = _persist_params(sym, best)
        verify = run_focused_backtest(sym)
        stats = verify.stats or {}
        meta = verify.meta

        report[sym] = {
            "best": best,
            "saved_params": params,
            "verify": {
                "net_profit_pct": stats.get("net_profit_pct"),
                "profit_factor": stats.get("profit_factor"),
                "win_rate": stats.get("win_rate"),
                "total_trades": stats.get("total_trades"),
                "max_drawdown_pct": stats.get("max_drawdown_pct"),
                "data_symbol": meta.data_symbol,
                "used_data_proxy": meta.used_data_proxy,
                "bars": meta.bars,
            },
        }
        print(
            f"Best {sym}: NP={best.get('net_profit_pct', 0):+.2f}% "
            f"PF={best.get('profit_factor', 0):.2f} "
            f"trades={best.get('total_trades')} "
            f"proxy={best.get('used_data_proxy')}",
            flush=True,
        )
        print(f"Saved params: {json.dumps(params, indent=2)}", flush=True)

    out_path = os.path.join("core", "pair_optimization_report.json")
    os.makedirs("core", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport written to {out_path}")


if __name__ == "__main__":
    main()
