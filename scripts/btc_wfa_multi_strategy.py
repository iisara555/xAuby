#!/usr/bin/env python3
"""BTC 4h walk-forward evaluation across four strategy plugins.

Compares ``bbrsi_mean_reversion``, ``supertrend_ema200``, ``xauby_actionzone``
(CDC ActionZone) and ``xauby_smc_pro`` (SMC + FVG) on BTCUSDT 4h, long + short,
using the repo's own plugin replay engine (decision on the last CLOSED bar,
fill at the NEXT bar open — no look-ahead).

Walk-forward protocol (anchored rolling, monthly step):
  * train = 6 calendar months, test = the following 1 month
  * the per-strategy grid is optimized on train only; the winning override is
    replayed on the test month with a warmup lead-in of PRE-test bars
  * the window then rolls forward by 1 month and the search repeats

Market-phase labels (bull / bear / sideways) come from the 1d close vs EMA200
evaluated at the LAST daily close BEFORE the test month starts, so the label
itself uses no in-month information.

Usage:
  python scripts/btc_wfa_multi_strategy.py fetch    # download BTCUSDT 4h+1d
  python scripts/btc_wfa_multi_strategy.py run      # walk-forward, all strategies
  python scripts/btc_wfa_multi_strategy.py run --strategy supertrend_ema200
  python scripts/btc_wfa_multi_strategy.py report   # aggregate OOS by phase

Data comes from data.binance.vision (public archive). Results append to JSONL
checkpoints in core/btc_wfa/ so interrupted runs resume where they left off.
"""
from __future__ import annotations

import argparse
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

OUT_DIR = os.path.join(ROOT, "core", "btc_wfa")
SYMBOL = "BTCUSDT"
TRAIN_MONTHS = 6
TEST_MONTHS = 1
WARMUP_BARS = 300          # pre-window lead-in so indicators are warm (past data only)
MIN_TRAIN_TRADES = 3       # gate: prefer train winners with at least this many trades
FIRST_TEST_MONTH = (2021, 1)

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
    # 2025+ archives use microsecond timestamps; normalize to ms.
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
        print(f"{SYMBOL} {tf}: {len(df)} bars {first} -> {last}", flush=True)
        df.to_csv(os.path.join(OUT_DIR, f"{SYMBOL.lower()}_{tf}.csv"), index=False)


# --------------------------------------------------------------------------- #
# Strategy grids                                                              #
# --------------------------------------------------------------------------- #
def _grid(base: dict, axes: dict) -> list:
    keys = list(axes)
    combos = []
    for values in itertools.product(*(axes[k] for k in keys)):
        ov = dict(base)
        ov.update(dict(zip(keys, values)))
        cid = "_".join(f"{k}={v}" for k, v in zip(keys, values))
        combos.append((cid, ov))
    return combos


# Long + short everywhere. Exits/sizing are fixed per strategy so the search
# space stays honest (structural entry knobs only, small grids).
STRATEGIES: dict = {
    "bbrsi_mean_reversion": {
        "grid": _grid(
            {"enable_short": True},
            {
                "bb_std": [2.0, 2.5],
                "rsi_oversold": [25.0, 30.0],
                "sl_atr_mult": [1.8, 2.5],
            },
        ),
        "use_regime": False,
    },
    "supertrend_ema200": {
        "grid": _grid(
            {"enable_short": True},
            {
                "supertrend_mult": [2.5, 3.0, 3.5],
                "sl_atr_mult": [2.0, 2.5],
                "exit_on_ema_loss": [True, False],
            },
        ),
        "use_regime": False,
    },
    "xauby_actionzone": {
        "grid": _grid(
            {
                "enable_short": True,
                "disable_stop_loss": True,
                "position_pct": 0.95,
                "rsi_min": 0.0,
                "rsi_max": 100.0,
                "vol_min_ratio": 0.0,
            },
            {
                "ap_smoothing": [1, 2],
                "require_fresh_zone": [True],
                "fresh_zone_window": [1, 2, 3],
                "exit_on_bear_cross": [False, True],
            },
        ),
        "use_regime": False,
    },
    "xauby_smc_pro": {
        "grid": _grid(
            {
                "use_fair_value_gap": True,
                "use_order_block": True,
            },
            {
                "confluence_min_score": [1.0, 2.0],
                "require_liquidity_sweep": [False, True],
            },
        ),
        "use_regime": False,
    },
}


# --------------------------------------------------------------------------- #
# Walk-forward plumbing                                                       #
# --------------------------------------------------------------------------- #
def _month_add(y: int, m: int, k: int):
    m += k
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return y, m


def _month_ts(y: int, m: int) -> int:
    """Epoch SECONDS of the month start (normalize_ohlcv_df emits seconds)."""
    import pandas as pd

    return int(pd.Timestamp(year=y, month=m, day=1, tz="UTC").timestamp())


def _init_worker() -> None:
    os.chdir(ROOT)
    import pandas as pd
    from xauby.backtest.data import normalize_ohlcv_df
    from xauby.observability.replay_validation import load_bot_config
    from xauby.runtime.trading_config import split_legacy_runtime_config

    df4 = normalize_ohlcv_df(pd.read_csv(os.path.join(OUT_DIR, f"{SYMBOL.lower()}_4h.csv")))
    df1 = normalize_ohlcv_df(pd.read_csv(os.path.join(OUT_DIR, f"{SYMBOL.lower()}_1d.csv")))
    cfg = load_bot_config()
    base_cfgs, merged_cfgs = {}, {}
    for name in STRATEGIES:
        strat_cfg, trading_cfg = split_legacy_runtime_config(
            cfg, name, symbol=SYMBOL, for_live=False
        )
        merged = dict(cfg)
        merged["trading"] = trading_cfg
        base_cfgs[name] = dict(strat_cfg)
        merged_cfgs[name] = merged
    _G.update(df4=df4, df1=df1, base_cfgs=base_cfgs, merged_cfgs=merged_cfgs)


def _slice(df, start_ts: int, end_ts: int, warmup: int):
    """Bars in [start_ts, end_ts) plus up to `warmup` PRE-start bars (past only)."""
    idx = df.index[(df["timestamp"] >= start_ts) & (df["timestamp"] < end_ts)]
    if len(idx) == 0:
        return None
    lo = max(0, int(idx[0]) - warmup)
    return df.iloc[lo:int(idx[-1]) + 1].reset_index(drop=True), int(idx[0]) - lo


def _run(strategy: str, df, override: dict, skip_bars: int) -> dict:
    from xauby.backtest.replay import run_plugin_replay

    use_regime = STRATEGIES[strategy]["use_regime"] or override.get("use_d1_regime_filter")
    stats = run_plugin_replay(
        df,
        strategy_config={**_G["base_cfgs"][strategy], **override},
        engine_config=_G["merged_cfgs"][strategy],
        symbol=SYMBOL,
        strategy_name=strategy,
        df_regime=_G["df1"] if use_regime else None,
        primary_timeframe="4h",
        regime_timeframe="1d" if use_regime else None,
        min_bars_override=skip_bars,
    )
    keys = (
        "net_profit_pct", "win_rate", "profit_factor", "max_drawdown_pct",
        "total_trades", "sharpe", "sortino", "exposure_pct", "avg_holding_bars",
    )
    out = {k: stats.get(k) for k in keys}
    out["skip_bars"] = skip_bars
    return out


def _phase_label(test_start_ts: int) -> str:
    """Bull/bear/sideways from 1d EMA200 using ONLY closes before the test month."""
    import pandas_ta as ta

    df1 = _G["df1"]
    hist = df1[df1["timestamp"] < test_start_ts]
    if len(hist) < 210:
        return "unknown"
    close = hist["close"].astype(float).reset_index(drop=True)
    ema = ta.ema(close, length=200)
    last, e_now, e_prev = float(close.iloc[-1]), float(ema.iloc[-1]), float(ema.iloc[-21])
    slope_up = e_now > e_prev
    if last > e_now and slope_up:
        return "bull"
    if last < e_now and not slope_up:
        return "bear"
    return "sideways"


def eval_window(item):
    strategy, (ty, tm) = item
    wid = f"{strategy}:{ty}-{tm:02d}"
    try:
        train_y, train_m = _month_add(ty, tm, -TRAIN_MONTHS)
        train_start = _month_ts(train_y, train_m)
        test_start = _month_ts(ty, tm)
        test_end = _month_ts(*_month_add(ty, tm, TEST_MONTHS))

        train = _slice(_G["df4"], train_start, test_start, WARMUP_BARS)
        test = _slice(_G["df4"], test_start, test_end, WARMUP_BARS)
        if train is None or test is None:
            return {"id": wid, "error": "no data"}
        train_df, train_skip = train
        test_df, test_skip = test

        results = []
        for cid, ov in STRATEGIES[strategy]["grid"]:
            r = _run(strategy, train_df, ov, train_skip)
            results.append((cid, ov, r))

        gated = [x for x in results if (x[2]["total_trades"] or 0) >= MIN_TRAIN_TRADES]
        pool = gated or results
        cid, ov, train_stats = max(pool, key=lambda x: x[2]["net_profit_pct"] or -1e9)

        oos = _run(strategy, test_df, ov, test_skip)
        return {
            "id": wid,
            "strategy": strategy,
            "test_month": f"{ty}-{tm:02d}",
            "phase": _phase_label(test_start),
            "chosen": cid,
            "override": ov,
            "train": train_stats,
            "oos": oos,
            "grid_train": [
                {"id": c, "net": r["net_profit_pct"], "trades": r["total_trades"]}
                for c, _, r in results
            ],
        }
    except Exception as exc:
        return {"id": wid, "error": f"{type(exc).__name__}: {exc}"}


def _test_months():
    import pandas as pd

    df4 = pd.read_csv(os.path.join(OUT_DIR, f"{SYMBOL.lower()}_4h.csv"),
                      usecols=["open_time"])
    last_ts = int(df4["open_time"].iloc[-1]) // 1000  # CSV is ms; compare in s
    months = []
    y, m = FIRST_TEST_MONTH
    while _month_ts(*_month_add(y, m, TEST_MONTHS)) <= last_ts:
        months.append((y, m))
        y, m = _month_add(y, m, 1)
    return months


def cmd_run(only: str | None, jobs: int) -> None:
    months = _test_months()
    items = [(s, ym) for s in STRATEGIES if (only is None or s == only)
             for ym in months]
    out_path = os.path.join(OUT_DIR, "wfa_results.jsonl")
    done = set()
    if os.path.exists(out_path):
        with open(out_path) as f:
            done = {json.loads(l)["id"] for l in f if l.strip()}
    todo = [it for it in items if f"{it[0]}:{it[1][0]}-{it[1][1]:02d}" not in done]
    print(f"{len(todo)} windows to run ({len(done)} already done)", flush=True)
    t0 = time.time()
    with Pool(jobs, initializer=_init_worker) as p, open(out_path, "a") as out:
        for i, res in enumerate(p.imap_unordered(eval_window, todo), 1):
            out.write(json.dumps(res) + "\n")
            out.flush()
            el = time.time() - t0
            print(f"  {i}/{len(todo)} {res['id']}"
                  f"{' ERR ' + res['error'] if 'error' in res else ''}"
                  f"  elapsed {el/60:.1f}m eta {el/i*(len(todo)-i)/60:.1f}m",
                  flush=True)


# --------------------------------------------------------------------------- #
# Reporting                                                                   #
# --------------------------------------------------------------------------- #
def _agg(rows):
    import math

    nets = [r["oos"]["net_profit_pct"] or 0.0 for r in rows]
    trades = sum(r["oos"]["total_trades"] or 0 for r in rows)
    wins = sum(1 for n in nets if n > 0)
    compounded = 1.0
    for n in nets:
        compounded *= 1.0 + n / 100.0
    mean = sum(nets) / len(nets)
    sd = math.sqrt(sum((n - mean) ** 2 for n in nets) / len(nets)) if len(nets) > 1 else 0.0
    mdd = max((r["oos"]["max_drawdown_pct"] or 0.0) for r in rows)
    return {
        "months": len(nets),
        "pos_months": wins,
        "avg_month_net": mean,
        "sd_month_net": sd,
        "compounded_net": (compounded - 1.0) * 100.0,
        "trades": trades,
        "worst_month": min(nets),
        "best_month": max(nets),
        "max_month_dd": mdd,
    }


def cmd_report(md_path: str | None) -> None:
    path = os.path.join(OUT_DIR, "wfa_results.jsonl")
    rows = []
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if "error" not in r:
                rows.append(r)
    lines = []

    def emit(s: str = "") -> None:
        print(s)
        lines.append(s)

    strategies = sorted({r["strategy"] for r in rows})
    phases = ["bull", "bear", "sideways"]
    emit(f"# BTC {SYMBOL} 4h walk-forward (train {TRAIN_MONTHS}m -> test {TEST_MONTHS}m)")
    emit()
    hdr = ("| strategy | phase | months | +months | avg mo % | sd % | compounded % "
           "| worst mo % | best mo % | trades |")
    emit(hdr)
    emit("|" + "---|" * 10)
    for s in strategies:
        srows = [r for r in rows if r["strategy"] == s]
        for ph in phases + ["ALL"]:
            sub = srows if ph == "ALL" else [r for r in srows if r["phase"] == ph]
            if not sub:
                continue
            a = _agg(sub)
            emit(f"| {s} | {ph} | {a['months']} | {a['pos_months']} "
                 f"| {a['avg_month_net']:+.2f} | {a['sd_month_net']:.2f} "
                 f"| {a['compounded_net']:+.1f} | {a['worst_month']:+.2f} "
                 f"| {a['best_month']:+.2f} | {a['trades']} |")
    if md_path:
        with open(md_path, "w") as f:
            f.write("\n".join(lines) + "\n")
        print(f"\nwritten: {md_path}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("command", choices=["fetch", "run", "report"])
    p.add_argument("--strategy", default=None, help="run a single strategy")
    p.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    p.add_argument("--md", default=None, help="report: also write markdown here")
    args = p.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    if args.command == "fetch":
        cmd_fetch()
    elif args.command == "run":
        cmd_run(args.strategy, args.jobs)
    else:
        cmd_report(args.md)


if __name__ == "__main__":
    main()
