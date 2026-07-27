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

Usage:
    PYTHONPATH=. python3 scripts/certify_xau_candidate.py
    PYTHONPATH=. python3 scripts/certify_xau_candidate.py --variant "long-only D1 on"
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from xauby.backtest.service import run_replay_from_bundle
from xauby.observability.replay_validation import load_bot_config
from scripts.validate_on_venue_data import _bundle_for, _okx_frame
from scripts.xau_phase_breakdown import (
    PROXY,
    WARMUP_BARS,
    aggregate,
    month_windows,
    phase_label,
)

CONFIGS: Dict[str, Dict[str, Any]] = {
    "long-only D1 off": {"enable_short": False, "use_d1_regime_filter": False},
    "long-only D1 on": {"enable_short": False, "use_d1_regime_filter": True},
    "long+short D1 off": {"enable_short": True, "use_d1_regime_filter": False},
    "long+short D1 on": {"enable_short": True, "use_d1_regime_filter": True},
    # Asymmetric: keep the daily confirmation on longs, let shorts fire on the
    # 4H flip. Rationale is D1 lag — gating shorts on the daily zone enters a
    # decline only after it has already turned.
    "L:D1on S:D1off": {
        "enable_short": True,
        "use_d1_regime_filter": True,
        "use_d1_regime_filter_short": False,
    },
    # The mirror, included so the asymmetry is tested in both directions rather
    # than only the one that sounds right.
    "L:D1off S:D1on": {
        "enable_short": True,
        "use_d1_regime_filter": True,
        "use_d1_regime_filter_long": False,
    },
}

# Which long-only config each long+short config must clear by min_profit_edge_pp.
# The asymmetric cells gate longs the same way "long-only D1 on" does, so that is
# the honest baseline for them; L:D1off gates longs like "long-only D1 off".
GATE_PAIRS = [
    ("long+short D1 on", "long-only D1 on"),
    ("long+short D1 off", "long-only D1 off"),
    ("L:D1on S:D1off", "long-only D1 on"),
    ("L:D1off S:D1on", "long-only D1 off"),
]

BOOTSTRAP_SAMPLES = 10000
SEED = 20260726


def _attach_d1(bundle, df1: pd.DataFrame, override: Dict[str, Any],
               end_ms: Optional[int] = None):
    if not override.get("use_d1_regime_filter"):
        return bundle
    frame = df1 if end_ms is None else df1[df1["open_time"] <= end_ms]
    bundle.df_regime = frame.reset_index(drop=True)
    bundle.regime_tf = "1d"
    bundle.use_d1 = True
    return bundle


def _run(bundle, override: Dict[str, Any], what: str) -> Dict[str, Any]:
    res = run_replay_from_bundle(bundle, strat_cfg_override=override)
    stats = res.stats or {}
    if not stats:
        raise RuntimeError(f"replay failed ({what}): "
                           f"{getattr(res.meta, 'error', '') or 'unknown'}")
    return stats


def continuous(df4, df1, cfg) -> Dict[str, Dict[str, Any]]:
    out = {}
    for name, override in CONFIGS.items():
        b = _bundle_for("XAUUSDT", df4, engine_config=cfg, label=PROXY)
        _attach_d1(b, df1, override)
        s = _run(b, override, f"continuous {name}")
        out[name] = {
            "net_pct": float(s.get("net_profit_pct", 0.0) or 0.0),
            "profit_factor": float(s.get("profit_factor", 0.0) or 0.0),
            "max_drawdown_pct": float(s.get("max_drawdown_pct", 0.0) or 0.0),
            "sharpe": float(s.get("sharpe", 0.0) or 0.0),
            "calmar": float(s.get("calmar", 0.0) or 0.0),
            "trades": int(s.get("total_trades", 0) or 0),
        }
    return out


def acceptance_gate(cont: Dict[str, Dict[str, Any]], min_edge: float) -> List[Dict[str, Any]]:
    rows = []
    for ls_name, lo_name in GATE_PAIRS:
        ls, lo = cont[ls_name]["net_pct"], cont[lo_name]["net_pct"]
        edge = ls - lo
        rows.append({
            "long_short": ls_name, "long_only": lo_name,
            "ls_net": ls, "lo_net": lo, "edge_pp": round(edge, 2),
            "passed": bool(ls > 0.0 and edge >= min_edge),
        })
    return rows


def walk_forward(df4, df1, cfg, variant: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    override = CONFIGS[variant]
    windows = [w for w in month_windows(df4) if phase_label(df1, w[2]) != "unknown"]
    months: List[Dict[str, Any]] = []
    for (y, m, start_ms, end_ms) in windows:
        phase = phase_label(df1, start_ms)
        if phase not in ("bull", "bear", "sideways"):
            continue
        idx = int((df4["open_time"] >= start_ms).idxmax())
        lo = max(0, idx - WARMUP_BARS)
        win = df4.iloc[lo:]
        win = win[win["open_time"] <= end_ms].reset_index(drop=True)
        if len(win) < WARMUP_BARS // 2:
            continue
        b = _bundle_for("XAUUSDT", win, engine_config=cfg, label=PROXY)
        _attach_d1(b, df1, override, end_ms=end_ms)
        s = _run(b, override, f"{y}-{m:02d} {variant}")
        months.append({
            "month": f"{y}-{m:02d}", "phase": phase,
            "net_pct": float(s.get("net_profit_pct", 0.0) or 0.0),
            "trades": int(s.get("total_trades", 0) or 0),
        })
    by_phase = {p: aggregate([m for m in months if m["phase"] == p])
                for p in ("bull", "bear", "sideways")}
    by_phase["ALL"] = aggregate(months)
    return months, by_phase


def bootstrap(months: List[Dict[str, Any]], samples: int = BOOTSTRAP_SAMPLES) -> Dict[str, Any]:
    """Resample monthly returns with replacement; CI on the compounded result."""
    rng = random.Random(SEED)
    nets = [m["net_pct"] for m in months]
    n = len(nets)
    if n < 5:
        return {"samples": 0, "note": "too few months to bootstrap"}
    comps: List[float] = []
    for _ in range(samples):
        c = 1.0
        for _ in range(n):
            c *= 1.0 + rng.choice(nets) / 100.0
        comps.append((c - 1.0) * 100.0)
    comps.sort()
    return {
        "samples": samples,
        "months_resampled": n,
        "median_pct": round(statistics.median(comps), 2),
        "p05_pct": round(comps[int(0.05 * samples)], 2),
        "p95_pct": round(comps[int(0.95 * samples)], 2),
        "prob_profitable": round(sum(1 for c in comps if c > 0) / samples, 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="bot_config.yaml")
    ap.add_argument("--variant", default=None,
                    help="Config to walk-forward. Default: every config that "
                         "clears the acceptance gate, plus the long-only baselines.")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    cfg = load_bot_config(args.config)
    acc_cfg = ((cfg.get("backtest") or {}).get("acceptance") or {})
    min_edge = float(acc_cfg.get("min_profit_edge_pp", 5.0))

    df4 = _okx_frame(PROXY, "4h")
    df1 = _okx_frame(PROXY, "1d")
    span = (f"{pd.to_datetime(df4['open_time'].iloc[0], unit='ms').date()} -> "
            f"{pd.to_datetime(df4['open_time'].iloc[-1], unit='ms').date()}")
    print(f"XAU certification — {PROXY} 4h, {len(df4)} bars, {span}\n")

    print("=== STAGE 1: acceptance gate (pre-registered) ===")
    print(f"criterion: long_short net > 0 AND edge >= {min_edge:.2f}pp "
          f"(backtest.acceptance)\n")
    cont = continuous(df4, df1, cfg)
    gate = acceptance_gate(cont, min_edge)
    for g in gate:
        print(f"  {g['long_short']:20s} {g['ls_net']:7.2f}%  vs  "
              f"{g['long_only']:20s} {g['lo_net']:7.2f}%   "
              f"edge {g['edge_pp']:+7.2f}pp  -> {'PASS' if g['passed'] else 'FAIL'}")

    admissible = [g["long_short"] for g in gate if g["passed"]]
    print(f"\n  admissible long+short configs: {admissible or 'NONE'}")

    print("\n=== continuous reference (all four) ===")
    hdr = f"{'config':20s} {'n':>4s} {'PF':>5s} {'net%':>8s} {'MDD%':>6s} {'Sharpe':>6s} {'Calmar':>6s}"
    print(hdr); print("-" * len(hdr))
    for name, r in cont.items():
        print(f"{name:20s} {r['trades']:4d} {r['profit_factor']:5.2f} "
              f"{r['net_pct']:8.2f} {r['max_drawdown_pct']:6.2f} "
              f"{r['sharpe']:6.2f} {r['calmar']:6.2f}")

    targets = [args.variant] if args.variant else (
        admissible + ["long-only D1 on", "long-only D1 off"]
    )
    seen = set()
    targets = [t for t in targets if not (t in seen or seen.add(t))]

    results: Dict[str, Any] = {}
    for variant in targets:
        if variant not in CONFIGS:
            raise SystemExit(f"unknown variant {variant!r}; choose from {list(CONFIGS)}")
        print(f"\n=== STAGE 2: walk-forward — {variant} (frozen config) ===")
        months, by_phase = walk_forward(df4, df1, cfg, variant)
        print(f"{len(months)} months, {months[0]['month']} -> {months[-1]['month']}")
        h = f"  {'phase':9s} {'mo':>3s} {'+mo':>4s} {'comp%':>9s} {'worst%':>7s} {'n':>4s}"
        print(h); print("  " + "-" * (len(h) - 2))
        for phase in ("bull", "bear", "sideways", "ALL"):
            a = by_phase[phase]
            if not a.get("months"):
                continue
            print(f"  {phase:9s} {a['months']:3d} {a['pos_months']:4d} "
                  f"{a['compounded_pct']:9.2f} {a['worst_month_pct']:7.2f} {a['trades']:4d}")

        print(f"\n=== STAGE 3: bootstrap — {variant} ===")
        bs = bootstrap(months)
        if bs.get("samples"):
            print(f"  {bs['samples']} resamples of {bs['months_resampled']} monthly returns")
            print(f"  compounded: median {bs['median_pct']:+.2f}%  "
                  f"90% CI [{bs['p05_pct']:+.2f}%, {bs['p95_pct']:+.2f}%]")
            print(f"  P(profitable) = {bs['prob_profitable']:.2%}")
        else:
            print(f"  {bs.get('note')}")
        results[variant] = {"by_phase": by_phase, "bootstrap": bs, "months": months}

    print("\n=== VERDICT ===")
    if not admissible:
        print("  Shorts do NOT clear the pre-registered acceptance gate on this data.")
        print("  No long+short XAU config is certifiable as-is.")
    for variant in targets:
        bs = results[variant]["bootstrap"]
        allp = results[variant]["by_phase"]["ALL"]
        gate_note = ("gate n/a (long-only)" if variant.startswith("long-only")
                     else ("gate PASS" if variant in admissible else "gate FAIL"))
        print(f"  {variant:20s} {gate_note:20s} "
              f"WFA {allp['pos_months']}/{allp['months']} +months, "
              f"P(profit) {bs.get('prob_profitable', 0):.1%}")

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump({"continuous": cont, "gate": gate, "min_edge_pp": min_edge,
                       "results": results}, fh, indent=2)
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
