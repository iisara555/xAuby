#!/usr/bin/env python3
"""Preview or apply stored best backtest parameters to bot_config.yaml.

Default is dry-run. Use --apply to write, and a timestamped backup is created.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from typing import Any, Dict

import yaml

from xauby.backtest.best_params import load_best_params
from xauby.runtime.trading_config import strategy_name_for_symbol


METRIC_KEYS = {
    "net_profit_pct",
    "profit_factor",
    "win_rate",
    "total_trades",
    "mdd",
    "data_symbol",
    "used_data_proxy",
    "config_used",
    "run_ok",
    "error",
}


def _normalize_symbol(symbol: str) -> str:
    return str(symbol or "").upper().replace("_", "")


def _load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_yaml(path: str, cfg: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=False)


def build_patch(cfg: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    best = load_best_params(symbol)
    if not best:
        raise SystemExit(f"No best params found for {symbol}")
    patch = {k: v for k, v in best.items() if k not in METRIC_KEYS}
    strategy_name = patch.pop("strategy_name", None) or strategy_name_for_symbol(cfg, symbol)
    patch["strategy"] = strategy_name
    return patch


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", required=True, help="Symbol, e.g. BTCUSDT")
    ap.add_argument("--config", default="bot_config.yaml")
    ap.add_argument("--apply", action="store_true", help="Write bot_config.yaml. Default is dry-run.")
    args = ap.parse_args()

    symbol = _normalize_symbol(args.symbol)
    cfg = _load_yaml(args.config)
    patch = build_patch(cfg, symbol)
    current = dict(((cfg.get("strategy") or {}).get("symbols") or {}).get(symbol) or {})

    print(json.dumps({"symbol": symbol, "current": current, "proposed": patch}, indent=2, sort_keys=True, default=str))
    if not args.apply:
        print("\nDry run only. Re-run with --apply to write bot_config.yaml.")
        return 0

    backup = f"{args.config}.bak.{int(time.time())}"
    shutil.copy2(args.config, backup)
    target = cfg.setdefault("strategy", {}).setdefault("symbols", {}).setdefault(symbol, {})
    target.update(patch)
    _save_yaml(args.config, cfg)
    print(f"\nApplied best params to {args.config}; backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
