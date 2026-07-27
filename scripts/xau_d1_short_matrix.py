"""2x2: does D1 confirmation fix the deployed XAU config's bull-market problem?

Context. The deployed XAU config (long+short, D1 off) beats the July
certificate's config (long-only, D1 on) in falling and choppy gold but loses
badly in sustained uptrends (docs/research/xau_regime_attribution_2026-07-26.md).
Those two configs differ in TWO ways at once, so neither result attributes to a
single cause.

`use_d1_regime_filter` in xauby_actionzone gates BOTH directions symmetrically
(strategy.py: longs need D1 in GREEN/YELLOW/ORANGE, shorts need RED/BLUE/LBLUE),
so long+short WITH the D1 filter is a legal, already-supported config that nobody
has measured. If it keeps the short side's crisis alpha while restoring the D1
filter's bull discipline, it is a better answer than routing between two
strategies — and it needs no regime router at all.

This runs the full 2x2 over identical candles:

                    D1 off              D1 on
    long-only       (factorial cell)    cert config
    long+short      DEPLOYED            <- the untested candidate

Three views per cell: a continuous 4y run (the headline numbers), a
month-by-month phase split, and the current gold drawdown.

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

from typing import Any, Dict, List, Optional

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

DRAWDOWN_FROM = "2026-03"  # gold peaked 2026-02

VARIANTS: Dict[str, Dict[str, Any]] = {
    "long-only  D1 off": {"enable_short": False, "use_d1_regime_filter": False},
    "long-only  D1 on  (cert)": {"enable_short": False, "use_d1_regime_filter": True},
    "long+short D1 off (DEPLOYED)": {"enable_short": True, "use_d1_regime_filter": False},
    "long+short D1 on  (candidate)": {"enable_short": True, "use_d1_regime_filter": True},
}

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


def _with_d1(bundle, df1: pd.DataFrame, override: Dict[str, Any], end_ms: Optional[int] = None):
    """Attach the 1d regime frame when the variant asks for the D1 filter."""
    if not override.get("use_d1_regime_filter"):
        return bundle
    regime = df1 if end_ms is None else df1[df1["open_time"] <= end_ms]
    bundle.df_regime = regime.reset_index(drop=True)
    bundle.regime_tf = "1d"
    bundle.use_d1 = True
    return bundle


def _stats_or_raise(result, what: str) -> Dict[str, Any]:
    stats = result.stats or {}
    if not stats:
        raise RuntimeError(
            f"replay failed ({what}): "
            f"{getattr(result.meta, 'error', '') or 'unknown error'}"
        )
    return stats


def run_continuous(
    df4: pd.DataFrame, df1: pd.DataFrame, cfg: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """One uninterrupted replay per variant: the headline numbers."""
    rows = []
    for name, override in VARIANTS.items():
        bundle = _bundle_for("XAUUSDT", df4, engine_config=cfg, label=PROXY)
        _with_d1(bundle, df1, override)
        stats = _stats_or_raise(
            run_replay_from_bundle(bundle, strat_cfg_override=override), f"continuous {name}"
        )
        row: Dict[str, Any] = {"variant": name}
        for out_key, stat_key in CONT_KEYS:
            row[out_key] = stats.get(stat_key)
        rows.append(row)
    return rows


def run_monthly(
    df4: pd.DataFrame, df1: pd.DataFrame, cfg: Dict[str, Any]
) -> tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    windows = [w for w in month_windows(df4) if phase_label(df1, w[2]) != "unknown"]
    buckets: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
        n: {"bull": [], "bear": [], "sideways": []} for n in VARIANTS
    }
    per_month: List[Dict[str, Any]] = []

    for (y, m, start_ms, end_ms) in windows:
        phase = phase_label(df1, start_ms)
        if phase not in ("bull", "bear", "sideways"):
            continue
        entry: Dict[str, Any] = {"month": f"{y}-{m:02d}", "phase": phase}
        idx = int((df4["open_time"] >= start_ms).idxmax())
        lo = max(0, idx - WARMUP_BARS)
        window = df4.iloc[lo:]
        window = window[window["open_time"] <= end_ms].reset_index(drop=True)
        if len(window) < WARMUP_BARS // 2:
            continue
        for name, override in VARIANTS.items():
            bundle = _bundle_for("XAUUSDT", window, engine_config=cfg, label=PROXY)
            _with_d1(bundle, df1, override, end_ms=end_ms)
            stats = _stats_or_raise(
                run_replay_from_bundle(bundle, strat_cfg_override=override),
                f"{y}-{m:02d} {name}",
            )
            rec = {
                "net_pct": float(stats.get("net_profit_pct", 0.0) or 0.0),
                "trades": int(stats.get("total_trades", 0) or 0),
            }
            buckets[name][phase].append(rec)
            entry[name] = rec["net_pct"]
            entry[f"{name} n"] = rec["trades"]
        per_month.append(entry)

    summary: Dict[str, Dict[str, Any]] = {}
    for name in VARIANTS:
        summary[name] = {}
        allm: List[Dict[str, Any]] = []
        for phase in ("bull", "bear", "sideways"):
            summary[name][phase] = aggregate(buckets[name][phase])
            allm += buckets[name][phase]
        summary[name]["ALL"] = aggregate(allm)
    return summary, per_month


def buy_and_hold(df1: pd.DataFrame, start: str, end: Optional[str] = None) -> Dict[str, float]:
    ts = pd.to_datetime(df1["open_time"], unit="ms")
    sel = df1[ts >= pd.Timestamp(start)]
    if end:
        sel = sel[pd.to_datetime(sel["open_time"], unit="ms") < pd.Timestamp(end)]
    c = sel["close"].astype(float)
    peak = c.cummax()
    return {
        "return_pct": round(float(c.iloc[-1] / c.iloc[0] - 1) * 100, 2),
        "max_dd_pct": round(float(((c - peak) / peak * 100).min()), 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="bot_config.yaml")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    cfg = load_bot_config(args.config)
    df4 = _okx_frame(PROXY, "4h")
    df1 = _okx_frame(PROXY, "1d")

    span = (f"{pd.to_datetime(df4['open_time'].iloc[0], unit='ms').date()} -> "
            f"{pd.to_datetime(df4['open_time'].iloc[-1], unit='ms').date()}")
    print(f"XAU 2x2 — {PROXY} 4h, {len(df4)} bars, {span}\n")

    print("=== CONTINUOUS RUN (headline; no monthly reset) ===")
    cont = run_continuous(df4, df1, cfg)
    hdr = (f"{'variant':32s} {'n':>4s} {'WR%':>6s} {'PF':>5s} {'net%':>8s} "
           f"{'MDD%':>6s} {'CAGR%':>6s} {'Calmar':>6s} {'Sharpe':>6s}")
    print(hdr)
    print("-" * len(hdr))
    for r in cont:
        print(f"{r['variant']:32s} {r['trades']:4d} {r['win_rate']:6.2f} "
              f"{r['profit_factor']:5.2f} {r['net_profit_pct']:8.2f} "
              f"{r['max_drawdown_pct']:6.2f} {r['cagr_pct']:6.2f} "
              f"{r['calmar']:6.2f} {r['sharpe']:6.2f}")
    bh_full = buy_and_hold(df1, str(pd.to_datetime(df4['open_time'].iloc[0], unit='ms').date()))
    print(f"{'buy & hold gold':32s} {'-':>4s} {'-':>6s} {'-':>5s} "
          f"{bh_full['return_pct']:8.2f} {bh_full['max_dd_pct']:6.2f}")

    print("\n=== BY PHASE (monthly reset; attribution only) ===")
    summary, per_month = run_monthly(df4, df1, cfg)
    print(f"{len(per_month)} months, {per_month[0]['month']} -> {per_month[-1]['month']}\n")
    h2 = f"{'variant':32s} {'phase':9s} {'mo':>3s} {'+mo':>4s} {'comp%':>9s} {'worst%':>7s} {'n':>4s}"
    print(h2)
    print("-" * len(h2))
    for name in VARIANTS:
        for phase in ("bull", "bear", "sideways", "ALL"):
            a = summary[name][phase]
            if not a.get("months"):
                continue
            print(f"{name:32s} {phase:9s} {a['months']:3d} {a['pos_months']:4d} "
                  f"{a['compounded_pct']:9.2f} {a['worst_month_pct']:7.2f} {a['trades']:4d}")
        print()

    print(f"=== CURRENT GOLD DRAWDOWN ({DRAWDOWN_FROM} -> "
          f"{per_month[-1]['month']}; peak 2026-02) ===")
    dd = [m for m in per_month if m["month"] >= DRAWDOWN_FROM]
    for name in VARIANTS:
        a = aggregate([{"net_pct": m.get(name, 0.0), "trades": m.get(f"{name} n", 0)}
                       for m in dd])
        print(f"  {name:32s} comp {a['compounded_pct']:8.2f}%  "
              f"+mo {a['pos_months']}/{a['months']}  worst {a['worst_month_pct']:7.2f}%  "
              f"n={a['trades']}")
    bh_dd = buy_and_hold(df1, DRAWDOWN_FROM + "-01")
    print(f"  {'buy & hold gold':32s} comp {bh_dd['return_pct']:8.2f}%")

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump({"continuous": cont, "phase": summary, "months": per_month,
                       "buy_hold": {"full": bh_full, "drawdown": bh_dd}}, fh, indent=2)
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
