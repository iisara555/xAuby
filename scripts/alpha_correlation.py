#!/usr/bin/env python3
"""How many *distinct* alphas does this repo actually have?

The champion searches ranked plugins against each other. They never asked
whether the plugins are different bets. A portfolio of six trend-followers is
one position held six ways: it diversifies nothing, and the rankings between
them are mostly noise about which parameterisation caught which rally.

This builds a monthly return series per plugin on one symbol — frozen default
configs, the same windows for everyone — and correlates them. Two plugins whose
monthly returns correlate at 0.8 are the same alpha wearing different
indicators, however differently their entry rules read.

What the output is for: deciding what to *add*. A candidate strategy is worth
building when it is uncorrelated with what already exists, even at a lower
standalone profit factor, because that is what changes a portfolio's drawdown.
A candidate that correlates 0.9 with SuperTrend has to beat SuperTrend outright
to be worth anything, which is a much harder bar.

Correlation here is over calendar-month returns, so it is blind to intra-month
path and it treats a month with three trades and a month with none as equal
observations. Read it as a coarse family map, not a covariance matrix to size
positions from.

Usage::

    PYTHONPATH=. python3 scripts/alpha_correlation.py --symbol BTCUSDT \
        --out-dir core/alpha_correlation --workers 4
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Dict, List, Mapping

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from scripts.rival_sweep import gates_on_d1
from scripts.validate_on_venue_data import _okx_frame
from xauby.backtest.service import _prepare_backtest_config
from xauby.backtest.walkforward import month_windows, phase_label, run_slice, slice_window
from xauby.observability.replay_validation import load_bot_config
from xauby.strategies.registry import available_strategies


WARMUP_BARS = 300

# Plugins that never take a position on a 4h series are noise in a correlation
# matrix — a column of zeros correlates with nothing and crowds the report.
MIN_ACTIVE_MONTHS = 12

_G: Dict[str, Any] = {}


def _init_worker(config_path: str, frame_path: str, daily_path: str, symbol: str) -> None:
    os.chdir(ROOT)
    cfg = load_bot_config(config_path)
    _G.clear()
    _G.update(cfg=cfg, symbol=symbol,
              df=pd.read_csv(frame_path), daily=pd.read_csv(daily_path))
    _G["resolved"] = {
        name: _prepare_backtest_config(symbol, name, dict(cfg), None)
        for name in available_strategies()
    }


def _eval(task: Mapping[str, Any]) -> Dict[str, Any]:
    name, window = task["strategy"], task["window"]
    try:
        merged_cfg, strat_cfg, primary_tf, _rtf, _use_d1, _used = _G["resolved"][name]
        config = {**dict(strat_cfg), **dict(task["override"])}
        sliced = slice_window(_G["df"], window, warmup_bars=WARMUP_BARS)
        if sliced is None or sliced.traded_bars <= 0:
            return {"strategy": name, "window": window.label, "skip": True}
        stats = run_slice(
            sliced,
            strategy_name=name,
            strategy_config=config,
            engine_config=merged_cfg,
            symbol=_G["symbol"],
            primary_timeframe=primary_tf,
            df_regime=_G["daily"] if gates_on_d1(config) else None,
            regime_timeframe="1d" if gates_on_d1(config) else None,
        )
        return {
            "strategy": name, "window": window.label,
            "net_pct": float(stats.get("net_profit_pct") or 0.0),
            "trades": int(stats.get("total_trades") or 0),
            "phase": phase_label(_G["daily"], window.start_ms),
        }
    except Exception as exc:
        return {"strategy": name, "window": window.label,
                "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="bot_config.yaml")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--out-dir", default="core/alpha_correlation")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--enable-short", action="store_true",
                        help="run every plugin with enable_short on")
    args = parser.parse_args()

    os.chdir(ROOT)
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    df = _okx_frame(args.symbol, "4h")
    daily = _okx_frame(args.symbol, "1d")
    frame_path, daily_path = out_dir / "frame4.csv", out_dir / "frame1.csv"
    df.to_csv(frame_path, index=False)
    daily.to_csv(daily_path, index=False)

    windows = month_windows(df)
    names = list(available_strategies())
    override = {"enable_short": bool(args.enable_short)}
    print(f"{args.symbol}: {len(df)} bars, {len(windows)} months, {len(names)} plugins "
          f"(enable_short={args.enable_short})")

    tasks = [{"strategy": name, "window": window, "override": override}
             for name in names for window in windows]
    rows: List[Dict[str, Any]] = []
    with Pool(processes=max(1, args.workers), initializer=_init_worker,
              initargs=(str(Path(args.config).resolve()), str(frame_path),
                        str(daily_path), args.symbol)) as pool:
        interval = max(1, len(tasks) // 20)
        for done, row in enumerate(pool.imap_unordered(_eval, tasks, chunksize=4), 1):
            rows.append(row)
            if done % interval == 0 or done == len(tasks):
                print(f"alpha: {done}/{len(tasks)}", flush=True)

    usable = [r for r in rows if "net_pct" in r]
    frame = pd.DataFrame(usable).pivot_table(index="window", columns="strategy",
                                             values="net_pct", aggfunc="first")
    activity = pd.DataFrame(usable).pivot_table(index="window", columns="strategy",
                                                values="trades", aggfunc="first")
    active = (activity.fillna(0) > 0).sum()
    keep = [c for c in frame.columns if active.get(c, 0) >= MIN_ACTIVE_MONTHS]
    dropped = sorted(set(frame.columns) - set(keep))
    frame = frame[keep].fillna(0.0)
    if dropped:
        print(f"\ndropped (traded in <{MIN_ACTIVE_MONTHS} months): {', '.join(dropped)}")

    print(f"\n-- monthly net % per plugin, {len(frame)} months --")
    summary = pd.DataFrame({
        "months_active": active[keep],
        "total_pct": frame.sum().round(2),
        "avg_mo": frame.mean().round(3),
        "std_mo": frame.std().round(3),
    }).sort_values("total_pct", ascending=False)
    print(summary.to_string())

    corr = frame.corr().round(2)
    print("\n-- correlation of monthly returns --")
    print(corr.to_string())

    print("\n-- closest relative for each plugin --")
    for name in corr.columns:
        others = corr[name].drop(name).sort_values(ascending=False)
        if others.empty:
            continue
        print(f"{name:26s} most like {others.index[0]:26s} r={others.iloc[0]:+.2f}   "
              f"least like {others.index[-1]:24s} r={others.iloc[-1]:+.2f}")

    # A plugin's mean correlation to everything else is the honest "is this a
    # new bet" number: a low value is what makes a weak standalone strategy
    # worth owning anyway.
    print("\n-- mean |correlation| to the rest (low = genuinely different bet) --")
    mean_abs = (corr.abs().sum() - 1) / max(1, len(corr) - 1)
    for name, value in mean_abs.sort_values().items():
        print(f"{name:26s} {value:.3f}")

    out = out_dir / "alpha_correlation.json"
    out.write_text(json.dumps({
        "symbol": args.symbol, "enable_short": args.enable_short,
        "months": list(frame.index), "dropped": dropped,
        "monthly_net": frame.to_dict(), "correlation": corr.to_dict(),
        "mean_abs_corr": mean_abs.round(3).to_dict(),
        "summary": summary.to_dict(),
    }, indent=2, default=str))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
