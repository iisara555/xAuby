"""Pre-registered regime switch rule for the XAU config. Evaluates, never decides.

Why this exists. XAU currently runs `long-only ungated + D1-gated shorts`, chosen
because gold has been falling since its 2026-02 peak and that config is the
strongest cell in a decline (+22.61% over 2026-03..06 against buy-and-hold's
-23.21%). Over the full 4.02y sample, however, `long-only + D1 on` is clearly
better (PF 1.96 vs 1.38, MDD 9.22% vs 14.42%) — because that sample is 90% bull.

So the current choice is regime-conditional. If gold resumes a sustained uptrend,
the config should switch back. Deciding *in the moment* what counts as "a
sustained uptrend" is exactly how the July certificate went wrong, so the rule is
fixed here, in advance, and this script only reports whether it has fired.

THE RULE (fixed 2026-07-26, before it was measured — do not re-tune):

    Daily phase uses the SAME definition as the BTC certificate
    (scripts/btc_wfa_multi_strategy.py::_phase_label): 1d close vs EMA200 plus
    the EMA200 slope over the trailing 21 bars, computed from closes strictly
    before the evaluated bar.

    CONFIRM_BARS = 30 consecutive daily closes.

    * 30 consecutive `bull` days      -> switch to `long-only + D1 on`
    * 30 consecutive non-`bull` days  -> switch to `long-only ungated + D1 shorts`

    The 30 is "about one month", the unit every analysis in this thread used. It
    was not selected by trying values and keeping the best.

This script does not change configuration. It prints the current state and the
history of firings so a human can act.

Usage:
    PYTHONPATH=. python3 scripts/xau_regime_switch_check.py
    PYTHONPATH=. python3 scripts/xau_regime_switch_check.py --json-out out.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Dict, List, Optional

import pandas as pd

from scripts.validate_on_venue_data import _okx_frame

PROXY = "XAUT-USDT"
CONFIRM_BARS = 30
EMA_LEN = 200
SLOPE_LOOKBACK = 21
MIN_HISTORY = EMA_LEN + SLOPE_LOOKBACK

CONFIG_BULL = "long-only + D1 on"
CONFIG_NONBULL = "long-only ungated + D1-gated shorts"


def daily_phases(df1: pd.DataFrame) -> pd.DataFrame:
    """Phase per daily bar, using only closes strictly before that bar."""
    import pandas_ta as ta

    close = df1["close"].astype(float).reset_index(drop=True)
    ema = ta.ema(close, length=EMA_LEN)
    rows: List[Dict[str, Any]] = []
    for i in range(len(close)):
        if i < MIN_HISTORY or pd.isna(ema.iloc[i - 1]):
            phase = "unknown"
        else:
            # Strictly-before semantics: the bar at i is judged on closes < i.
            last = float(close.iloc[i - 1])
            e_now = float(ema.iloc[i - 1])
            e_prev = float(ema.iloc[i - 1 - SLOPE_LOOKBACK])
            slope_up = e_now > e_prev
            if last > e_now and slope_up:
                phase = "bull"
            elif last < e_now and not slope_up:
                phase = "bear"
            else:
                phase = "sideways"
        rows.append({
            "date": pd.to_datetime(int(df1["open_time"].iloc[i]), unit="ms").date(),
            "close": float(close.iloc[i]),
            "phase": phase,
        })
    return pd.DataFrame(rows)


def evaluate(phases: pd.DataFrame) -> Dict[str, Any]:
    """Walk the phase series applying the confirmation rule."""
    known = phases[phases["phase"] != "unknown"].reset_index(drop=True)
    if known.empty:
        return {"error": "not enough history to evaluate the rule"}

    # Start in the config that matches the first confirmed state; the history of
    # switches is what matters, not the arbitrary starting point.
    current = CONFIG_NONBULL
    run_kind: Optional[str] = None
    run_len = 0
    switches: List[Dict[str, Any]] = []

    for _, row in known.iterrows():
        kind = "bull" if row["phase"] == "bull" else "non-bull"
        if kind == run_kind:
            run_len += 1
        else:
            run_kind, run_len = kind, 1
        if run_len == CONFIRM_BARS:
            target = CONFIG_BULL if kind == "bull" else CONFIG_NONBULL
            if target != current:
                switches.append({
                    "date": str(row["date"]),
                    "to": target,
                    "trigger": f"{CONFIRM_BARS} consecutive {kind} days",
                    "close": round(float(row["close"]), 2),
                })
                current = target

    last = known.iloc[-1]
    kind_now = "bull" if last["phase"] == "bull" else "non-bull"
    target_now = CONFIG_BULL if kind_now == "bull" else CONFIG_NONBULL
    return {
        "as_of": str(last["date"]),
        "close": round(float(last["close"]), 2),
        "phase_today": str(last["phase"]),
        "current_streak_kind": kind_now,
        "current_streak_len": int(run_len),
        "bars_to_confirm": max(0, CONFIRM_BARS - int(run_len)),
        "rule_says": current,
        "streak_target": target_now,
        "switches": switches,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    df1 = _okx_frame(PROXY, "1d")
    phases = daily_phases(df1)
    result = evaluate(phases)
    if "error" in result:
        print(result["error"])
        return 1

    print(f"XAU regime switch check — {PROXY} 1d, rule fixed 2026-07-26")
    print(f"rule: {CONFIRM_BARS} consecutive daily closes confirm a regime\n")

    print(f"  as of            {result['as_of']}  (close {result['close']})")
    print(f"  phase today      {result['phase_today']}")
    print(f"  current streak   {result['current_streak_len']} x {result['current_streak_kind']}")
    print(f"  bars to confirm  {result['bars_to_confirm']}")
    print()
    print(f"  RULE SAYS RUN:   {result['rule_says']}")

    switches = result["switches"]
    print(f"\n  historical firings: {len(switches)}")
    for s in switches:
        print(f"    {s['date']}  -> {s['to']:38s} ({s['trigger']}, close {s['close']})")

    counts = phases[phases["phase"] != "unknown"]["phase"].value_counts().to_dict()
    print(f"\n  daily phase mix over the sample: {counts}")

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(result, fh, indent=2, default=str)
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
