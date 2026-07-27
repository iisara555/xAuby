"""Regenerate every windowed XAU number through xauby.backtest.walkforward.

The figures in xau_regime_attribution_2026-07-26.md,
xau_per_side_d1_test_2026-07-26.md and the WFA/bootstrap stage of
xau_certification_2026-07-26.md were produced by ad-hoc harnesses carrying two
defects (see xauby/backtest/walkforward.py):

  * the warmup lead-in was traded, so consecutive months overlapped by ~200 bars;
  * variant overrides set only `use_d1_regime_filter`, so when the base config
    gained `use_d1_regime_filter_long` the six cells collapsed into two.

This script re-derives them with the library, whose WindowSlice makes the first
impossible and whose resolve_variant rejects the second. Continuous full-frame
results are NOT regenerated here: they never used windowing and are unaffected.

Usage:
    PYTHONPATH=. python3 scripts/xau_windowed_regen.py --json-out out.json
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Dict, List

import pandas as pd

from xauby.backtest.walkforward import (
    aggregate,
    aggregate_by_phase,
    month_windows,
    resolve_variant,
    run_slice,
    slice_window,
)
from xauby.backtest.service import _prepare_backtest_config, resolve_strategy_name
from xauby.observability.replay_validation import load_bot_config
from scripts.validate_on_venue_data import _okx_frame

PROXY = "XAUT-USDT"
WARMUP = 300
DRAWDOWN_FROM = "2026-03"
BOOTSTRAP_SAMPLES = 10000
SEED = 20260727


def _d1(shared: bool, long_gated: bool, short_gated: bool) -> Dict[str, Any]:
    """Every key of the d1_gate control group, always. resolve_variant insists."""
    return {
        "use_d1_regime_filter": shared,
        "use_d1_regime_filter_long": long_gated,
        "use_d1_regime_filter_short": short_gated,
    }


# Complete specs. Previously these set only `use_d1_regime_filter` and inherited
# the per-side keys from the base config — which is how they stopped being six
# distinct variants the moment the base config changed.
VARIANTS: Dict[str, Dict[str, Any]] = {
    "long-only D1 off": {"enable_short": False, **_d1(False, False, False)},
    "long-only D1 on": {"enable_short": False, **_d1(True, True, True)},
    "long+short D1 off": {"enable_short": True, **_d1(False, False, False)},
    "long+short D1 on": {"enable_short": True, **_d1(True, True, True)},
    "L:D1on S:D1off": {"enable_short": True, **_d1(True, True, False)},
    "L:D1off S:D1on (DEPLOYED)": {"enable_short": True, **_d1(True, False, True)},
}


def bootstrap(nets: List[float], samples: int = BOOTSTRAP_SAMPLES) -> Dict[str, Any]:
    if len(nets) < 5:
        return {"samples": 0, "note": "too few windows"}
    rng = random.Random(SEED)
    comps = []
    for _ in range(samples):
        c = 1.0
        for _ in range(len(nets)):
            c *= 1.0 + rng.choice(nets) / 100.0
        comps.append((c - 1.0) * 100.0)
    comps.sort()
    return {
        "samples": samples,
        "median_pct": round(statistics.median(comps), 2),
        "p05_pct": round(comps[int(0.05 * samples)], 2),
        "p95_pct": round(comps[int(0.95 * samples)], 2),
        "prob_profitable": round(sum(1 for c in comps if c > 0) / samples, 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="bot_config.yaml")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    cfg = load_bot_config(args.config)
    name = resolve_strategy_name("XAUUSDT", cfg, None)
    merged_cfg, base_strat, primary_tf, _rtf, _u, _used = _prepare_backtest_config(
        "XAUUSDT", name, cfg, None
    )
    df4 = _okx_frame(PROXY, "4h")
    df1 = _okx_frame(PROXY, "1d")

    windows = month_windows(df4)
    print(f"XAU windowed regeneration — {PROXY} 4h, {len(windows)} complete months, "
          f"{windows[0].label} -> {windows[-1].label}")
    print(f"warmup {WARMUP} bars, never traded (WindowSlice.skip_bars)\n")

    out: Dict[str, Any] = {"windows": len(windows), "variants": {}}

    for label, overrides in VARIANTS.items():
        strat = resolve_variant(base_strat, overrides)
        gated_any = overrides["use_d1_regime_filter_long"] or overrides["use_d1_regime_filter_short"]
        results = []
        for window in windows:
            sliced = slice_window(df4, window, warmup_bars=WARMUP)
            if sliced is None or sliced.traded_bars <= 0:
                continue
            stats = run_slice(
                sliced,
                strategy_name=name,
                strategy_config=strat,
                engine_config=merged_cfg,
                symbol="XAUUSDT",
                primary_timeframe=primary_tf,
                df_regime=df1 if gated_any else None,
                regime_timeframe="1d" if gated_any else None,
            )
            from xauby.backtest.walkforward import WindowResult, phase_label
            results.append(WindowResult(window=window, stats=stats,
                                        phase=phase_label(df1, window.start_ms)))

        by_phase = aggregate_by_phase(results)
        dd = [r for r in results if r.window.label >= DRAWDOWN_FROM]
        dd_agg = aggregate(dd)
        nets = [r.net_pct for r in results]

        out["variants"][label] = {
            "by_phase": by_phase,
            "drawdown_window": dd_agg,
            "drawdown_months": {r.window.label: round(r.net_pct, 2) for r in dd},
            "bootstrap": bootstrap(nets),
            "months": {r.window.label: {"phase": r.phase, "net": round(r.net_pct, 2),
                                        "n": r.trades} for r in results},
        }

        print(f"=== {label} ===")
        head = f"  {'phase':9s} {'mo':>3s} {'+mo':>4s} {'comp%':>9s} {'worst%':>7s} {'n':>4s}"
        print(head)
        for phase in ("bull", "bear", "sideways", "ALL"):
            agg = by_phase.get(phase)
            if not agg or not agg.get("windows"):
                continue
            print(f"  {phase:9s} {agg['windows']:3d} {agg['positive_windows']:4d} "
                  f"{agg['compounded_pct']:9.2f} {agg['worst_window_pct']:7.2f} {agg['trades']:4d}")
        print(f"  drawdown {DRAWDOWN_FROM}+: {dd_agg['compounded_pct']:+.2f}% "
              f"({dd_agg['positive_windows']}/{dd_agg['windows']} +mo, n={dd_agg['trades']})  "
              f"{' '.join(f'{k} {v:+.2f}' for k, v in out['variants'][label]['drawdown_months'].items())}")
        bs = out["variants"][label]["bootstrap"]
        if bs.get("samples"):
            print(f"  bootstrap: median {bs['median_pct']:+.2f}%  "
                  f"90% CI [{bs['p05_pct']:+.2f}, {bs['p95_pct']:+.2f}]  "
                  f"P(profit) {bs['prob_profitable']:.2%}")
        print()

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(out, fh, indent=2, default=str)
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
