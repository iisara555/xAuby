#!/usr/bin/env python3
"""Walk-forward: can per-window tuning repair Donchian's out-of-sample gap?

`btc_champion_search.py` found `xauby_donchian_trend` beating
`supertrend_ema200` on full-history profit factor (1.658 vs 1.513) while
*losing* the out-of-sample window (OOS PF 1.326 vs 1.550) and posting a worse
worst fold (0.468 vs 0.603). Two explanations fit that shape and they have
opposite consequences:

* the edge is real but the fixed default config has drifted out of tune, in
  which case periodic re-optimisation recovers it; or
* the edge is a property of the early history and no amount of tuning brings it
  into the recent regime.

An anchored rolling walk-forward separates them. Train on the trailing
``--train-months``, pick the best cell of the strategy's grid on that window
only, freeze it, replay the next month, roll forward. Nothing in a test month
informs the config that trades it.

**The incumbent gets the same treatment.** Handing Donchian per-window tuning
while replaying SuperTrend frozen would compare a tuned challenger against an
untuned champion and call the difference edge. Every strategy here runs both
arms: `frozen` (one config for the whole history) and `reopt` (re-tuned every
month), on identical windows.

Data is native OKX `BTC-USDT-SWAP` 4h — the venue the pair actually trades —
not the Binance spot archive `btc_wfa_multi_strategy.py` uses.

Usage::

    PYTHONPATH=. python3 scripts/btc_donchian_wfa.py \
        --out-dir core/btc_donchian_wfa --workers 4
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from scripts.validate_on_venue_data import _okx_frame
from xauby.backtest.service import _prepare_backtest_config
from xauby.backtest.walkforward import (
    Window,
    month_windows,
    phase_label,
    run_slice,
    slice_window,
)
from xauby.observability.replay_validation import load_bot_config


SYMBOL = "BTCUSDT"
VENUE = "BTC-USDT-SWAP"
WARMUP_BARS = 300
TRAIN_MONTHS = 6
MIN_TRAIN_TRADES = 3

# Grids stay small on purpose. A train window is six months of 4h bars, so a
# wide grid picks its winner from a handful of trades and the "tuning" is noise
# fitting — which would answer the question with an artifact.
GRIDS: Dict[str, List[Dict[str, Any]]] = {
    "xauby_donchian_trend": [
        {"entry_len": e, "adx_min": a, "sl_atr_mult": s}
        for e, a, s in itertools.product((48, 96, 120), (20.0, 25.0), (2.0, 2.5))
    ],
    "supertrend_ema200": [
        {"supertrend_mult": m, "sl_atr_mult": s, "exit_on_ema_loss": x}
        for m, s, x in itertools.product((2.5, 3.0, 3.5, 4.0), (2.0, 3.0), (True, False))
    ],
}

# (label, strategy, base override). The base is what `frozen` replays and what
# every grid cell is merged onto in `reopt`.
ARMS: List[Tuple[str, str, Dict[str, Any]]] = [
    ("supertrend L+S", "supertrend_ema200", {"enable_short": True}),
    ("donchian long", "xauby_donchian_trend", {"enable_short": False}),
    ("donchian L+S", "xauby_donchian_trend", {"enable_short": True}),
]

_G: Dict[str, Any] = {}


def _init_worker(config_path: str, frame_path: str, daily_path: str) -> None:
    os.chdir(ROOT)
    cfg = load_bot_config(config_path)
    _G.clear()
    _G["cfg"] = cfg
    _G["df"] = pd.read_csv(frame_path)
    _G["daily"] = pd.read_csv(daily_path)
    _G["resolved"] = {
        name: _prepare_backtest_config(SYMBOL, name, dict(cfg), None)
        for name in {strategy for _, strategy, _ in ARMS}
    }


def _replay(strategy: str, config: Mapping[str, Any], window: Window) -> Dict[str, Any]:
    merged_cfg, strat_cfg, primary_tf, _regime_tf, _use_d1, _used = _G["resolved"][strategy]
    sliced = slice_window(_G["df"], window, warmup_bars=WARMUP_BARS)
    if sliced is None or sliced.traded_bars <= 0:
        return {}
    return run_slice(
        sliced,
        strategy_name=strategy,
        strategy_config={**dict(strat_cfg), **dict(config)},
        engine_config=merged_cfg,
        symbol=SYMBOL,
        primary_timeframe=primary_tf,
    )


def _train_span(windows: Sequence[Window], index: int) -> Optional[Window]:
    """One window covering the ``TRAIN_MONTHS`` months before ``windows[index]``."""
    if index < TRAIN_MONTHS:
        return None
    first = windows[index - TRAIN_MONTHS]
    last = windows[index - 1]
    return Window(label=f"{first.label}..{last.label}",
                  start_ms=first.start_ms, end_ms=last.end_ms)


def _score(stats: Mapping[str, Any]) -> Tuple[float, float]:
    """Rank train cells by profit factor, breaking ties on net.

    Cells below ``MIN_TRAIN_TRADES`` are pushed to the bottom rather than
    dropped, so a window where nothing trades still yields a choice instead of
    silently falling back to the base config with no record of why.
    """
    trades = int(stats.get("total_trades") or 0)
    pf = float(stats.get("profit_factor") or 0.0)
    net = float(stats.get("net_profit_pct") or 0.0)
    if trades < MIN_TRAIN_TRADES:
        return (-1.0, net)
    return (pf, net)


def _eval(task: Mapping[str, Any]) -> Dict[str, Any]:
    label, strategy, base = task["arm"]
    window = task["window"]
    try:
        chosen = dict(base)
        train_label = None
        if task["mode"] == "reopt":
            train = task["train"]
            train_label = train.label
            best, best_score = None, None
            for cell in GRIDS[strategy]:
                stats = _replay(strategy, {**base, **cell}, train)
                if not stats:
                    continue
                score = _score(stats)
                if best_score is None or score > best_score:
                    best, best_score = cell, score
            if best is not None:
                chosen = {**base, **best}
        stats = _replay(strategy, chosen, window)
        return {
            "arm": label, "mode": task["mode"], "strategy": strategy,
            "window": window.label, "train": train_label,
            "config": chosen, "stats": stats,
            "phase": phase_label(_G["daily"], window.start_ms),
        }
    except Exception as exc:  # one bad month must not lose the walk
        return {"arm": label, "mode": task["mode"], "window": window.label,
                "error": f"{type(exc).__name__}: {exc}"}


def _summarise(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    usable = [r for r in rows if not r.get("error") and r.get("stats")]
    if not usable:
        return {"windows": 0}
    nets = [float(r["stats"].get("net_profit_pct") or 0.0) for r in usable]
    compounded = 1.0
    for value in nets:
        compounded *= 1 + value / 100
    gross_win = sum(float(r["stats"].get("gross_profit") or 0.0) for r in usable)
    gross_loss = sum(abs(float(r["stats"].get("gross_loss") or 0.0)) for r in usable)
    return {
        "windows": len(usable),
        "positive": sum(1 for v in nets if v > 0),
        "compounded_pct": round((compounded - 1) * 100, 2),
        "avg_window_pct": round(sum(nets) / len(nets), 3),
        "worst_window_pct": round(min(nets), 2),
        "best_window_pct": round(max(nets), 2),
        "trades": sum(int(r["stats"].get("total_trades") or 0) for r in usable),
        "pooled_pf": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
    }


def _by_phase(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for phase in ("bull", "bear", "sideways", "unknown"):
        subset = [r for r in rows if r.get("phase") == phase]
        if subset:
            out[phase] = _summarise(subset)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="bot_config.yaml")
    parser.add_argument("--out-dir", default="core/btc_donchian_wfa")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--train-months", type=int, default=TRAIN_MONTHS)
    parser.add_argument("--first", default=None,
                        help="earliest test month, e.g. 2021-01")
    args = parser.parse_args()

    globals()["TRAIN_MONTHS"] = max(1, args.train_months)

    os.chdir(ROOT)
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    df = _okx_frame(SYMBOL, "4h")
    daily = _okx_frame(SYMBOL, "1d")
    frame_path, daily_path = out_dir / "native4.csv", out_dir / "native1.csv"
    df.to_csv(frame_path, index=False)
    daily.to_csv(daily_path, index=False)

    windows = month_windows(df, first=args.first)
    testable = [(i, w) for i, w in enumerate(windows) if i >= TRAIN_MONTHS]
    print(f"{VENUE} 4h: {len(df)} bars, {len(windows)} months, "
          f"{len(testable)} testable after a {TRAIN_MONTHS}-month train span")

    tasks: List[Dict[str, Any]] = []
    for arm in ARMS:
        for index, window in testable:
            tasks.append({"arm": arm, "window": window, "mode": "frozen",
                          "train": None})
            tasks.append({"arm": arm, "window": window, "mode": "reopt",
                          "train": _train_span(windows, index)})
    print(f"{len(tasks)} window-runs ({len(ARMS)} arms x 2 modes)", flush=True)

    rows: List[Dict[str, Any]] = []
    with Pool(processes=max(1, args.workers), initializer=_init_worker,
              initargs=(str(Path(args.config).resolve()),
                        str(frame_path), str(daily_path))) as pool:
        interval = max(1, len(tasks) // 20)
        for done, row in enumerate(pool.imap_unordered(_eval, tasks, chunksize=1),
                                   start=1):
            rows.append(row)
            if done % interval == 0 or done == len(tasks):
                print(f"wfa: {done}/{len(tasks)}", flush=True)

    report: Dict[str, Any] = {"windows": len(testable), "arms": {}}
    print("\n-- anchored rolling walk-forward, OKX BTC-USDT-SWAP 4h --")
    print(f"   train {TRAIN_MONTHS} months -> test 1 month, rolling; "
          f"pooled PF sums gross win/loss across all test months\n")
    header = (f"{'arm':22s} {'mode':7s} {'win':>7s} {'pooled PF':>10s} "
              f"{'compounded':>11s} {'avg mo':>8s} {'worst mo':>9s} {'trades':>7s}")
    print(header)
    for label, _strategy, _base in ARMS:
        for mode in ("frozen", "reopt"):
            subset = [r for r in rows if r.get("arm") == label and r.get("mode") == mode]
            summary = _summarise(subset)
            report["arms"][f"{label} [{mode}]"] = {
                "summary": summary, "by_phase": _by_phase(subset)}
            if not summary.get("windows"):
                print(f"{label:22s} {mode:7s}   no usable windows")
                continue
            print(f"{label:22s} {mode:7s} "
                  f"{summary['positive']:3d}/{summary['windows']:<3d} "
                  f"{str(summary['pooled_pf']):>10s} "
                  f"{summary['compounded_pct']:+10.2f}% "
                  f"{summary['avg_window_pct']:+7.3f}% "
                  f"{summary['worst_window_pct']:+8.2f}% "
                  f"{summary['trades']:7d}")

    print("\n-- by market phase (pooled PF / compounded) --")
    for key, block in report["arms"].items():
        phases = block["by_phase"]
        if not phases:
            continue
        cells = "  ".join(
            f"{phase}: {stats.get('pooled_pf')} / {stats.get('compounded_pct'):+.1f}%"
            for phase, stats in phases.items())
        print(f"{key:34s} {cells}")

    errors = [r for r in rows if r.get("error")]
    if errors:
        print(f"\n{len(errors)} window-runs failed; first: {errors[0]['error'][:120]}")

    report["rows"] = rows
    out = out_dir / "btc_donchian_wfa.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
