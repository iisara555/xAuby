"""Does the deployed XAU config earn its keep in bear phases?

The deployed XAU config runs long+short with the D1 regime filter OFF. The July
certificate measured long-only with D1 ON. Judging the deployed config by a
whole-period profit factor averages its short side across a gold market that was
rising most of the time, which would hide exactly the thing shorts are for.

So this splits the record by market phase instead of averaging over it, and runs
BOTH configs over the SAME candles so any difference is attributable to the
config. Phase labels follow the BTC certificate's convention
(`scripts/btc_wfa_multi_strategy.py::_phase_label`): 1d close vs EMA200 plus
EMA slope, computed from closes STRICTLY BEFORE each test month, so no
look-ahead.

Data is OKX XAUT-USDT (~4y, the deepest gold series on the venue we trade; its
fidelity to XAU-USDT-SWAP is measured in
docs/research/venue_data_revalidation_2026-07-26.md).

Usage:
    PYTHONPATH=. python3 scripts/xau_phase_breakdown.py
    PYTHONPATH=. python3 scripts/xau_phase_breakdown.py --json-out out.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from xauby.backtest.service import run_replay_from_bundle
from xauby.observability.replay_validation import load_bot_config
from scripts.validate_on_venue_data import _bundle_for, _okx_frame

PROXY = "XAUT-USDT"
WARMUP_BARS = 300

# The two configs under test, as overrides on the resolved strategy config.
# "deployed" is the empty override: whatever bot_config.yaml + the whitelist say.
VARIANTS: Dict[str, Dict[str, Any]] = {
    "deployed (long+short, D1 off)": {},
    "cert (long-only, D1 on)": {
        "enable_short": False,
        "use_d1_regime_filter": True,
    },
}


def phase_label(df1: pd.DataFrame, before_ts: int) -> str:
    """Bull/bear/sideways from 1d EMA200, using only closes before `before_ts`."""
    import pandas_ta as ta

    hist = df1[df1["open_time"] < before_ts]
    if len(hist) < 210:
        return "unknown"
    close = hist["close"].astype(float).reset_index(drop=True)
    ema = ta.ema(close, length=200)
    if ema is None or ema.isna().iloc[-1]:
        return "unknown"
    last, e_now, e_prev = float(close.iloc[-1]), float(ema.iloc[-1]), float(ema.iloc[-21])
    slope_up = e_now > e_prev
    if last > e_now and slope_up:
        return "bull"
    if last < e_now and not slope_up:
        return "bear"
    return "sideways"


def month_windows(df: pd.DataFrame) -> List[Tuple[int, int, int, int]]:
    """(year, month, start_ms, end_ms) for every COMPLETE month in the frame.

    The trailing partial month is dropped. Keeping it would compare a ~26-day
    stub against full months in the same compounded total and in "worst month",
    which silently flatters whichever config happened to be winning mid-month.
    """
    ts = pd.to_datetime(df["open_time"], unit="ms")
    out = []
    for (y, m), grp in df.groupby([ts.dt.year, ts.dt.month]):
        out.append((int(y), int(m), int(grp["open_time"].iloc[0]),
                    int(grp["open_time"].iloc[-1])))
    out.sort(key=lambda r: (r[0], r[1]))
    if out:
        last_end = pd.to_datetime(out[-1][3], unit="ms")
        if last_end.day < 28:
            out = out[:-1]
    return out


def run_month(
    df: pd.DataFrame,
    df1: Optional[pd.DataFrame],
    start_ms: int,
    end_ms: int,
    cfg: Dict[str, Any],
    override: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Replay one month with a warmup lead-in. None if the run failed."""
    idx = int((df["open_time"] >= start_ms).idxmax())
    lo = max(0, idx - WARMUP_BARS)
    window = df.iloc[lo:].copy()
    window = window[window["open_time"] <= end_ms].reset_index(drop=True)
    if len(window) < WARMUP_BARS // 2:
        return None

    use_d1 = bool(override.get("use_d1_regime_filter"))
    bundle = _bundle_for("XAUUSDT", window, engine_config=cfg, label=PROXY)
    if use_d1 and df1 is not None:
        regime = df1[df1["open_time"] <= end_ms].reset_index(drop=True)
        bundle.df_regime = regime
        bundle.regime_tf = "1d"
        bundle.use_d1 = True

    result = run_replay_from_bundle(bundle, strat_cfg_override=override or None)
    stats = result.stats or {}
    if not stats:
        # run_replay_from_bundle swallows exceptions into empty stats; surfacing
        # that as a skipped month would quietly bias the aggregate toward zero.
        raise RuntimeError(
            f"replay failed for {pd.to_datetime(start_ms, unit='ms').date()}: "
            f"{getattr(result.meta, 'error', '') or 'unknown error'}"
        )
    return {
        "net_pct": float(stats.get("net_profit_pct", 0.0) or 0.0),
        "trades": int(stats.get("total_trades", 0) or 0),
    }


def aggregate(months: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compound monthly returns, matching the BTC certificate's reporting."""
    if not months:
        return {"months": 0}
    comp = 1.0
    for m in months:
        comp *= 1.0 + m["net_pct"] / 100.0
    nets = [m["net_pct"] for m in months]
    return {
        "months": len(months),
        "pos_months": sum(1 for v in nets if v > 0),
        "compounded_pct": round((comp - 1.0) * 100, 2),
        "avg_month_pct": round(sum(nets) / len(nets), 3),
        "worst_month_pct": round(min(nets), 2),
        "best_month_pct": round(max(nets), 2),
        "trades": sum(m["trades"] for m in months),
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

    windows = month_windows(df4)
    # Drop the first months: they are consumed by the 300-bar warmup and the
    # 210-day EMA200 history the phase label needs.
    windows = [w for w in windows if phase_label(df1, w[2]) != "unknown"]

    rows: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
        name: {"bull": [], "bear": [], "sideways": []} for name in VARIANTS
    }
    per_month: List[Dict[str, Any]] = []

    for (y, m, start_ms, end_ms) in windows:
        phase = phase_label(df1, start_ms)
        if phase not in ("bull", "bear", "sideways"):
            continue
        entry: Dict[str, Any] = {"month": f"{y}-{m:02d}", "phase": phase}
        for name, override in VARIANTS.items():
            res = run_month(df4, df1, start_ms, end_ms, cfg, override)
            if res is None:
                continue
            rows[name][phase].append(res)
            entry[name] = res["net_pct"]
            entry[f"{name} n"] = res["trades"]
        per_month.append(entry)

    print(f"XAU phase breakdown — {PROXY} 4h, {len(per_month)} months, "
          f"{per_month[0]['month']} -> {per_month[-1]['month']}\n")

    hdr = f"{'config':32s} {'phase':9s} {'mo':>3s} {'+mo':>4s} {'comp%':>8s} " \
          f"{'avg%':>7s} {'worst%':>7s} {'best%':>7s} {'n':>4s}"
    print(hdr)
    print("-" * len(hdr))
    summary: Dict[str, Dict[str, Any]] = {}
    for name in VARIANTS:
        summary[name] = {}
        all_months: List[Dict[str, Any]] = []
        for phase in ("bull", "bear", "sideways"):
            agg = aggregate(rows[name][phase])
            summary[name][phase] = agg
            all_months += rows[name][phase]
            if agg["months"]:
                print(f"{name:32s} {phase:9s} {agg['months']:3d} "
                      f"{agg['pos_months']:4d} {agg['compounded_pct']:8.2f} "
                      f"{agg['avg_month_pct']:7.3f} {agg['worst_month_pct']:7.2f} "
                      f"{agg['best_month_pct']:7.2f} {agg['trades']:4d}")
        tot = aggregate(all_months)
        summary[name]["ALL"] = tot
        print(f"{name:32s} {'ALL':9s} {tot['months']:3d} {tot['pos_months']:4d} "
              f"{tot['compounded_pct']:8.2f} {tot['avg_month_pct']:7.3f} "
              f"{tot['worst_month_pct']:7.2f} {tot['best_month_pct']:7.2f} "
              f"{tot['trades']:4d}")
        print()

    names = list(VARIANTS)
    print("Head-to-head by phase (compounded %, deployed minus cert):")
    for phase in ("bull", "bear", "sideways", "ALL"):
        a = summary[names[0]].get(phase, {}).get("compounded_pct")
        b = summary[names[1]].get(phase, {}).get("compounded_pct")
        if a is None or b is None:
            continue
        winner = "deployed" if a > b else "cert"
        print(f"  {phase:9s} deployed {a:8.2f}   cert {b:8.2f}   "
              f"delta {a - b:+8.2f}  -> {winner}")

    # The live-relevant slice: gold peaked in 2026-02 and has been falling since,
    # so the current market is the regime the deployed config is built for. Phase
    # labels lag (EMA200 slope stayed up for months into the decline), which is
    # exactly why this is reported separately from the bull/bear/sideways split.
    dd_from = "2026-03"
    dd_months = [m for m in per_month if m["month"] >= dd_from]
    if dd_months:
        print(f"\nCurrent gold drawdown ({dd_from} -> {per_month[-1]['month']}, "
              f"{len(dd_months)} months, peak 2026-02):")
        for name in VARIANTS:
            agg = aggregate([{"net_pct": m.get(name, 0.0),
                              "trades": m.get(f"{name} n", 0)} for m in dd_months])
            print(f"  {name:32s} comp {agg['compounded_pct']:8.2f}%  "
                  f"+mo {agg['pos_months']}/{agg['months']}  "
                  f"worst {agg['worst_month_pct']:6.2f}%  n={agg['trades']}")
        print("  per-month:")
        for m in dd_months:
            cells = "  ".join(f"{n.split()[0]} {m.get(n, 0.0):+6.2f}% (n={m.get(f'{n} n', 0)})"
                              for n in VARIANTS)
            print(f"    {m['month']} {m['phase']:9s} {cells}")

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump({"summary": summary, "months": per_month}, fh, indent=2)
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
