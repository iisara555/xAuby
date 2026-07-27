"""Does a short-bearing XAU config earn its keep in bear phases?

Judging a config by a whole-period profit factor averages its short side across a
gold market that was rising most of the time, which hides exactly the thing
shorts are for. So this splits the record by market phase instead of averaging
over it, and runs the configs over the SAME candles so any difference is
attributable to the config alone.

Phase labels follow the BTC certificate's convention: 1d close vs EMA200 plus
EMA slope, computed from closes STRICTLY BEFORE each test month, so no
look-ahead. Months whose phase cannot be labelled are reported as `unknown` and
still counted in `ALL` — dropping them, as the first version of this script did,
silently shortened the record from 48 months to 40.

Everything runs through `scripts/xau_harness.py`, so the variant table, the
warmup handling and the phase labels are shared with every other XAU study.

Usage:
    PYTHONPATH=. python3 scripts/xau_phase_breakdown.py
    PYTHONPATH=. python3 scripts/xau_phase_breakdown.py \\
        --variants "long+short D1 off" "long-only D1 on" --json-out out.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Dict

from xauby.backtest.walkforward import aggregate, aggregate_by_phase, month_windows
from xauby.observability.replay_validation import load_bot_config
from scripts.xau_harness import (
    DRAWDOWN_FROM,
    PROXY,
    VARIANTS,
    drawdown_slice,
    load_frames,
    prepare,
    print_variant_banner,
    run_windowed,
)

# The pair the original study compared: what ran live before 2026-07-26 against
# what the July certificate actually measured.
DEFAULT_PAIR = ("long+short D1 off", "long-only D1 on")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="bot_config.yaml")
    ap.add_argument("--variants", nargs="+", default=list(DEFAULT_PAIR),
                    choices=list(VARIANTS), metavar="NAME",
                    help=f"variants to compare; choose from {list(VARIANTS)}")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    cfg = load_bot_config(args.config)
    prep = prepare(cfg)
    df4, df1 = load_frames()
    windows = month_windows(df4)

    print(f"XAU phase breakdown — {PROXY} 4h, {len(windows)} complete months, "
          f"{windows[0].label} -> {windows[-1].label}")
    print_variant_banner(prep)
    print()

    runs = {name: run_windowed(prep, df4, df1, VARIANTS[name], windows=windows)
            for name in args.variants}

    hdr = (f"{'config':22s} {'phase':9s} {'mo':>3s} {'+mo':>4s} {'comp%':>9s} "
           f"{'avg%':>7s} {'worst%':>7s} {'best%':>7s} {'n':>4s}")
    print(hdr)
    print("-" * len(hdr))
    summary: Dict[str, Dict[str, Any]] = {}
    for name in args.variants:
        by_phase = aggregate_by_phase(runs[name])
        summary[name] = by_phase
        for phase in ("bull", "bear", "sideways", "unknown", "ALL"):
            agg = by_phase.get(phase)
            if not agg or not agg.get("windows"):
                continue
            print(f"{name:22s} {phase:9s} {agg['windows']:3d} "
                  f"{agg['positive_windows']:4d} {agg['compounded_pct']:9.2f} "
                  f"{agg['avg_window_pct']:7.3f} {agg['worst_window_pct']:7.2f} "
                  f"{agg['best_window_pct']:7.2f} {agg['trades']:4d}")
        print()

    if len(args.variants) == 2:
        left, right = args.variants
        print(f"Head-to-head by phase (compounded %, {left} minus {right}):")
        for phase in ("bull", "bear", "sideways", "ALL"):
            a = summary[left].get(phase, {}).get("compounded_pct")
            b = summary[right].get(phase, {}).get("compounded_pct")
            if a is None or b is None:
                continue
            print(f"  {phase:9s} {a:8.2f}   {b:8.2f}   delta {a - b:+8.2f}  "
                  f"-> {left if a > b else right}")

    # The live-relevant slice: gold peaked in 2026-02 and fell into 2026-06, so
    # this is the regime a short-bearing config is built for. Phase labels lag
    # (the EMA200 slope stayed up for months into the decline), which is exactly
    # why this is reported separately from the bull/bear/sideways split.
    print(f"\nCurrent gold drawdown ({DRAWDOWN_FROM} -> {windows[-1].label}, "
          f"peak 2026-02):")
    drawdown: Dict[str, Any] = {}
    for name in args.variants:
        window_results = drawdown_slice(runs[name])
        agg = aggregate(window_results)
        drawdown[name] = {
            "aggregate": agg,
            "months": {r.window.label: round(r.net_pct, 2) for r in window_results},
        }
        print(f"  {name:22s} comp {agg['compounded_pct']:8.2f}%  "
              f"+mo {agg['positive_windows']}/{agg['windows']}  "
              f"worst {agg['worst_window_pct']:7.2f}%  n={agg['trades']}")
        print("    " + "  ".join(f"{k} {v:+6.2f}"
                                 for k, v in drawdown[name]["months"].items()))

    if args.json_out:
        months: Dict[str, Any] = {
            name: {r.window.label: {"phase": r.phase, "net_pct": round(r.net_pct, 2),
                                    "trades": r.trades}
                   for r in runs[name]}
            for name in args.variants
        }
        with open(args.json_out, "w") as fh:
            json.dump({"summary": summary, "drawdown": drawdown,
                       "months": months}, fh, indent=2)
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
