"""Focused-symbol replay backtest using the same service as the TUI."""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from xauby.backtest.service import run_focused_backtest


def main():
    parser = argparse.ArgumentParser(description="Replay backtest via live-merged strategy config")
    parser.add_argument("--symbol", default="XAUTUSDT")
    parser.add_argument("--strategy", default=None)
    parser.add_argument("--config", default="bot_config.yaml")
    parser.add_argument(
        "--timeframe",
        "--primary-timeframe",
        dest="primary_timeframe",
        default=None,
        help="Primary candle timeframe (e.g. 1h, 4h). Default: whitelist/config.",
    )
    parser.add_argument(
        "--confirm-timeframe",
        "--regime-timeframe",
        dest="regime_timeframe",
        default=None,
        help="Confirm/regime timeframe (e.g. 4h, 1d). Default: whitelist/config.",
    )
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    result = run_focused_backtest(
        args.symbol,
        strategy_name=args.strategy,
        engine_config=cfg,
        bot_state={
            "primary_timeframe": args.primary_timeframe or "",
            "confirm_timeframe": args.regime_timeframe or "",
        },
    )
    if not result.meta.run_ok:
        raise SystemExit(f"Backtest failed: {result.meta.error}")

    stats = result.stats
    if result.meta.used_data_proxy:
        print(f"Data proxy: {result.meta.data_symbol} (limited {args.symbol} history)")
    print(f"Primary TF: {result.meta.primary_tf} | Confirm TF: {result.meta.regime_tf or 'off'}")
    print(f"Config used: {result.meta.config_used}")
    print(f"Bars: {stats.get('bars', 0)}")
    for k, v in stats.items():
        if k != "bars":
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
