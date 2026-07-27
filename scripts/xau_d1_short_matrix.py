"""2x2: does D1 confirmation fix a long+short XAU config's bull-market problem?

Context. The config that ran live before 2026-07-26 (long+short, D1 off) beat the
July certificate's config (long-only, D1 on) in falling and choppy gold but lost
badly in sustained uptrends (docs/research/xau_regime_attribution_2026-07-26.md).
Those two configs differ in TWO ways at once, so neither result attributes to a
single cause.

`use_d1_regime_filter` in xauby_actionzone gates BOTH directions symmetrically
(longs need D1 in GREEN/YELLOW/ORANGE, shorts need RED/BLUE/LBLUE), so long+short
WITH the D1 filter is a legal, already-supported config that no certificate had
measured. This runs the full 2x2 over identical candles:

                    D1 off              D1 on
    long-only       (factorial cell)    cert config
    long+short      (was deployed)      <- the candidate

Three views per cell: a continuous 4y run (the headline numbers), a
month-by-month phase split, and the current gold drawdown. All of it goes
through `scripts/xau_harness.py` — in particular the windowed runs use
`xauby.backtest.walkforward`, so the warmup lead-in is never traded. The first
version of this script traded it, which inflated every monthly figure it
published.

Usage:
    PYTHONPATH=. python3 scripts/xau_d1_short_matrix.py
    PYTHONPATH=. python3 scripts/xau_d1_short_matrix.py --json-out out.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Dict, List

import pandas as pd

from xauby.backtest.walkforward import aggregate, aggregate_by_phase, month_windows
from xauby.observability.replay_validation import load_bot_config
from scripts.xau_harness import (
    DRAWDOWN_FROM,
    PROXY,
    VARIANTS,
    buy_and_hold,
    drawdown_slice,
    load_frames,
    prepare,
    print_variant_banner,
    run_continuous,
    run_windowed,
)

# The 2x2, in reading order. Names are the shared catalog's.
CELLS = ["long-only D1 off", "long-only D1 on",
         "long+short D1 off", "long+short D1 on"]

CONT_KEYS = [
    ("trades", "total_trades"),
    ("win_rate", "win_rate"),
    ("profit_factor", "profit_factor"),
    ("net_profit_pct", "net_profit_pct"),
    ("max_drawdown_pct", "max_drawdown_pct"),
    ("cagr_pct", "cagr_pct"),
    ("calmar", "calmar"),
    ("sharpe", "sharpe"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="bot_config.yaml")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    cfg = load_bot_config(args.config)
    prep = prepare(cfg)
    df4, df1 = load_frames()

    span = (f"{pd.to_datetime(df4['open_time'].iloc[0], unit='ms').date()} -> "
            f"{pd.to_datetime(df4['open_time'].iloc[-1], unit='ms').date()}")
    print(f"XAU 2x2 — {PROXY} 4h, {len(df4)} bars, {span}")
    print_variant_banner(prep)
    print()

    print("=== CONTINUOUS RUN (headline; no monthly reset) ===")
    continuous: List[Dict[str, Any]] = []
    for name in CELLS:
        stats = run_continuous(prep, df4, df1, VARIANTS[name], engine_config=cfg)
        row: Dict[str, Any] = {"variant": name}
        for out_key, stat_key in CONT_KEYS:
            row[out_key] = stats.get(stat_key)
        continuous.append(row)

    hdr = (f"{'variant':22s} {'n':>4s} {'WR%':>6s} {'PF':>5s} {'net%':>8s} "
           f"{'MDD%':>6s} {'CAGR%':>6s} {'Calmar':>6s} {'Sharpe':>6s}")
    print(hdr)
    print("-" * len(hdr))
    for row in continuous:
        print(f"{row['variant']:22s} {row['trades']:4d} {row['win_rate']:6.2f} "
              f"{row['profit_factor']:5.2f} {row['net_profit_pct']:8.2f} "
              f"{row['max_drawdown_pct']:6.2f} {row['cagr_pct']:6.2f} "
              f"{row['calmar']:6.2f} {row['sharpe']:6.2f}")
    bh_full = buy_and_hold(
        df1, str(pd.to_datetime(df4["open_time"].iloc[0], unit="ms").date())
    )
    print(f"{'buy & hold gold':22s} {'-':>4s} {'-':>6s} {'-':>5s} "
          f"{bh_full['return_pct']:8.2f} {bh_full['max_dd_pct']:6.2f}")

    print("\n=== BY PHASE (monthly reset; attribution only) ===")
    windows = month_windows(df4)
    print(f"{len(windows)} complete months, {windows[0].label} -> "
          f"{windows[-1].label}\n")
    runs = {name: run_windowed(prep, df4, df1, VARIANTS[name], windows=windows)
            for name in CELLS}

    h2 = (f"{'variant':22s} {'phase':9s} {'mo':>3s} {'+mo':>4s} {'comp%':>9s} "
          f"{'worst%':>7s} {'n':>4s}")
    print(h2)
    print("-" * len(h2))
    phase_summary: Dict[str, Any] = {}
    for name in CELLS:
        by_phase = aggregate_by_phase(runs[name])
        phase_summary[name] = by_phase
        for phase in ("bull", "bear", "sideways", "unknown", "ALL"):
            agg = by_phase.get(phase)
            if not agg or not agg.get("windows"):
                continue
            print(f"{name:22s} {phase:9s} {agg['windows']:3d} "
                  f"{agg['positive_windows']:4d} {agg['compounded_pct']:9.2f} "
                  f"{agg['worst_window_pct']:7.2f} {agg['trades']:4d}")
        print()

    print(f"=== CURRENT GOLD DRAWDOWN ({DRAWDOWN_FROM} -> {windows[-1].label}; "
          f"peak 2026-02) ===")
    drawdown: Dict[str, Any] = {}
    for name in CELLS:
        window_results = drawdown_slice(runs[name])
        agg = aggregate(window_results)
        drawdown[name] = {
            "aggregate": agg,
            "months": {r.window.label: round(r.net_pct, 2) for r in window_results},
        }
        print(f"  {name:22s} comp {agg['compounded_pct']:8.2f}%  "
              f"+mo {agg['positive_windows']}/{agg['windows']}  "
              f"worst {agg['worst_window_pct']:7.2f}%  n={agg['trades']}")
    bh_dd = buy_and_hold(df1, DRAWDOWN_FROM + "-01")
    print(f"  {'buy & hold gold':22s} comp {bh_dd['return_pct']:8.2f}%")

    if args.json_out:
        months = {
            name: {r.window.label: {"phase": r.phase, "net_pct": round(r.net_pct, 2),
                                    "trades": r.trades}
                   for r in runs[name]}
            for name in CELLS
        }
        with open(args.json_out, "w") as fh:
            json.dump({"continuous": continuous, "phase": phase_summary,
                       "drawdown": drawdown, "months": months,
                       "buy_hold": {"full": bh_full, "drawdown": bh_dd}},
                      fh, indent=2)
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
