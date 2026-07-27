#!/usr/bin/env python3
"""Walk-forward config sweep for xauby_actionzone on XAU (PAXGUSDT proxy).

Institutional-style evaluation on top of the repo's own plugin replay engine:
IS/OOS split (70/30 with warmup lead-in), 5-fold walk-forward, production
costs (fee/slippage/funding from bot_config.yaml), and minimum-sample gates.
See docs/actionzone_config_search_2026-07.md for the July 2026 run.

Usage:
  python scripts/actionzone_wfa_sweep.py fetch     # download PAXGUSDT 4h+1d candles
  python scripts/actionzone_wfa_sweep.py stageA    # structural entry grid, IS+OOS
  python scripts/actionzone_wfa_sweep.py stageC    # focused candidates: IS/OOS + folds + full
  python scripts/actionzone_wfa_sweep.py report    # rank stageA results

Data comes from data.binance.vision (public archive; works where the
Binance REST API is geo-blocked). Results append to JSONL checkpoints in
core/actionzone_sweep/ so interrupted stages resume where they left off.
"""
from __future__ import annotations

import io
import itertools
import json
import os
import sys
import time
import zipfile
from datetime import date, timedelta
from multiprocessing import Pool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "core", "actionzone_sweep")
SYMBOL = "PAXGUSDT"
WARMUP = 300
SPLIT_RATIO = 0.70
N_FOLDS = 5
MIN_IS_TRADES, MIN_OOS_TRADES = 40, 18

_G: dict = {}


# --------------------------------------------------------------------------- #
# Data: data.binance.vision monthly + daily archives                          #
# --------------------------------------------------------------------------- #
KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "count", "taker_buy_base",
    "taker_buy_quote", "ignore",
]


def _fetch_zip(url: str):
    import pandas as pd
    import requests

    r = requests.get(url, timeout=60)
    if r.status_code != 200:
        return None
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        with zf.open(zf.namelist()[0]) as f:
            df = pd.read_csv(f, header=None, names=KLINE_COLS)
    if not str(df.iloc[0]["open_time"]).lstrip("-").isdigit():
        df = df.iloc[1:].reset_index(drop=True)
    return df


def fetch_candles(tf: str, start=(2020, 1)):
    """All monthly archives from `start` plus current-month daily archives."""
    import pandas as pd

    base = "https://data.binance.vision/data/spot"
    today = date.today()
    frames = []
    y, m = start
    last = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    while (y, m) <= last:
        df = _fetch_zip(f"{base}/monthly/klines/{SYMBOL}/{tf}/{SYMBOL}-{tf}-{y}-{m:02d}.zip")
        if df is not None:
            frames.append(df)
        m += 1
        if m > 12:
            m, y = 1, y + 1
    d = date(today.year, today.month, 1)
    while d < today:
        df = _fetch_zip(f"{base}/daily/klines/{SYMBOL}/{tf}/{SYMBOL}-{tf}-{d.isoformat()}.zip")
        if df is not None:
            frames.append(df)
        d += timedelta(days=1)
    out = pd.concat(frames, ignore_index=True)
    for c in KLINE_COLS:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna(subset=["open_time"])
    # 2025+ archives switched to microsecond timestamps; normalize to ms.
    for col in ("open_time", "close_time"):
        ts = out[col].astype("int64")
        out[col] = ts.where(ts < 10**14, ts // 1000)
    return out.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)


def cmd_fetch() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    import pandas as pd

    for tf in ("4h", "1d"):
        df = fetch_candles(tf)
        first = pd.to_datetime(df["open_time"].iloc[0], unit="ms")
        last = pd.to_datetime(df["open_time"].iloc[-1], unit="ms")
        print(f"{SYMBOL} {tf}: {len(df)} bars {first} -> {last}")
        df.to_csv(os.path.join(OUT_DIR, f"{SYMBOL.lower()}_{tf}.csv"), index=False)


# --------------------------------------------------------------------------- #
# Replay plumbing (repo engine, production config resolution)                 #
# --------------------------------------------------------------------------- #
def _init_worker() -> None:
    os.chdir(ROOT)
    import pandas as pd
    from xauby.backtest.data import normalize_ohlcv_df
    from xauby.observability.replay_validation import load_bot_config
    from xauby.runtime.trading_config import split_legacy_runtime_config

    df4 = normalize_ohlcv_df(pd.read_csv(os.path.join(OUT_DIR, f"{SYMBOL.lower()}_4h.csv")))
    df1 = normalize_ohlcv_df(pd.read_csv(os.path.join(OUT_DIR, f"{SYMBOL.lower()}_1d.csv")))
    cfg = load_bot_config()
    strat_cfg, trading_cfg = split_legacy_runtime_config(
        cfg, "xauby_actionzone", symbol="XAUUSDT", for_live=False
    )
    merged = dict(cfg)
    merged["trading"] = trading_cfg
    split = int(len(df4) * SPLIT_RATIO)
    oos_lo = max(0, split - WARMUP)
    _G.update(
        df4=df4,
        df1=df1,
        base_cfg=dict(strat_cfg),
        merged=merged,
        is_df=df4.iloc[:split].reset_index(drop=True),
        oos_df=df4.iloc[oos_lo:].reset_index(drop=True),
        oos_skip=split - oos_lo,
    )


def _run(df, override, skip_bars: int):
    """Replay `df`, treating its first `skip_bars` bars as warmup only.

    `skip_bars` is a required positional because omitting it is the whole bug:
    the frames below prepend a 300-bar lead-in, and without the override the
    replay skips only the strategy's own `min_bars` (100) and trades the other
    200 — bars that belong to the PREVIOUS window. Measured on this study's
    config, that leak moved OOS net from +43.29% to +47.78% and fold 3's PF from
    1.50 to 1.07; the published conclusions survive it, but nothing else should
    inherit it. See xauby/backtest/walkforward.py.
    """
    from xauby.backtest.replay import run_plugin_replay

    stats = run_plugin_replay(
        df,
        strategy_config={**_G["base_cfg"], **override},
        engine_config=_G["merged"],
        symbol="XAUUSDT",
        strategy_name="xauby_actionzone",
        df_regime=_G["df1"],
        primary_timeframe="4h",
        regime_timeframe="1d",
        min_bars_override=skip_bars,
    )
    keys = (
        "net_profit_pct", "win_rate", "profit_factor", "max_drawdown_pct",
        "total_trades", "sharpe", "sortino", "calmar", "cagr_pct",
        "exposure_pct", "avg_holding_bars", "max_consecutive_losses",
    )
    return {k: stats.get(k) for k in keys}


def _fold_frames():
    """(frame, skip_bars) per fold — the count travels with the frame."""
    df4 = _G["df4"]
    seg = len(df4) // N_FOLDS
    for f in range(N_FOLDS):
        fold_start = f * seg
        start = max(0, fold_start - WARMUP)
        end = len(df4) if f == N_FOLDS - 1 else (f + 1) * seg
        yield df4.iloc[start:end].reset_index(drop=True), fold_start - start


def eval_is_oos(item):
    combo_id, override = item
    try:
        return {
            "id": combo_id,
            "override": override,
            "is": _run(_G["is_df"], override, 0),
            "oos": _run(_G["oos_df"], override, _G["oos_skip"]),
        }
    except Exception as exc:
        return {"id": combo_id, "override": override, "error": str(exc)}


def eval_full(item):
    combo_id, override = item
    try:
        out = eval_is_oos(item)
        out["full"] = _run(_G["df4"], override, 0)
        out["folds"] = [_run(df, override, skip) for df, skip in _fold_frames()]
        return out
    except Exception as exc:
        return {"id": combo_id, "override": override, "error": str(exc)}


def _run_pool(items, fn, out_path):
    done = set()
    if os.path.exists(out_path):
        with open(out_path) as f:
            done = {json.loads(l)["id"] for l in f if l.strip()}
    todo = [(cid, ov) for cid, ov in items if cid not in done]
    print(f"{len(todo)} to run ({len(done)} already done)", flush=True)
    t0 = time.time()
    with Pool(os.cpu_count() or 4, initializer=_init_worker) as pool:
        with open(out_path, "a") as out:
            for i, res in enumerate(pool.imap_unordered(fn, todo), 1):
                out.write(json.dumps(res) + "\n")
                out.flush()
                if i % 4 == 0 or i == len(todo):
                    el = time.time() - t0
                    print(f"  {i}/{len(todo)}  elapsed {el/60:.1f}m "
                          f"eta {el/i*(len(todo)-i)/60:.1f}m", flush=True)


# --------------------------------------------------------------------------- #
# Grids                                                                       #
# --------------------------------------------------------------------------- #
LIVE_EXITS = {
    "enable_short": True,
    "disable_stop_loss": True,
    "position_pct": 0.95,
    "partial_tp_pct": 12.0,
    "partial_tp_fraction": 0.5,
    "rsi_min": 0.0,
    "rsi_max": 100.0,
    "vol_min_ratio": 0.0,
}


def stage_a_grid():
    fresh = [1, 2, 3]
    slope = [(False, 3), (True, 3), (True, 5)]
    combos = []
    for ap, fz, (slp, bars), thrust, d1, bearx in itertools.product(
        [1, 2], fresh, slope, [0.0, 0.5], [False, True], [False, True]
    ):
        ov = {
            "ap_smoothing": ap,
            "require_fresh_zone": True,
            "fresh_zone_window": fz,
            "require_slow_slope": slp,
            "slow_slope_bars": bars,
            "entry_thrust_min": thrust,
            "use_d1_regime_filter": d1,
            "exit_on_bear_cross": bearx,
            **LIVE_EXITS,
        }
        cid = f"ap{ap}_fz{fz}_sl{bars if slp else 0}_th{thrust}_d1{int(d1)}_bx{int(bearx)}"
        combos.append((cid, ov))
    return combos


def stage_c_candidates():
    base = {
        "ap_smoothing": 2,
        "require_fresh_zone": True,
        "require_slow_slope": True,
        "slow_slope_bars": 3,
        "entry_thrust_min": 0.0,
        "exit_on_bear_cross": False,
        **LIVE_EXITS,
    }
    named = {
        "live_baseline": {},
        "LO_fz1_ptp12": {"enable_short": False, "fresh_zone_window": 1},
        "LO_fz1_ptp12_d1": {"enable_short": False, "fresh_zone_window": 1,
                            "use_d1_regime_filter": True},
        "LO_fz3_ptp12_d1": {"enable_short": False, "fresh_zone_window": 3,
                            "use_d1_regime_filter": True},
        "LS_fz2_ptp12_d1": {"enable_short": True, "fresh_zone_window": 2,
                            "use_d1_regime_filter": True},
    }
    return [(name, ({**base, "use_d1_regime_filter": False, "fresh_zone_window": 1, **ov}
                    if name != "live_baseline" else {}))
            for name, ov in named.items()]


def cmd_report() -> None:
    path = os.path.join(OUT_DIR, "stageA.jsonl")
    if not os.path.exists(path):
        raise SystemExit(f"no results at {path} — run `stageA` first "
                         f"(archived run: docs/research/actionzone_sweep_2026-07/)")
    rows = []
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if "error" not in r:
                rows.append(r)
    valid = [
        r for r in rows
        if r["is"]["total_trades"] >= MIN_IS_TRADES
        and r["oos"]["total_trades"] >= MIN_OOS_TRADES
        and r["is"]["net_profit_pct"] > 0
        and r["oos"]["net_profit_pct"] > 0
    ]
    print(f"{len(rows)} results, {len(valid)} pass validity gates")
    valid.sort(key=lambda r: r["oos"]["profit_factor"], reverse=True)
    for r in valid[:15]:
        i, o = r["is"], r["oos"]
        print(f"{r['id']:<42} IS pf={i['profit_factor']:.2f} wr={i['win_rate']:.0f}% "
              f"np={i['net_profit_pct']:+6.1f}% t={i['total_trades']:>3} | "
              f"OOS pf={o['profit_factor']:.2f} wr={o['win_rate']:.0f}% "
              f"np={o['net_profit_pct']:+6.1f}% t={o['total_trades']:>3}")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    os.makedirs(OUT_DIR, exist_ok=True)
    if cmd == "fetch":
        cmd_fetch()
    elif cmd == "stageA":
        _run_pool(stage_a_grid(), eval_is_oos, os.path.join(OUT_DIR, "stageA.jsonl"))
    elif cmd == "stageC":
        _run_pool(stage_c_candidates(), eval_full, os.path.join(OUT_DIR, "stageC.jsonl"))
    elif cmd == "report":
        cmd_report()
    else:
        raise SystemExit(f"unknown command: {cmd}")


if __name__ == "__main__":
    main()
