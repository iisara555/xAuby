"""Compare CDC AP smoothing and fresh-zone windows via live-parity replay.

This script intentionally delegates to ``run_focused_backtest`` instead of
reimplementing trade simulation. All non-tested parameters come from the same
merged config path used by the Backtest TUI and live runtime.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from xauby.backtest.service import run_focused_backtest


def _parse_csv_ints(value: str) -> list[int]:
    return [int(v.strip()) for v in str(value).split(",") if v.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare CDC AP/fresh settings using live-parity replay")
    parser.add_argument("--symbol", default="XAUTUSDT")
    parser.add_argument("--strategy", default="cdc_action_zone")
    parser.add_argument("--config", default="bot_config.yaml")
    parser.add_argument("--ap", default="1,2", help="Comma-separated ap_smoothing values")
    parser.add_argument("--windows", default="1,2,3,4", help="Comma-separated fresh_zone_window values")
    parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="Also test require_fresh_zone=false using the current fresh_zone_window value.",
    )
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    rows = []
    for ap in _parse_csv_ints(args.ap):
        for win in _parse_csv_ints(args.windows):
            override = {
                "ap_smoothing": ap,
                "require_fresh_zone": True,
                "fresh_zone_window": win,
            }
            result = run_focused_backtest(
                args.symbol,
                strategy_name=args.strategy,
                engine_config=cfg,
                strat_cfg_override=override,
            )
            if result.meta.run_ok:
                rows.append((override, result.stats, result.meta))

        if args.include_disabled:
            override = {
                "ap_smoothing": ap,
                "require_fresh_zone": False,
            }
            result = run_focused_backtest(
                args.symbol,
                strategy_name=args.strategy,
                engine_config=cfg,
                strat_cfg_override=override,
            )
            if result.meta.run_ok:
                rows.append((override, result.stats, result.meta))

    if not rows:
        raise SystemExit("No successful backtest rows")

    first_meta = rows[0][2]
    if first_meta.used_data_proxy:
        print(f"Data proxy: {first_meta.data_symbol} (limited {args.symbol} history)")
    print(
        f"Backtest: {first_meta.symbol} {first_meta.primary_tf} "
        f"{first_meta.period_start}-{first_meta.period_end} bars={first_meta.bars}"
    )
    header = f"{'AP':>3s} {'fresh':>8s} {'net%':>8s} {'win%':>7s} {'trades':>7s} {'mdd%':>7s} {'PF':>6s}"
    print(header)
    print("-" * len(header))

    for override, stats, _meta in rows:
        fresh = (
            str(override.get("fresh_zone_window"))
            if override.get("require_fresh_zone")
            else "off"
        )
        print(
            f"{int(override['ap_smoothing']):>3d} {fresh:>8s} "
            f"{float(stats.get('net_profit_pct', 0.0)):8.2f} "
            f"{float(stats.get('win_rate', 0.0)):7.1f} "
            f"{int(stats.get('total_trades', 0)):7d} "
            f"{float(stats.get('max_drawdown_pct', 0.0)):7.2f} "
            f"{float(stats.get('profit_factor', 0.0)):6.2f}"
        )

    best = max(
        rows,
        key=lambda row: (
            float(row[1].get("net_profit_pct", 0.0)),
            float(row[1].get("profit_factor", 0.0)),
        ),
    )
    best_override, best_stats, _ = best
    fresh_label = (
        str(best_override.get("fresh_zone_window"))
        if best_override.get("require_fresh_zone")
        else "off"
    )
    print(
        f"\nBEST -> ap_smoothing={best_override['ap_smoothing']} "
        f"fresh_zone={fresh_label} "
        f"(net {float(best_stats.get('net_profit_pct', 0.0)):.2f}%, "
        f"win {float(best_stats.get('win_rate', 0.0)):.1f}%, "
        f"PF {float(best_stats.get('profit_factor', 0.0)):.2f})"
    )


if __name__ == "__main__":
    main()
