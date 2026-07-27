"""Certification protocol for an XAU config: acceptance gate + WFA + bootstrap.

Runs the repo's own pre-registered criteria against candidate XAU configs rather
than against a config chosen after seeing the results. Three stages:

1. **Acceptance gate** (`bot_config.yaml -> backtest.acceptance`): a long+short
   config is only admissible if it is net-positive AND beats the equivalent
   long-only config by at least `min_profit_edge_pp`. This is the codified,
   pre-registered bar for turning shorts on. Implemented independently in
   `scripts/evaluate_okx_xau_migration.py::evaluate`; here it is applied through
   the real strategy plugin so the full deployed config (ROI ladder, fresh-zone
   window, sizing) is in play.

2. **Walk-forward**: the config is frozen and replayed month by month across the
   whole series, with a warmup lead-in and no per-window re-optimization, in the
   same format the BTC certificate reports.

3. **Bootstrap**: monthly returns are resampled with replacement to put a
   confidence interval on the compounded result and estimate P(profitable). A
   single compounded number over one gold cycle is not evidence of stability.

Stages 2 and 3 go through `xauby.backtest.walkforward` via
`scripts/xau_harness.py`. The version of this script that produced the first
published certificate did its own windowing and traded the warmup lead-in, which
inflated every monthly figure; the library makes that unrepresentable.

Usage:
    PYTHONPATH=. python3 scripts/certify_xau_candidate.py
    PYTHONPATH=. python3 scripts/certify_xau_candidate.py --variant "long-only D1 on"
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Dict, List

import pandas as pd

from xauby.backtest.walkforward import aggregate_by_phase, month_windows
from xauby.observability.replay_validation import load_bot_config
from scripts.xau_harness import (
    GATE_BASELINE,
    PROXY,
    VARIANTS,
    bootstrap,
    load_frames,
    prepare,
    print_variant_banner,
    run_continuous,
    run_windowed,
)


def acceptance_gate(continuous: Dict[str, Dict[str, Any]],
                    min_edge: float) -> List[Dict[str, Any]]:
    rows = []
    for candidate, baseline in GATE_BASELINE.items():
        candidate_net = continuous[candidate]["net_pct"]
        baseline_net = continuous[baseline]["net_pct"]
        edge = candidate_net - baseline_net
        rows.append({
            "long_short": candidate, "long_only": baseline,
            "ls_net": candidate_net, "lo_net": baseline_net,
            "edge_pp": round(edge, 2),
            "passed": bool(candidate_net > 0.0 and edge >= min_edge),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="bot_config.yaml")
    ap.add_argument("--variant", default=None, choices=list(VARIANTS), metavar="NAME",
                    help="Config to walk-forward. Default: every config that "
                         "clears the acceptance gate, plus the long-only baselines.")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    cfg = load_bot_config(args.config)
    acceptance = ((cfg.get("backtest") or {}).get("acceptance") or {})
    min_edge = float(acceptance.get("min_profit_edge_pp", 5.0))

    prep = prepare(cfg)
    df4, df1 = load_frames()
    span = (f"{pd.to_datetime(df4['open_time'].iloc[0], unit='ms').date()} -> "
            f"{pd.to_datetime(df4['open_time'].iloc[-1], unit='ms').date()}")
    print(f"XAU certification — {PROXY} 4h, {len(df4)} bars, {span}")
    print_variant_banner(prep)
    print()

    print("=== STAGE 1: acceptance gate (pre-registered) ===")
    print(f"criterion: long_short net > 0 AND edge >= {min_edge:.2f}pp "
          f"(backtest.acceptance)\n")
    continuous: Dict[str, Dict[str, Any]] = {}
    for name, overrides in VARIANTS.items():
        stats = run_continuous(prep, df4, df1, overrides, engine_config=cfg)
        continuous[name] = {
            "net_pct": float(stats.get("net_profit_pct", 0.0) or 0.0),
            "profit_factor": float(stats.get("profit_factor", 0.0) or 0.0),
            "max_drawdown_pct": float(stats.get("max_drawdown_pct", 0.0) or 0.0),
            "sharpe": float(stats.get("sharpe", 0.0) or 0.0),
            "calmar": float(stats.get("calmar", 0.0) or 0.0),
            "trades": int(stats.get("total_trades", 0) or 0),
        }

    gate = acceptance_gate(continuous, min_edge)
    for row in gate:
        print(f"  {row['long_short']:20s} {row['ls_net']:7.2f}%  vs  "
              f"{row['long_only']:20s} {row['lo_net']:7.2f}%   "
              f"edge {row['edge_pp']:+7.2f}pp  -> "
              f"{'PASS' if row['passed'] else 'FAIL'}")

    admissible = [row["long_short"] for row in gate if row["passed"]]
    print(f"\n  admissible long+short configs: {admissible or 'NONE'}")

    print("\n=== continuous reference (all cells) ===")
    hdr = (f"{'config':20s} {'n':>4s} {'PF':>5s} {'net%':>8s} {'MDD%':>6s} "
           f"{'Sharpe':>6s} {'Calmar':>6s}")
    print(hdr)
    print("-" * len(hdr))
    for name, row in continuous.items():
        print(f"{name:20s} {row['trades']:4d} {row['profit_factor']:5.2f} "
              f"{row['net_pct']:8.2f} {row['max_drawdown_pct']:6.2f} "
              f"{row['sharpe']:6.2f} {row['calmar']:6.2f}")

    targets = ([args.variant] if args.variant
               else admissible + ["long-only D1 on", "long-only D1 off"])
    seen = set()
    targets = [t for t in targets if not (t in seen or seen.add(t))]

    windows = month_windows(df4)
    results: Dict[str, Any] = {}
    for variant in targets:
        print(f"\n=== STAGE 2: walk-forward — {variant} (frozen config) ===")
        window_results = run_windowed(prep, df4, df1, VARIANTS[variant],
                                      windows=windows)
        by_phase = aggregate_by_phase(window_results)
        print(f"{len(window_results)} complete months, "
              f"{window_results[0].window.label} -> "
              f"{window_results[-1].window.label}")
        head = (f"  {'phase':9s} {'mo':>3s} {'+mo':>4s} {'comp%':>9s} "
                f"{'worst%':>7s} {'n':>4s}")
        print(head)
        print("  " + "-" * (len(head) - 2))
        for phase in ("bull", "bear", "sideways", "unknown", "ALL"):
            agg = by_phase.get(phase)
            if not agg or not agg.get("windows"):
                continue
            print(f"  {phase:9s} {agg['windows']:3d} {agg['positive_windows']:4d} "
                  f"{agg['compounded_pct']:9.2f} {agg['worst_window_pct']:7.2f} "
                  f"{agg['trades']:4d}")

        print(f"\n=== STAGE 3: bootstrap — {variant} ===")
        boot = bootstrap([r.net_pct for r in window_results])
        if boot.get("samples"):
            print(f"  {boot['samples']} resamples of "
                  f"{boot['windows_resampled']} monthly returns")
            print(f"  compounded: median {boot['median_pct']:+.2f}%  "
                  f"90% CI [{boot['p05_pct']:+.2f}%, {boot['p95_pct']:+.2f}%]")
            print(f"  P(profitable) = {boot['prob_profitable']:.2%}")
        else:
            print(f"  {boot.get('note')}")
        results[variant] = {
            "by_phase": by_phase,
            "bootstrap": boot,
            "months": {r.window.label: {"phase": r.phase,
                                        "net_pct": round(r.net_pct, 2),
                                        "trades": r.trades}
                       for r in window_results},
        }

    print("\n=== VERDICT ===")
    if not admissible:
        print("  Shorts do NOT clear the pre-registered acceptance gate on this data.")
        print("  No long+short XAU config is certifiable as-is.")
    for variant in targets:
        boot = results[variant]["bootstrap"]
        overall = results[variant]["by_phase"]["ALL"]
        gate_note = ("gate n/a (long-only)" if variant.startswith("long-only")
                     else ("gate PASS" if variant in admissible else "gate FAIL"))
        print(f"  {variant:20s} {gate_note:20s} "
              f"WFA {overall['positive_windows']}/{overall['windows']} +months, "
              f"P(profit) {boot.get('prob_profitable', 0):.1%}")

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump({"continuous": continuous, "gate": gate,
                       "min_edge_pp": min_edge, "results": results}, fh, indent=2)
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
