#!/usr/bin/env python3
"""Find a challenger to `supertrend_ema200` on BTC, on native OKX swap data.

BTC's config space has been searched hard — `btc_supertrend_okx_pf_grid.py` ran
576 cells of SuperTrend shapes on this exact series. What has never been asked
on OKX data is whether SuperTrend is the right *plugin*: the only multi-strategy
BTC comparison in the repo (`btc_wfa_multi_strategy.py`) covers four plugins on
Binance **spot**, not the venue the pair actually trades.

So this searches strategies, not parameters. Every registered plugin is replayed
on OKX `BTC-USDT-SWAP` 4h, long-only and long+short, each on its own default
config — a challenger is judged on its own stop, trailing and sizing model, not
wearing SuperTrend's. `supertrend_ema200` at the deployed config is the
benchmark row.

Unlike the XAU study there is no proxy: OKX has native BTC swap candles back to
2019-12, so selection and confirmation happen on the venue that trades. The
cost is that every fold is one BTC regime — see the drawdown-phase and fold
tables in the report, which is where a plugin that only works in a bull market
gives itself away.

This is a research harness. It writes JSON and prints tables; it never edits
`bot_config.yaml` or `coin_whitelist.json`.

Usage::

    PYTHONPATH=. python3 scripts/btc_champion_search.py \
        --out-dir core/btc_champion --workers 4
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Dict, Mapping

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from scripts.rival_sweep import (
    buy_and_hold,
    collect,
    fold_slices,
    format_row,
    passes,
    pf,
    rival_items,
    run_rival,
)
from scripts.validate_on_venue_data import _okx_frame
from xauby.observability.replay_validation import load_bot_config


SYMBOL = "BTCUSDT"
VENUE = "BTC-USDT-SWAP"
INCUMBENT = "supertrend_ema200"

# Matches scripts/btc_supertrend_okx_pf_grid.py so the two BTC studies split and
# gate the same series identically.
SPLIT_RATIO = 0.70
WARMUP_BARS = 300
MIN_IS_TRADES = 30
MIN_OOS_TRADES = 10
N_FOLDS = 5

_G: Dict[str, Any] = {}


def _init_worker(config_path: str, paths: Mapping[str, str]) -> None:
    os.chdir(ROOT)
    _G.clear()
    _G["cfg"] = load_bot_config(config_path)
    for key, path in paths.items():
        _G[key] = pd.read_csv(path)

    df = _G["native4"]
    split = int(len(df) * SPLIT_RATIO)
    oos_lo = max(0, split - WARMUP_BARS)
    _G.update(split=split,
              is_df=df.iloc[:split].reset_index(drop=True),
              oos_df=df.iloc[oos_lo:].reset_index(drop=True),
              oos_skip=split - oos_lo)


def _run(frame: pd.DataFrame, item: Mapping[str, Any], skip_bars: int) -> Dict[str, Any]:
    # The daily frame is offered to every plugin; `run_rival` attaches it only to
    # those whose own config gates on it.
    return run_rival(frame, item, symbol=SYMBOL, engine_config=_G["cfg"],
                     skip_bars=skip_bars, label=VENUE,
                     df_regime=_G["native1"], regime_tf="1d")


def _eval(item: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        return {
            **dict(item),
            "is": _run(_G["is_df"], item, 0),
            "oos": _run(_G["oos_df"], item, _G["oos_skip"]),
            "full": _run(_G["native4"], item, 0),
        }
    except Exception as exc:  # one bad plugin must not lose the sweep
        return {**dict(item), "error": f"{type(exc).__name__}: {exc}"}


def _eval_folds(item: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        folds = [
            _run(frame, item, skip)
            for frame, skip in fold_slices(_G["native4"], n_folds=N_FOLDS,
                                           warmup_bars=WARMUP_BARS)
        ]
        return {"id": item["id"], "folds": folds}
    except Exception as exc:
        return {"id": item["id"], "error": f"{type(exc).__name__}: {exc}"}


def _write_frames(out_dir: Path) -> Dict[str, Any]:
    frames = {"native4": _okx_frame(SYMBOL, "4h"), "native1": _okx_frame(SYMBOL, "1d")}
    paths, ranges = {}, {}
    for key, frame in frames.items():
        path = out_dir / f"{key}.csv"
        frame.to_csv(path, index=False)
        paths[key] = str(path)
        ranges[key] = {
            "bars": len(frame),
            "start": str(pd.to_datetime(int(frame["open_time"].iloc[0]), unit="ms")),
            "end": str(pd.to_datetime(int(frame["open_time"].iloc[-1]), unit="ms")),
        }
    return {"paths": paths, "ranges": ranges, "frames": frames}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="bot_config.yaml")
    parser.add_argument("--out-dir", default="core/btc_champion")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--top-n", type=int, default=6)
    args = parser.parse_args()

    os.chdir(ROOT)
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    data = _write_frames(out_dir)
    for key, meta in data["ranges"].items():
        print(f"{key}: {meta['bars']} bars  {meta['start']} -> {meta['end']}")
    bh = buy_and_hold(data["frames"]["native4"])
    print(f"buy & hold {VENUE}: {bh['return_pct']:+.2f}%  MDD {bh['max_dd_pct']:.2f}%")

    # The incumbent is the benchmark, not a candidate, so it is added back as a
    # single explicit row rather than left in the challenger pool.
    items = rival_items(INCUMBENT)
    benchmark = [
        {"id": f"{INCUMBENT}__{'ls' if shorts else 'long'} (INCUMBENT)",
         "strategy": INCUMBENT, "enable_short": shorts, "maturity": "production",
         "incumbent": True, "override": {"enable_short": shorts}}
        for shorts in (True, False)
    ]
    everything = benchmark + items
    print(f"\n=== {len(everything)} runs ({len(benchmark)} benchmark, "
          f"{len(items)} challengers) ===", flush=True)

    with Pool(processes=max(1, args.workers), initializer=_init_worker,
              initargs=(str(Path(args.config).resolve()), data["paths"])) as pool:
        rows = collect(pool, _eval, everything, label="strategy")

        ok = [r for r in rows if not r.get("error")]
        ok.sort(key=lambda r: pf(r, "full"), reverse=True)
        print("\n-- every plugin on OKX BTC-USDT-SWAP 4h (own default config) --")
        print("   net is NOT comparable across rows (different sizing models); PF is.")
        for row in ok:
            gate = "pass" if passes(row, min_is=MIN_IS_TRADES,
                                    min_oos=MIN_OOS_TRADES) else "fail"
            mark = "  <== INCUMBENT" if row.get("incumbent") else ""
            print(f"{row['id']:40s} [{row.get('maturity')}] gate={gate}{mark}")
            print(f"    full {format_row(row, 'full')}")
            print(f"    IS   {format_row(row, 'is')}")
            print(f"    OOS  {format_row(row, 'oos')}")
        for row in rows:
            if row.get("error"):
                print(f"{row['id']:40s} ERROR {row['error'][:80]}")

        # Folds for the incumbent plus whatever cleared the gate on full PF.
        gated = [r for r in ok if passes(r, min_is=MIN_IS_TRADES,
                                         min_oos=MIN_OOS_TRADES)]
        finalist_ids = list(dict.fromkeys(
            [r["id"] for r in gated[: args.top_n]]
            + [r["id"] for r in ok if r.get("incumbent")]))
        by_id = {item["id"]: item for item in everything}
        print(f"\n=== folds for {len(finalist_ids)} finalists ===", flush=True)
        folds = collect(pool, _eval_folds, [by_id[i] for i in finalist_ids],
                        label="folds")

        print("\n-- five chronological folds --")
        for row in folds:
            if row.get("error"):
                print(f"{row['id']:40s} ERROR {row['error'][:80]}")
                continue
            pfs = [float(f.get("profit_factor") or 0) for f in row["folds"]]
            nets = [float(f.get("net_profit_pct") or 0) for f in row["folds"]]
            trades = sum(int(f.get("total_trades") or 0) for f in row["folds"])
            comp = 1.0
            for value in nets:
                comp *= 1 + value / 100
            print(f"{row['id']:40s} PF {', '.join(f'{p:.2f}' for p in pfs)}")
            print(f"    profitable {sum(1 for v in nets if v > 0)}/{N_FOLDS}  "
                  f"worst PF {min(pfs):.3f}  compounded {(comp - 1) * 100:+.2f}%  "
                  f"trades {trades}")

    out = out_dir / "btc_champion_search.json"
    out.write_text(json.dumps(
        {"ranges": data["ranges"], "buy_and_hold": bh, "rows": rows, "folds": folds},
        indent=2, default=str))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
