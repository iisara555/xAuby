"""Regenerate every windowed XAU number through xauby.backtest.walkforward.

The figures in xau_regime_attribution_2026-07-26.md,
xau_d1_short_matrix_2026-07-26.md, xau_per_side_d1_test_2026-07-26.md and the
WFA/bootstrap stage of xau_certification_2026-07-26.md were produced by ad-hoc
harnesses carrying two defects (see xauby/backtest/walkforward.py):

  * the warmup lead-in was traded, so consecutive months overlapped by ~200 bars;
  * variant overrides set only `use_d1_regime_filter`, so when the base config
    gained `use_d1_regime_filter_long` the six cells collapsed into two.

This script re-derives them all in one pass through the library, whose
WindowSlice makes the first impossible and whose resolve_variant rejects the
second. Continuous full-frame results are NOT regenerated here: they never used
windowing and are unaffected — `scripts/certify_xau_candidate.py` and
`scripts/xau_d1_short_matrix.py` report those.

Usage:
    PYTHONPATH=. python3 scripts/xau_windowed_regen.py --json-out out.json
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
    WARMUP_BARS,
    bootstrap,
    deployed_variant_name,
    drawdown_slice,
    load_frames,
    prepare,
    run_windowed,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="bot_config.yaml")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    cfg = load_bot_config(args.config)
    prep = prepare(cfg)
    df4, df1 = load_frames()
    live = deployed_variant_name(prep.base_strategy_config)

    windows = month_windows(df4)
    print(f"XAU windowed regeneration — {PROXY} 4h, {len(windows)} complete months, "
          f"{windows[0].label} -> {windows[-1].label}")
    print(f"warmup {WARMUP_BARS} bars, never traded (WindowSlice.skip_bars)\n")

    out: Dict[str, Any] = {"windows": len(windows), "deployed": live, "variants": {}}

    for label, overrides in VARIANTS.items():
        results = run_windowed(prep, df4, df1, overrides, windows=windows,
                               warmup_bars=WARMUP_BARS)
        by_phase = aggregate_by_phase(results)
        drawdown = drawdown_slice(results)
        drawdown_agg = aggregate(drawdown)

        out["variants"][label] = {
            "by_phase": by_phase,
            "drawdown_window": drawdown_agg,
            "drawdown_months": {r.window.label: round(r.net_pct, 2) for r in drawdown},
            "bootstrap": bootstrap([r.net_pct for r in results]),
            "months": {r.window.label: {"phase": r.phase, "net": round(r.net_pct, 2),
                                        "n": r.trades} for r in results},
        }

        suffix = "  (DEPLOYED)" if label == live else ""
        print(f"=== {label}{suffix} ===")
        head = (f"  {'phase':9s} {'mo':>3s} {'+mo':>4s} {'comp%':>9s} "
                f"{'worst%':>7s} {'n':>4s}")
        print(head)
        for phase in ("bull", "bear", "sideways", "unknown", "ALL"):
            agg = by_phase.get(phase)
            if not agg or not agg.get("windows"):
                continue
            print(f"  {phase:9s} {agg['windows']:3d} {agg['positive_windows']:4d} "
                  f"{agg['compounded_pct']:9.2f} {agg['worst_window_pct']:7.2f} "
                  f"{agg['trades']:4d}")
        print(f"  drawdown {DRAWDOWN_FROM}+: {drawdown_agg['compounded_pct']:+.2f}% "
              f"({drawdown_agg['positive_windows']}/{drawdown_agg['windows']} +mo, "
              f"n={drawdown_agg['trades']})  "
              + " ".join(f"{k} {v:+.2f}"
                         for k, v in out["variants"][label]["drawdown_months"].items()))
        boot = out["variants"][label]["bootstrap"]
        if boot.get("samples"):
            print(f"  bootstrap: median {boot['median_pct']:+.2f}%  "
                  f"90% CI [{boot['p05_pct']:+.2f}, {boot['p95_pct']:+.2f}]  "
                  f"P(profit) {boot['prob_profitable']:.2%}")
        print()

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(out, fh, indent=2, default=str)
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
