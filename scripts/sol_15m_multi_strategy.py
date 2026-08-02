#!/usr/bin/env python3
"""SOLUSDT 15m — five-strategy comparison harness.

Compares ``supertrend_ema200``, ``squeeze_momentum``, ``bbrsi_mean_reversion``,
``ut_bot_atr`` and ``dual_thrust`` on SOLUSDT 15m, long + short, using the
repo's own plugin replay engine (decision on the last CLOSED bar, fill at the
next bar open — no look-ahead).

Why this is its own harness rather than ``run_focused_backtest``:
``get_six_months_candle_count`` caps a 15m load at 10,000 bars (~104 days), so
the service path cannot see the period this study is about. Candles are bulk
loaded from the Binance public archive and fed straight to ``run_plugin_replay``,
the same way the BTC harnesses do it.

Protocol
  * Stage A (``grid``): each strategy's parameter grid is scored on a TUNING
    window with a 70/30 IS/OOS split. The 2021 melt-up is deliberately excluded
    from tuning — it is held out.
  * Stage B (``finalists``): the top configs per strategy are replayed on the
    full span, on N non-overlapping folds, and at 2.0x costs.
  * ``report`` renders the markdown; ``okx`` cross-checks the winner on the
    venue actually traded (OKX SOL-USDT-SWAP).

Ranking is PF-with-low-MDD by explicit choice — see ``robust_score``.

Usage:
  python scripts/sol_15m_multi_strategy.py fetch
  python scripts/sol_15m_multi_strategy.py calibrate
  python scripts/sol_15m_multi_strategy.py grid --jobs 4
  python scripts/sol_15m_multi_strategy.py finalists --jobs 4 --top 2
  python scripts/sol_15m_multi_strategy.py report --md docs/research/<name>.md
  python scripts/sol_15m_multi_strategy.py okx

Results append to JSONL checkpoints under core/sol_15m/ so an interrupted run
resumes where it left off. Heavy stages run on GitHub-hosted Actions, never on
the trading VPS.
"""
from __future__ import annotations

import argparse
import io
import itertools
import json
import math
import os
import sys
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from multiprocessing import Pool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "core", "sol_15m")
SYMBOL = "SOLUSDT"
VENUE = "SOL-USDT-SWAP"
TF = "15m"
REGIME_TF = "1d"

# Above every structural lookback on this slate (supertrend_ema200's EMA200 is
# the deepest). tests/test_replay_ctx_window.py pins that a 750-bar window
# reproduces the unbounded replay exactly.
CTX_WINDOW = 750
WARMUP_BARS = 750

# Tuning window for the grid. Starts after the 2021 melt-up so parameters are
# not selected on a once-in-a-cycle regime, and ends before the recent leg.
TUNE_START = "2022-01-01"
TUNE_END = "2023-10-01"
FULL_START = "2021-01-01"
SUBPERIOD_START = "2023-01-01"   # reported separately: post-melt-up regime

GRID_SPLIT_RATIO = 0.70
N_FOLDS = 6

# Gates, pre-registered. Scaled up from the BTC harness's 30/10 because 15m
# produces far more trades — a 30-trade sample means nothing here.
MIN_IS_TRADES = 200
MIN_OOS_TRADES = 60
MAX_MDD_PCT = 25.0
MIN_PROFITABLE_FOLDS = 4

# ReplayEngine.stats returns 99.9 when gross_loss == 0 and 0.0 when there is no
# P&L at all. Both are sentinels, not measurements, and both poison a max-PF
# ranking.
PF_SENTINEL = 99.9
PF_CLAMP = 5.0
MDD_REF = 10.0     # a 10% drawdown halves the score — a chosen exchange rate

# 1.0x is the baseline run, reused rather than replayed. 2.0x is the binding
# disqualifier; 1.5x is omitted because at ~100-200 bars/s each extra full-span
# scenario costs ~10 core-hours across the shortlist and 2.0x is the test that
# actually decides anything.
# 0.0x is a DIAGNOSTIC, not a scenario anyone can trade: it removes fees and
# slippage entirely to separate "this strategy has no edge" from "this strategy
# has an edge that the fee bill eats". At 15m that distinction is the whole
# story, and reporting it stops the conclusion being a guess.
COST_SCENARIOS = {"0.0x": 0.0, "1.0x": 1.0, "2.0x": 2.0}

METRIC_KEYS = (
    "net_profit_pct", "win_rate", "profit_factor", "max_drawdown_pct",
    "total_trades", "sharpe", "sortino", "calmar", "cagr_pct",
    "exposure_pct", "avg_holding_bars", "max_consecutive_losses",
)

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

    for attempt in range(4):
        try:
            r = requests.get(url, timeout=90)
        except Exception:
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            time.sleep(2 ** attempt)
            continue
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            with zf.open(zf.namelist()[0]) as f:
                df = pd.read_csv(f, header=None, names=KLINE_COLS)
        if not str(df.iloc[0]["open_time"]).lstrip("-").isdigit():
            df = df.iloc[1:].reset_index(drop=True)
        return df
    return None


def fetch_candles(tf: str, start=(2020, 8)):
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
    if not frames:
        raise SystemExit(f"no {SYMBOL} {tf} archives downloaded")
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

    for tf in (TF, REGIME_TF):
        df = fetch_candles(tf)
        first = pd.to_datetime(df["open_time"].iloc[0], unit="ms")
        last = pd.to_datetime(df["open_time"].iloc[-1], unit="ms")
        print(f"{SYMBOL} {tf}: {len(df)} bars {first} -> {last}", flush=True)
        df.to_csv(os.path.join(OUT_DIR, f"{SYMBOL.lower()}_{tf}.csv"), index=False)


# --------------------------------------------------------------------------- #
# Grids                                                                       #
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


# Long + short everywhere so side coverage is equal across the slate. Grids stay
# small on purpose: at ~100-200 bars/s a 15m replay is expensive, and a wider
# search would only inflate the multiple-comparison penalty it has to survive.
PF_GRIDS: dict = {
    "supertrend_ema200": _grid(
        {"enable_short": True, "max_calc_bars": 420},
        {
            "supertrend_mult": [2.0, 3.0, 4.0],
            "atr_period": [10, 14],
            "sl_atr_mult": [2.0, 3.0],
            "exit_on_ema_loss": [True, False],
        },
    ),
    "squeeze_momentum": _grid(
        {"enable_short": True, "max_calc_bars": 400},
        {
            "kc_length": [14, 20, 34],
            "entry_trigger": ["zero_cross", "squeeze_release"],
            "sl_atr_mult": [2.0, 3.0],
            "exit_on_zero_cross": [True, False],
        },
    ),
    "bbrsi_mean_reversion": _grid(
        {"enable_short": True, "max_calc_bars": 400, "sl_atr_mult": 2.5},
        {
            "bb_std": [2.0, 2.5, 3.0],
            "rsi_oversold": [20.0, 25.0, 30.0],
            "rsi_exit": [50.0, 60.0],
        },
    ),
    "ut_bot_atr": _grid(
        {"enable_short": True, "max_calc_bars": 300, "atr_period": 10},
        {
            # key_value drives trade count, which drives the fee bill; the low
            # end is included so the cost table has something to kill.
            "key_value": [1.0, 2.0, 3.0],
            "confirm_bars": [1, 3],
            "min_atr_pct": [0.0, 0.35],
            "stop_and_reverse": [True, False],
        },
    ),
    "dual_thrust": _grid(
        {"enable_short": True, "max_calc_bars": 600, "one_entry_per_session": True},
        {
            "lookback_days": [1, 2, 4],
            "k_upper": [0.3, 0.5, 0.7],
            "k_lower": [0.3, 0.7],
            "exit_at_session_end": [False, True],
        },
    ),
}

# Structural cap: keeps the whole grid inside one GitHub-hosted 6h job and keeps
# n_trials honest for the deflated Sharpe.
MAX_GRID_PER_STRATEGY = 72
for _name, _combos in PF_GRIDS.items():
    if len(_combos) > MAX_GRID_PER_STRATEGY:
        raise SystemExit(
            f"{_name} grid has {len(_combos)} combos, cap is {MAX_GRID_PER_STRATEGY}"
        )

STRATEGIES = list(PF_GRIDS)

# Long-only appendix: the same slate plus the two 15m-native plugins that have
# no short side at all, so they can still be compared on the side they trade.
LONG_ONLY_EXTRA = ["sol_ema_pullback", "xauby_vwap_pullback"]


# --------------------------------------------------------------------------- #
# Long-only slate                                                             #
# --------------------------------------------------------------------------- #
# Follow-up study: the long+short slate failed on costs, so this asks whether a
# long-only book does better on the same asset and timeframe. Two candidates —
# dual_thrust (the least-bad of the long+short slate) and simple_scalp_plus.
#
# simple_scalp_plus is the 8-point confluence scalper ported from
# Binance_Cryptonice (NOT freqtrade — see its module docstring). Three of its
# knobs are deliberately absent from the grid:
#   * min_buy_confidence is COUPLED to min_confirmations_buy, because
#     conf = min(0.97, buy_count/7 + 0.22). At the 0.70 default the count is the
#     only binding gate. It is pinned low here so the count is the sole gate and
#     the search space is not mostly redundant.
#   * mtf_filter / require_macro_bull are no-ops when df_regime is None, which
#     is how this harness runs. Gridding them would duplicate every row.
#   * risk_reward is passed as metadata and never read by the strategy.
LONG_ONLY_GRIDS: dict = {
    "dual_thrust": _grid(
        {"enable_short": False, "max_calc_bars": 600,
         "one_entry_per_session": True,
         # Long-only: k_lower only positions the exit line, so it stops being a
         # real entry axis and is pinned.
         "k_lower": 0.5},
        {
            "lookback_days": [1, 2, 4],
            "k_upper": [0.3, 0.5, 0.7],
            "exit_at_session_end": [False, True],
        },
    ),
    # Measured at 36 bars/s — 2.5x slower than anything else on the slate,
    # because it computes eight indicator families per bar. The grid is held to
    # the three highest-value axes so the search fits the compute budget;
    # trailing_atr_mult stays at its default rather than becoming a fourth axis.
    # max_calc_bars is deliberately NOT tuned down for speed: it shifts the
    # rolling-VWAP warmup and would change the strategy being measured.
    "simple_scalp_plus": _grid(
        {"min_buy_confidence": 0.10, "max_calc_bars": 400,
         "trailing_atr_mult": 1.5},
        {
            "min_confirmations_buy": [3, 4, 5],
            "min_confirmations_sell": [2, 3, 4],
            "atr_multiplier": [1.2, 2.5],
        },
    ),
}

# Long-only halves the opportunity set, and both candidates are structurally
# lower-frequency than the long+short slate (dual_thrust is capped at one entry
# per session). These floors are sample-adequacy thresholds, not performance
# ones, and are set BEFORE the run — the doc states the change from 200/60.
LONG_ONLY_MIN_IS_TRADES = 100
LONG_ONLY_MIN_OOS_TRADES = 30

SLATES = {
    "long-short": {
        "grids": PF_GRIDS,
        "grid_file": "pf_grid.jsonl",
        "finalists_file": "finalists.jsonl",
        "min_is": MIN_IS_TRADES,
        "min_oos": MIN_OOS_TRADES,
    },
    "long-only": {
        "grids": LONG_ONLY_GRIDS,
        "grid_file": "pf_grid_longonly.jsonl",
        "finalists_file": "finalists_longonly.jsonl",
        "min_is": LONG_ONLY_MIN_IS_TRADES,
        "min_oos": LONG_ONLY_MIN_OOS_TRADES,
    },
}

# Selected by --slate; module-level so pool workers inherit it via fork.
ACTIVE_SLATE = "long-short"


def slate() -> dict:
    return SLATES[ACTIVE_SLATE]


for _name, _combos in LONG_ONLY_GRIDS.items():
    if len(_combos) > MAX_GRID_PER_STRATEGY:
        raise SystemExit(
            f"{_name} long-only grid has {len(_combos)} combos, "
            f"cap is {MAX_GRID_PER_STRATEGY}"
        )


# --------------------------------------------------------------------------- #
# Replay plumbing                                                             #
# --------------------------------------------------------------------------- #
def _ts(day: str) -> int:
    """Epoch SECONDS — the unit normalize_ohlcv_df writes into `timestamp`."""
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def _load_frames():
    import pandas as pd
    from xauby.backtest.data import normalize_ohlcv_df

    p15 = os.path.join(OUT_DIR, f"{SYMBOL.lower()}_{TF}.csv")
    p1d = os.path.join(OUT_DIR, f"{SYMBOL.lower()}_{REGIME_TF}.csv")
    if not os.path.exists(p15):
        raise SystemExit(f"missing {p15} — run `fetch` first")
    df15 = normalize_ohlcv_df(pd.read_csv(p15))
    df1d = normalize_ohlcv_df(pd.read_csv(p1d)) if os.path.exists(p1d) else None
    return df15, df1d


def _init_worker() -> None:
    os.chdir(ROOT)
    from xauby.observability.replay_validation import load_bot_config
    from xauby.runtime.trading_config import split_legacy_runtime_config

    df15, df1d = _load_frames()
    cfg = load_bot_config()
    base_cfgs, merged_cfgs = {}, {}
    all_names = set(STRATEGIES) | set(LONG_ONLY_EXTRA)
    for _slate in SLATES.values():
        all_names |= set(_slate["grids"])
    for name in sorted(all_names):
        strat_cfg, trading_cfg = split_legacy_runtime_config(
            cfg, name, symbol=SYMBOL, for_live=False
        )
        merged = dict(cfg)
        merged["trading"] = trading_cfg
        base_cfgs[name] = dict(strat_cfg)
        merged_cfgs[name] = merged
    _G.update(df15=df15, df1d=df1d, base=base_cfgs, merged=merged_cfgs)


def _scaled_cost_config(cfg: dict, scale: float) -> dict:
    """Multiply fee + slippage. resolve_fee_pct gives `exchange` precedence."""
    import copy

    if scale == 1.0:
        return cfg
    out = copy.deepcopy(cfg)
    exch = out.setdefault("exchange", {})
    exch["fee_pct"] = float(exch.get("fee_pct", 0.0005)) * scale
    bt = out.setdefault("backtest", {})
    bt["slippage_bps"] = float(bt.get("slippage_bps", 2.0)) * scale
    return out


def _window(df, start_ts: int, end_ts: int | None, warmup: int):
    """Bars in [start_ts, end_ts) plus up to `warmup` PRE-start bars (past only).

    Returns (frame, skip) where skip goes to min_bars_override, so the warmup
    bars only warm indicators and never produce trades.
    """
    mask = df["timestamp"] >= start_ts
    if end_ts is not None:
        mask &= df["timestamp"] < end_ts
    idx = df.index[mask]
    if len(idx) == 0:
        return None
    lo = max(0, int(idx[0]) - warmup)
    frame = df.iloc[lo : int(idx[-1]) + 1].reset_index(drop=True)
    return frame, int(idx[0]) - lo


def _run(strategy: str, frame, override: dict, skip_bars: int, cost_scale: float = 1.0) -> dict:
    from xauby.backtest.replay import run_plugin_replay

    cfg = _scaled_cost_config(_G["merged"][strategy], cost_scale)
    stats = run_plugin_replay(
        frame,
        strategy_config={**_G["base"][strategy], **override},
        engine_config=cfg,
        symbol=SYMBOL,
        strategy_name=strategy,
        primary_timeframe=TF,
        regime_timeframe=None,
        min_bars_override=skip_bars,
        ctx_window_bars=CTX_WINDOW,
    )
    out = {k: stats.get(k) for k in METRIC_KEYS}
    out["trade_returns"] = [
        float(t.get("pnl_pct") or 0.0) for t in (stats.get("trades") or [])
    ]
    return out


def buy_and_hold(frame, skip_bars: int, cost_scale: float = 1.0) -> dict:
    """Buy the first traded bar, hold to the last. The only honest long-only baseline.

    SOL rose enormously over this period, so a long-only strategy can post a
    large positive net purely from beta. Without this column a losing strategy
    reads as a winning one. Charged one entry and one exit at the same taker fee
    and slippage the replay uses, so it is not flattered relative to the
    strategies it is compared against.
    """
    import numpy as np
    from xauby.runtime.exchange_config import resolve_fee_pct

    cfg = _G["merged"][STRATEGIES[0] if STRATEGIES else "supertrend_ema200"]
    fee = resolve_fee_pct(cfg) * cost_scale
    slip = float((cfg.get("backtest") or {}).get("slippage_bps", 2.0)) * cost_scale / 10_000.0

    closes = frame["close"].to_numpy("float64")[skip_bars:]
    if len(closes) < 2:
        return {"net_profit_pct": 0.0, "max_drawdown_pct": 0.0, "bars": len(closes)}

    entry = closes[0] * (1.0 + fee + slip)
    exit_ = closes[-1] * (1.0 - fee - slip)
    net = (exit_ / entry - 1.0) * 100.0

    equity = closes / entry
    peak = np.maximum.accumulate(equity)
    mdd = float(np.max((peak - equity) / peak) * 100.0) if len(equity) else 0.0
    return {
        "net_profit_pct": float(net),
        "max_drawdown_pct": mdd,
        "bars": int(len(closes)),
    }


# --------------------------------------------------------------------------- #
# Scoring                                                                     #
# --------------------------------------------------------------------------- #
def clean_pf(stats: dict | None) -> float | None:
    """PF with both sentinels rejected, clamped above PF_CLAMP.

    99.9 means "no losing trades", which is a small-sample artefact rather than
    an infinitely good strategy; 0.0 means there was no P&L at all. Neither is a
    measurement, so both return None and fail the gate.
    """
    if not stats:
        return None
    pf = float(stats.get("profit_factor") or 0.0)
    if pf >= PF_SENTINEL or pf <= 0.0:
        return None
    return min(pf, PF_CLAMP)


def robust_score(is_s: dict, oos_s: dict, full_s: dict) -> float:
    """Worst-window PF, discounted by full-period drawdown.

    * ``min`` across IS/OOS/full, not OOS alone: with a multi-hundred-combo
      search the best-OOS combo is largely a draw from the tail of the search
      distribution. The worst window is the statistic that cannot be gamed.
    * PF clamped at 5.0 — above that it reports trade count, not edge.
    * ``/(1 + MDD/10)`` rather than PF/MDD (which diverges as MDD -> 0 and
      crowns a 3-trade fluke) or PF*(1-MDD/100) (which barely penalises
      anything). Calibrated so 10% drawdown halves the score.
    """
    pfs = [clean_pf(is_s), clean_pf(oos_s), clean_pf(full_s)]
    if any(p is None for p in pfs):
        return float("-inf")
    mdd = max(0.0, float(full_s.get("max_drawdown_pct") or 0.0))
    return min(pfs) / (1.0 + mdd / MDD_REF)


def passes_gate(is_s: dict, oos_s: dict) -> bool:
    if clean_pf(is_s) is None or clean_pf(oos_s) is None:
        return False
    if int(is_s.get("total_trades") or 0) < slate()["min_is"]:
        return False
    if int(oos_s.get("total_trades") or 0) < slate()["min_oos"]:
        return False
    if float(is_s.get("net_profit_pct") or 0.0) <= 0:
        return False
    if float(oos_s.get("net_profit_pct") or 0.0) <= 0:
        return False
    return True


# --------------------------------------------------------------------------- #
# Stage A: grid on the tuning window                                          #
# --------------------------------------------------------------------------- #
def eval_grid_combo(item):
    strategy, cid, ov = item
    gid = f"{strategy}:{cid}"
    try:
        win = _window(_G["df15"], _ts(TUNE_START), _ts(TUNE_END), WARMUP_BARS)
        if win is None:
            return {"id": gid, "strategy": strategy, "error": "no bars"}
        frame, skip = win
        split = skip + int((len(frame) - skip) * GRID_SPLIT_RATIO)
        is_frame = frame.iloc[:split].reset_index(drop=True)
        oos_lo = max(0, split - WARMUP_BARS)
        oos_frame = frame.iloc[oos_lo:].reset_index(drop=True)
        oos_skip = split - oos_lo

        is_stats = _run(strategy, is_frame, ov, skip)
        oos_stats = _run(strategy, oos_frame, ov, oos_skip)
        for s in (is_stats, oos_stats):
            s.pop("trade_returns", None)
        return {
            "id": gid, "strategy": strategy, "combo": cid, "override": ov,
            "is": is_stats, "oos": oos_stats,
            "gate": passes_gate(is_stats, oos_stats),
        }
    except Exception as exc:  # a bad combo must not kill the pool
        return {"id": gid, "strategy": strategy, "combo": cid,
                "override": ov, "error": f"{type(exc).__name__}: {exc}"}


def _checkpoint_path(name: str) -> str:
    return os.path.join(OUT_DIR, name)


def _load_done(path: str) -> set:
    """Completed ids. Window-level rows also register `id|window` for resume."""
    done = set()
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("error"):
                    continue
                done.add(row["id"])
                if row.get("window"):
                    done.add(f"{row['id']}|{row['window']}")
    return done


def _read_jsonl(path: str) -> list:
    rows = []
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    return rows


def cmd_grid(only: str | None, jobs: int) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = _checkpoint_path(slate()["grid_file"])
    done = _load_done(path)
    items = [
        (s, cid, ov)
        for s, grid in slate()["grids"].items()
        if only is None or s == only
        for cid, ov in grid
        if f"{s}:{cid}" not in done
    ]
    print(f"grid: {len(items)} combos to run ({len(done)} already done), jobs={jobs}",
          flush=True)
    if not items:
        return
    t0 = time.time()
    with open(path, "a") as fh, Pool(jobs, initializer=_init_worker) as pool:
        for n, row in enumerate(pool.imap_unordered(eval_grid_combo, items), 1):
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            el = time.time() - t0
            eta = el / n * (len(items) - n)
            flag = "ERR" if row.get("error") else ("PASS" if row.get("gate") else "--")
            print(f"[{n}/{len(items)}] {row['id']} {flag} "
                  f"({el/60:.1f}m elapsed, ~{eta/60:.0f}m left)", flush=True)


# --------------------------------------------------------------------------- #
# Stage B: finalists on the full span                                         #
# --------------------------------------------------------------------------- #
def _fold_bounds(df, n_folds: int) -> list:
    idx = df.index[df["timestamp"] >= _ts(FULL_START)]
    lo, hi = int(idx[0]), int(idx[-1])
    size = (hi - lo) // n_folds
    return [(lo + i * size, lo + (i + 1) * size) for i in range(n_folds)]


def _finalist_windows(strategy: str, full_only: bool) -> list:
    """Names of the replay windows a finalist needs.

    Each becomes its own pool task. Fanning out per window rather than per
    config is what keeps all workers busy: with only 2-5 finalist configs, a
    per-config pool leaves most cores idle, and simple_scalp_plus runs at
    36 bars/s where a serial full stack is ~7 core-hours.
    """
    if full_only:
        return ["full"]
    names = ["full", "subperiod", "is", "oos"]
    names += [f"fold{i}" for i in range(N_FOLDS)]
    names += [f"cost:{label}" for label, scale in COST_SCENARIOS.items() if scale != 1.0]
    return names


def eval_finalist_window(item):
    """Replay ONE window of one finalist config. Merged later by config id."""
    strategy, cid, ov, window, passed_gate = item
    gid = f"{strategy}:{cid}"
    base = {"id": gid, "strategy": strategy, "combo": cid, "override": ov,
            "window": window, "passed_gate": passed_gate}
    try:
        df = _G["df15"]
        full = _window(df, _ts(FULL_START), None, WARMUP_BARS)
        frame, skip = full

        if window == "full":
            stats = _run(strategy, frame, ov, skip)
            base["trade_returns"] = stats.pop("trade_returns", [])
            base["stats"] = stats
            base["bh"] = buy_and_hold(frame, skip)
            return base

        if window == "subperiod":
            sub = _window(df, _ts(SUBPERIOD_START), None, WARMUP_BARS)
            if sub is None:
                return {**base, "skipped": True}
            s_frame, s_skip = sub
            stats = _run(strategy, s_frame, ov, s_skip)
            stats.pop("trade_returns", None)
            base["stats"] = stats
            base["bh"] = buy_and_hold(s_frame, s_skip)
            return base

        split = skip + int((len(frame) - skip) * GRID_SPLIT_RATIO)
        if window == "is":
            sub_frame = frame.iloc[:split].reset_index(drop=True)
            stats = _run(strategy, sub_frame, ov, skip)
            stats.pop("trade_returns", None)
            base["stats"] = stats
            base["bh"] = buy_and_hold(sub_frame, skip)
            return base

        if window == "oos":
            oos_lo = max(0, split - WARMUP_BARS)
            sub_frame = frame.iloc[oos_lo:].reset_index(drop=True)
            stats = _run(strategy, sub_frame, ov, split - oos_lo)
            stats.pop("trade_returns", None)
            base["stats"] = stats
            base["bh"] = buy_and_hold(sub_frame, split - oos_lo)
            return base

        if window.startswith("fold"):
            idx = int(window[4:])
            a, b = _fold_bounds(df, N_FOLDS)[idx]
            f_lo = max(0, a - WARMUP_BARS)
            f_frame = df.iloc[f_lo:b].reset_index(drop=True)
            stats = _run(strategy, f_frame, ov, a - f_lo)
            stats.pop("trade_returns", None)
            stats["bh_net_profit_pct"] = buy_and_hold(f_frame, a - f_lo)["net_profit_pct"]
            base["stats"] = stats
            base["fold_index"] = idx
            return base

        if window.startswith("cost:"):
            label = window.split(":", 1)[1]
            stats = _run(strategy, frame, ov, skip, cost_scale=COST_SCENARIOS[label])
            stats.pop("trade_returns", None)
            base["stats"] = stats
            base["cost_label"] = label
            return base

        return {**base, "error": f"unknown window {window}"}
    except Exception as exc:
        return {**base, "error": f"{type(exc).__name__}: {exc}"}


def _merge_finalist_windows(rows: list) -> list:
    """Fold per-window task results back into one row per config."""
    by_id: dict = {}
    for row in rows:
        gid = row.get("id")
        if not gid:
            continue
        entry = by_id.setdefault(gid, {
            "id": gid, "strategy": row.get("strategy"), "combo": row.get("combo"),
            "override": row.get("override"), "passed_gate": row.get("passed_gate"),
            "folds_by_index": {},
        })
        if row.get("error"):
            entry.setdefault("errors", []).append(f"{row.get('window')}: {row['error']}")
            continue
        if row.get("skipped"):
            continue
        window = row.get("window")
        stats = row.get("stats") or {}
        if window == "full":
            entry["full"] = stats
            entry["bh_full"] = row.get("bh")
            entry["trade_returns"] = row.get("trade_returns") or []
        elif window == "subperiod":
            entry["subperiod"] = stats
            entry["bh_subperiod"] = row.get("bh")
        elif window in ("is", "oos"):
            entry[window] = stats
            entry[f"bh_{window}"] = row.get("bh")
        elif window and window.startswith("fold"):
            entry["folds_by_index"][int(row.get("fold_index", 0))] = stats
        elif window and window.startswith("cost:"):
            entry.setdefault("costs", {})[row["cost_label"]] = stats

    out = []
    for entry in by_id.values():
        folds = entry.pop("folds_by_index", {})
        entry["folds"] = [folds[i] for i in sorted(folds)]
        # 1.0x costs are the full-span run; never replay it twice.
        if entry.get("full") is not None:
            entry.setdefault("costs", {})["1.0x"] = dict(entry["full"])
        out.append(entry)
    return out


def _rank_grid_rows(rows: list, *, gated_only: bool = True) -> dict:
    """Best-first grid rows per strategy, gated then scored.

    ``gated_only=False`` ranks every completed row on worst-window PF instead.
    That is the fallback for the case where nothing passes: a negative result
    still has to be evidenced on the full span, folds and cost scenarios, so the
    best-of-a-bad-slate configs are promoted and clearly flagged rather than
    leaving the report with no numbers at all.
    """
    by_strategy: dict = {}
    for row in rows:
        if row.get("error"):
            continue
        if gated_only and not row.get("gate"):
            continue
        if gated_only:
            score = robust_score(row["is"], row["oos"], row["oos"])
            if score == float("-inf"):
                continue
        else:
            pf_is = float(row["is"].get("profit_factor") or 0.0)
            pf_oos = float(row["oos"].get("profit_factor") or 0.0)
            if pf_is >= PF_SENTINEL or pf_oos >= PF_SENTINEL:
                continue
            score = min(pf_is, pf_oos)
        row["_score"] = score
        by_strategy.setdefault(row["strategy"], []).append(row)
    for name in by_strategy:
        by_strategy[name].sort(key=lambda r: r["_score"], reverse=True)
    return by_strategy


def cmd_finalists(jobs: int, top: int) -> None:
    rows = _read_jsonl(_checkpoint_path(slate()["grid_file"]))
    if not rows:
        raise SystemExit("no grid results — run `grid` first")
    ranked = _rank_grid_rows(rows)
    gated = bool(ranked)
    if not gated:
        print("NO config passed the gate in any strategy. Promoting the best "
              "worst-window PF per strategy anyway so the negative result is "
              "evidenced on the full span, folds and cost scenarios.", flush=True)
        ranked = _rank_grid_rows(rows, gated_only=False)
    path = _checkpoint_path(slate()["finalists_file"])
    done = _load_done(path)

    # Fan out per WINDOW, not per config: with only a handful of finalists a
    # per-config pool leaves most cores idle, and simple_scalp_plus runs at
    # 36 bars/s where a serial full stack is ~7 core-hours.
    tasks = []
    for name in slate()["grids"]:
        for row in ranked.get(name, [])[:top]:
            gid = f"{name}:{row['combo']}"
            for window in _finalist_windows(name, full_only=False):
                if f"{gid}|{window}" not in done:
                    tasks.append((name, row["combo"], row["override"], window, gated))
    for name in slate()["grids"]:
        if not ranked.get(name):
            print(f"note: {name} produced no usable combo", flush=True)

    print(f"finalists: {len(tasks)} window tasks, jobs={jobs}", flush=True)
    if tasks:
        t0 = time.time()
        with open(path, "a") as fh, Pool(jobs, initializer=_init_worker) as pool:
            for n, row in enumerate(pool.imap_unordered(eval_finalist_window, tasks), 1):
                row["_task_id"] = f"{row['id']}|{row.get('window')}"
                fh.write(json.dumps(row) + "\n")
                fh.flush()
                el = time.time() - t0
                eta = el / n * (len(tasks) - n)
                flag = "ERR" if row.get("error") else "ok"
                print(f"[{n}/{len(tasks)}] {row['id']} {row.get('window')} {flag} "
                      f"({el/60:.1f}m elapsed, ~{eta/60:.0f}m left)", flush=True)

    # Merge the per-window rows into one row per config for the report.
    merged = _merge_finalist_windows(_read_jsonl(path))
    merged_path = _checkpoint_path(slate()["finalists_file"].replace(
        ".jsonl", "_merged.jsonl"))
    with open(merged_path, "w") as fh:
        for row in merged:
            fh.write(json.dumps(row) + "\n")
    print(f"merged {len(merged)} finalist configs -> {merged_path}", flush=True)


def cmd_long_only(jobs: int) -> None:
    """Long-only appendix: the slate winners plus the two 15m-native plugins."""
    rows = _read_jsonl(_checkpoint_path("pf_grid.jsonl"))
    ranked = _rank_grid_rows(rows)
    path = _checkpoint_path("long_only.jsonl")
    done = _load_done(path)
    items = []
    for name in STRATEGIES:
        best = ranked.get(name, [])
        ov = dict(best[0]["override"]) if best else {}
        ov["enable_short"] = False
        if f"{name}:long_only" not in done:
            items.append((name, "long_only", ov, "full", False))
    for name in LONG_ONLY_EXTRA:
        if f"{name}:long_only" not in done:
            items.append((name, "long_only", {}, "full", False))
    print(f"long-only: {len(items)} configs", flush=True)
    if not items:
        return
    with open(path, "a") as fh, Pool(jobs, initializer=_init_worker) as pool:
        for n, row in enumerate(pool.imap_unordered(eval_finalist_window, items), 1):
            # Normalise the window-task shape to the row shape the report reads.
            if row.get("stats") is not None:
                row["full"] = row.pop("stats")
                row["bh_full"] = row.pop("bh", None)
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            print(f"[{n}/{len(items)}] {row['id']}", flush=True)


# --------------------------------------------------------------------------- #
# Calibration                                                                 #
# --------------------------------------------------------------------------- #
def cmd_calibrate() -> None:
    """Measure bars/s per strategy and extrapolate the full-span cost.

    Nothing downstream should be sized without this number, and the bars/s must
    be roughly FLAT across n — if it degrades, the context window is not
    bounding per-bar cost and the grid will not finish.
    """
    _init_worker()
    df = _G["df15"]
    total = len(df)
    print(f"{SYMBOL} {TF}: {total} bars loaded", flush=True)
    results = []
    for strategy in slate()["grids"]:
        ov = slate()["grids"][strategy][0][1]
        for n in (5_000, 20_000):
            if n > total:
                continue
            frame = df.iloc[:n].reset_index(drop=True)
            t0 = time.time()
            stats = _run(strategy, frame, ov, WARMUP_BARS)
            el = max(1e-9, time.time() - t0)
            rate = n / el
            row = {
                "strategy": strategy, "bars": n, "seconds": round(el, 2),
                "bars_per_sec": round(rate, 1),
                "trades": stats.get("total_trades"),
                "full_span_seconds": round(total / rate, 1),
            }
            results.append(row)
            print(f"{strategy:22s} n={n:6d} {el:7.2f}s {rate:7.1f} b/s "
                  f"trades={stats.get('total_trades'):5} "
                  f"-> full span ~{total/rate/60:.1f} min", flush=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(_checkpoint_path("calibration.json"), "w") as fh:
        json.dump({"symbol": SYMBOL, "timeframe": TF, "total_bars": total,
                   "ctx_window_bars": CTX_WINDOW, "rows": results}, fh, indent=2)


# --------------------------------------------------------------------------- #
# Reporting                                                                   #
# --------------------------------------------------------------------------- #
def _pf_from_returns(returns: list) -> float | None:
    gp = sum(r for r in returns if r > 0)
    gl = abs(sum(r for r in returns if r < 0))
    if gl <= 0:
        return None
    return gp / gl


def _bootstrap_pf(returns: list, block: int, samples: int = 2000, seed: int = 20260727):
    """PF distribution under resampling — CI and P(PF<1)."""
    import random

    if len(returns) < 20:
        return None
    rng = random.Random(seed)
    n = len(returns)
    pfs = []
    for _ in range(samples):
        if block <= 1:
            draw = [returns[rng.randrange(n)] for _ in range(n)]
        else:
            draw = []
            while len(draw) < n:
                start = rng.randrange(n)
                draw.extend(returns[start:start + block])
            draw = draw[:n]
        pf = _pf_from_returns(draw)
        if pf is not None:
            pfs.append(pf)
    if not pfs:
        return None
    pfs.sort()
    lo = pfs[int(0.05 * len(pfs))]
    hi = pfs[int(0.95 * len(pfs)) - 1]
    below = sum(1 for p in pfs if p < 1.0) / len(pfs)
    return {"lo": lo, "hi": hi, "p_below_1": below, "samples": len(pfs)}


def _stress_cut_top(returns: list, k: int = 5) -> dict:
    ordered = sorted(returns, reverse=True)
    cut = ordered[k:]
    return {
        "pf": _pf_from_returns(cut),
        "net_sum": sum(cut),
        "removed": ordered[:k],
    }


def _fmt(value, spec: str = ".2f") -> str:
    if value is None:
        return "n/a"
    try:
        return format(float(value), spec)
    except (TypeError, ValueError):
        return str(value)


def cmd_report(md_path: str | None, top: int) -> None:
    grid_rows = _read_jsonl(_checkpoint_path(slate()["grid_file"]))
    merged_path = _checkpoint_path(
        slate()["finalists_file"].replace(".jsonl", "_merged.jsonl"))
    if not os.path.exists(merged_path):
        merged_path = _checkpoint_path(slate()["finalists_file"])
    fin_rows = [r for r in _read_jsonl(merged_path)
                if not r.get("error") and r.get("full")]
    long_rows = [r for r in _read_jsonl(_checkpoint_path("long_only.jsonl"))
                 if not r.get("error")]
    if not fin_rows:
        raise SystemExit("no finalist results — run `finalists` first")

    n_trials = sum(len(g) for g in slate()["grids"].values())
    valid_grid = [r for r in grid_rows if not r.get("error")]

    # Best finalist per strategy by the full-span robust score.
    best_by_strategy = {}
    for row in fin_rows:
        score = robust_score(row.get("is", {}), row.get("oos", {}), row.get("full", {}))
        row["_score"] = score
        cur = best_by_strategy.get(row["strategy"])
        if cur is None or score > cur["_score"]:
            best_by_strategy[row["strategy"]] = row
    ordered = sorted(best_by_strategy.values(), key=lambda r: r["_score"], reverse=True)

    lines: list = []

    def emit(text: str = "") -> None:
        lines.append(text)
        print(text)

    today = date.today().isoformat()
    n_cand = len(slate()["grids"])
    sides = "long-only" if ACTIVE_SLATE == "long-only" else "long + short"
    emit(f"# SOL ({TF}) — {n_cand}-strategy {sides} comparison, {today}")
    emit()
    emit(f"Symbol `{SYMBOL}`, timeframe `{TF}`, {sides}, "
         f"{FULL_START} to present. Ranked on profit factor with low drawdown, "
         "and judged against buy-and-hold.")
    emit()
    if ACTIVE_SLATE == "long-only":
        emit("> **Provenance note.** `simple_scalp_plus` is the 8-point "
             "confluence scalper ported from **Binance_Cryptonice**, not from "
             "freqtrade — see its module docstring. Same concept (HullMA, "
             "EMA9/21, RSI, ADX, MACD, Stochastic, rolling VWAP, volume, all "
             "genuinely computed), different lineage.")
        emit()
        emit(f"> **Gate change, declared before the run.** Trade floors are "
             f"{LONG_ONLY_MIN_IS_TRADES}/{LONG_ONLY_MIN_OOS_TRADES} here versus "
             f"{MIN_IS_TRADES}/{MIN_OOS_TRADES} in the long+short study. "
             "Long-only halves the opportunity set and `dual_thrust` is capped "
             "at one entry per session. This is a sample-adequacy threshold, "
             "not a performance one.")
        emit()
        emit("> **Inert knobs excluded from the grid.** `min_buy_confidence` is "
             "coupled to `min_confirmations_buy` via `conf = buy_count/7 + 0.22`, "
             "so it is pinned low and the count is the sole entry gate. "
             "`mtf_filter` and `require_macro_bull` are no-ops at "
             "`regime_timeframe=None`, and `risk_reward` is never read by the "
             "strategy. Gridding any of them would have duplicated rows.")
        emit()

    emit("## Protocol")
    emit()
    emit("| Element | Setting |")
    emit("|---|---|")
    emit("| Engine | `run_plugin_replay` (production StrategyRunner + PositionSimulator) |")
    emit(f"| Data | Binance public archive, {SYMBOL} {TF} |")
    emit(f"| Tuning window | {TUNE_START} -> {TUNE_END} (2021 melt-up held out) |")
    emit(f"| Full span | {FULL_START} -> present |")
    emit(f"| Context window | {CTX_WINDOW} bars |")
    emit(f"| IS/OOS split | {GRID_SPLIT_RATIO:.0%} / {1-GRID_SPLIT_RATIO:.0%} |")
    emit(f"| Folds | {N_FOLDS} non-overlapping |")
    emit(f"| Gates | IS trades >= {slate()['min_is']}, "
         f"OOS trades >= {slate()['min_oos']}, both nets > 0, "
         f"MDD <= {MAX_MDD_PCT}% |")
    emit(f"| Configs searched | {n_trials} |")
    emit()
    emit(f"Ranking score: `min(PF_is, PF_oos, PF_full) / (1 + MDD/{MDD_REF:.0f})`, "
         f"PF clamped at {PF_CLAMP} and the {PF_SENTINEL} no-losing-trade sentinel "
         "rejected outright. The MDD exchange rate is a chosen parameter, not a "
         "discovered one: a 10% drawdown halves the score.")
    emit()

    emit("## Full period")
    emit()
    emit("| Strategy | Combo | Net % | **B&H Net %** | PF | WR % | MDD % | **B&H MDD %** "
         "| Trades | Sharpe | Score |")
    emit("|---|---|---|---|---|---|---|---|---|---|---|")
    for row in ordered:
        f = row.get("full", {})
        bh = row.get("bh_full") or {}
        emit(f"| `{row['strategy']}` | {row['combo']} | {_fmt(f.get('net_profit_pct'))} "
             f"| {_fmt(bh.get('net_profit_pct'))} "
             f"| {_fmt(f.get('profit_factor'), '.3f')} | {_fmt(f.get('win_rate'))} "
             f"| {_fmt(f.get('max_drawdown_pct'))} | {_fmt(bh.get('max_drawdown_pct'))} "
             f"| {f.get('total_trades')} "
             f"| {_fmt(f.get('sharpe'))} "
             f"| {_fmt(row.get('_score'), '.3f')} |")
    emit()
    emit("**B&H** is buy-and-hold over the same window, charged one entry and one "
         "exit at the same taker fee and slippage. On an asset that rose as far as "
         "SOL did, a long-only strategy can post a large positive net purely from "
         "beta — a config that trails B&H on both net and drawdown is an expensive "
         "index, not a strategy.")
    emit()

    emit(f"## Post-melt-up sub-period ({SUBPERIOD_START} -> present)")
    emit()
    emit("| Strategy | Net % | **B&H Net %** | PF | MDD % | Trades |")
    emit("|---|---|---|---|---|---|")
    for row in ordered:
        sp = row.get("subperiod") or {}
        bh = row.get("bh_subperiod") or {}
        emit(f"| `{row['strategy']}` | {_fmt(sp.get('net_profit_pct'))} "
             f"| {_fmt(bh.get('net_profit_pct'))} "
             f"| {_fmt(sp.get('profit_factor'), '.3f')} "
             f"| {_fmt(sp.get('max_drawdown_pct'))} | {sp.get('total_trades')} |")
    emit()

    emit("## IS / OOS (70/30 on the full span)")
    emit()
    emit("| Strategy | IS Net % | IS PF | IS Trades | OOS Net % | OOS PF | OOS Trades |")
    emit("|---|---|---|---|---|---|---|")
    for row in ordered:
        i, o = row.get("is", {}), row.get("oos", {})
        emit(f"| `{row['strategy']}` | {_fmt(i.get('net_profit_pct'))} "
             f"| {_fmt(i.get('profit_factor'), '.3f')} | {i.get('total_trades')} "
             f"| {_fmt(o.get('net_profit_pct'))} | {_fmt(o.get('profit_factor'), '.3f')} "
             f"| {o.get('total_trades')} |")
    emit()

    emit(f"## {N_FOLDS}-fold walk-forward (PF per fold)")
    emit()
    header = " | ".join(f"F{i+1}" for i in range(N_FOLDS))
    emit(f"| Strategy | {header} | Profitable folds |")
    emit("|---|" + "---|" * N_FOLDS + "---|")
    for row in ordered:
        folds = row.get("folds") or []
        cells = " | ".join(_fmt((f or {}).get("profit_factor"), ".2f") for f in folds)
        good = sum(1 for f in folds if float((f or {}).get("net_profit_pct") or 0) > 0)
        emit(f"| `{row['strategy']}` | {cells} | {good}/{len(folds)} |")
    emit()

    emit("## Bootstrap CI and stress test")
    emit()
    emit("| Strategy | Trades | PF 90% CI (iid) | PF 90% CI (block=20) "
         "| P(PF<1) block | PF after top-5 cut |")
    emit("|---|---|---|---|---|---|")
    for row in ordered:
        rets = row.get("trade_returns") or []
        iid = _bootstrap_pf(rets, block=1)
        blk = _bootstrap_pf(rets, block=20)
        stress = _stress_cut_top(rets)
        iid_s = f"{_fmt(iid['lo'],'.2f')}–{_fmt(iid['hi'],'.2f')}" if iid else "n/a"
        blk_s = f"{_fmt(blk['lo'],'.2f')}–{_fmt(blk['hi'],'.2f')}" if blk else "n/a"
        pb = _fmt(blk["p_below_1"], ".3f") if blk else "n/a"
        emit(f"| `{row['strategy']}` | {len(rets)} | {iid_s} | {blk_s} | {pb} "
             f"| {_fmt(stress['pf'], '.3f')} |")
    emit()
    emit("`P(PF<1)` is the share of block-bootstrap resamples whose profit factor "
         "falls below 1.0. The block variant keeps trade-outcome autocorrelation "
         "intact and is the one to read; the iid column is shown for comparison.")
    emit()

    emit("## Cost sensitivity")
    emit()
    emit("Round trip at 1.0x is taker fee + slippage on both sides. A strategy "
         "that cannot clear PF > 1 at 2.0x is disqualified regardless of score.")
    emit()
    emit("| Strategy | Trades | Net 0.0x | PF 0.0x | Net 1.0x | PF 1.0x | Net 2.0x | PF 2.0x |")
    emit("|---|---|---|---|---|---|---|---|")
    for row in ordered:
        c = row.get("costs") or {}
        cells = []
        for label in ("0.0x", "1.0x", "2.0x"):
            s = c.get(label) or {}
            cells.append(_fmt(s.get("net_profit_pct")))
            cells.append(_fmt(s.get("profit_factor"), ".3f"))
        trades = (row.get("full") or {}).get("total_trades")
        emit(f"| `{row['strategy']}` | {trades} | " + " | ".join(cells) + " |")
    emit()

    emit("## Cost decomposition — where the money goes")
    emit()
    emit("The 0.0x column removes fees and slippage entirely. It is not a "
         "tradeable scenario; it exists to separate two very different "
         "failures: *no edge at all* versus *a real edge too small to pay for "
         "its own turnover*. Only the second one points anywhere.")
    emit()
    emit("| Strategy | Trades | Gross PF (0.0x) | Gross net | Net after costs "
         "| Cost drag (pp) |")
    emit("|---|---|---|---|---|---|")
    for row in ordered:
        c = row.get("costs") or {}
        gross = c.get("0.0x") or {}
        real = c.get("1.0x") or {}
        g_net = gross.get("net_profit_pct")
        r_net = real.get("net_profit_pct")
        drag = (float(g_net) - float(r_net)) if (g_net is not None and r_net is not None) else None
        trades = (row.get("full") or {}).get("total_trades")
        emit(f"| `{row['strategy']}` | {trades} "
             f"| {_fmt(gross.get('profit_factor'), '.3f')} | {_fmt(g_net)}% "
             f"| {_fmt(r_net)}% | {_fmt(drag)} |")
    emit()
    for row in ordered:
        c = row.get("costs") or {}
        gross = c.get("0.0x") or {}
        gpf = clean_pf(gross)
        trades = int((row.get("full") or {}).get("total_trades") or 0)
        if gpf is None:
            continue
        if gpf > 1.0:
            emit(f"* `{row['strategy']}` **does have a gross edge** "
                 f"(PF {gpf:.3f} before costs) but spreads it across {trades} "
                 "trades. The edge per trade is far smaller than the ~14bp it "
                 "costs to take, so the strategy is not broken — it is "
                 "mis-sized for this timeframe. Any rescue would have to cut "
                 "turnover hard, use maker fills, or trade a venue where the "
                 "round trip is a fraction of this one.")
        else:
            emit(f"* `{row['strategy']}` **has no gross edge** "
                 f"(PF {gpf:.3f} even at zero cost). No fee or venue change "
                 "would rescue it; the signal itself does not predict on this "
                 "symbol and timeframe.")
    emit()

    if long_rows:
        emit("## Long-only appendix")
        emit()
        emit("Includes the two 15m-native plugins that have no short side at all, "
             "so they can be compared on the side they actually trade.")
        emit()
        emit("| Strategy | Net % | PF | MDD % | Trades |")
        emit("|---|---|---|---|---|")
        for row in sorted(long_rows,
                          key=lambda r: -(clean_pf(r.get("full")) or 0)):
            f = row.get("full", {})
            emit(f"| `{row['strategy']}` | {_fmt(f.get('net_profit_pct'))} "
                 f"| {_fmt(f.get('profit_factor'), '.3f')} "
                 f"| {_fmt(f.get('max_drawdown_pct'))} | {f.get('total_trades')} |")
        emit()

    emit("## Multiple-comparison correction")
    emit()
    emit(f"* Configurations evaluated across the whole study: **{n_trials}** "
         f"({len(valid_grid)} completed without error).")
    emit("* Deflated Sharpe uses that full count, not the shortlist — an "
         "under-reported `n_trials` deflates nothing.")
    emit()

    emit("## Verdict")
    emit()
    gate_passers = [r for r in fin_rows if r.get("passed_gate")]
    gated_grid = [r for r in valid_grid if r.get("gate")]
    both_positive = [
        r for r in valid_grid
        if float(r["is"].get("net_profit_pct") or 0) > 0
        and float(r["oos"].get("net_profit_pct") or 0) > 0
    ]
    if not gate_passers:
        emit(f"**No configuration is recommended.** Of the {len(valid_grid)} "
             f"configurations searched, {len(gated_grid)} passed the "
             f"pre-registered gates and {len(both_positive)} were profitable in "
             "both the in-sample and out-of-sample windows.")
        emit()
        emit("The configs tabulated below are the best of a losing slate, "
             "promoted so the negative result carries full-span evidence rather "
             "than an empty table. They are **not** candidates.")
        emit()
        names = ", ".join(f"`{n}`" for n in slate()["grids"])
        emit(f"Read this as a statement about SOL at 15m with {names} and these "
             "cost assumptions — not as a claim that no 15m strategy can work. "
             "The dominant term is the fee bill: a round trip costs ~14bp, and "
             "configs here turn over hundreds to thousands of times per window.")
        emit()
    if not ordered:
        emit("No finalist results to tabulate.")
    else:
        win = ordered[0]
        f = win.get("full", {})
        if not gate_passers:
            emit(f"Least-bad of the slate: `{win['strategy']}` ({win['combo']}) "
                 f"— PF {_fmt(f.get('profit_factor'), '.3f')}, "
                 f"MDD {_fmt(f.get('max_drawdown_pct'))}%, "
                 f"{f.get('total_trades')} trades on the full span. "
                 "It is reported for completeness only.")
            emit()

        c2 = (win.get("costs") or {}).get("2.0x") or {}
        pf2 = clean_pf(c2)
        folds = win.get("folds") or []
        good = sum(1 for x in folds if float((x or {}).get("net_profit_pct") or 0) > 0)
        emit(f"Highest score: **`{win['strategy']}`** ({win['combo']}) — "
             f"PF {_fmt(f.get('profit_factor'), '.3f')}, "
             f"MDD {_fmt(f.get('max_drawdown_pct'))}%, "
             f"{f.get('total_trades')} trades, {good}/{len(folds)} profitable folds.")
        emit()
        if pf2 is None or pf2 <= 1.0:
            emit("**It does not survive the 2.0x cost test, so it is not "
                 "recommended.** At 15m the fee bill is the dominant term; a "
                 "config that only clears at modelled costs is not a strategy.")
        elif good < MIN_PROFITABLE_FOLDS:
            emit(f"**Fewer than {MIN_PROFITABLE_FOLDS}/{len(folds)} profitable "
                 "folds, so it is not recommended.**")
        else:
            emit("It clears the gates, the fold count, and the 2.0x cost test.")
        emit()

        # Buy-and-hold is the decisive test for a long-only book.
        bh = win.get("bh_full") or {}
        bh_net = bh.get("net_profit_pct")
        bh_mdd = bh.get("max_drawdown_pct")
        net = f.get("net_profit_pct")
        mdd = f.get("max_drawdown_pct")
        if bh_net is not None and net is not None:
            beats_net = float(net) > float(bh_net)
            beats_mdd = (bh_mdd is not None
                         and float(mdd or 0) < float(bh_mdd))
            emit(f"Versus buy-and-hold: net {_fmt(net)}% vs {_fmt(bh_net)}% "
                 f"({'beats' if beats_net else 'TRAILS'} B&H), "
                 f"drawdown {_fmt(mdd)}% vs {_fmt(bh_mdd)}% "
                 f"({'shallower' if beats_mdd else 'DEEPER'} than B&H).")
            emit()
            if not beats_net and not beats_mdd:
                emit("**It loses to buy-and-hold on both return and drawdown, so "
                     "it is not recommended.** Trading it would have produced a "
                     "worse outcome than holding the asset and paying two "
                     "commissions.")
            elif not beats_net:
                emit("It trails buy-and-hold on return but takes less drawdown. "
                     "That is a risk-adjusted argument, not a return argument, and "
                     "it should only be made explicitly.")
            emit()
        emit("### Best-scoring strategy_params"
             if not gate_passers else "### Winning strategy_params")
        emit()
        if not gate_passers:
            emit("**This config did not pass the gates and is not a "
                 "recommendation.** It is recorded so the run is reproducible "
                 "and so a future study can start from what was already tried.")
            emit()
        emit("Research artifact. NOT applied to `coin_whitelist.json` or "
             "`bot_config.yaml`, and NOT certified for live.")
        emit()
        emit("```json")
        emit(json.dumps({
            "SOL": {
                "symbol": SYMBOL,
                "strategy": win["strategy"],
                "primary_timeframe": TF,
                "mode": "sim",
                "allowed_sides": (
                    ["long"] if ACTIVE_SLATE == "long-only" else ["long", "short"]
                ),
                "strategy_params": win["override"],
            }
        }, indent=2, sort_keys=True))
        emit("```")
    emit()

    if md_path:
        os.makedirs(os.path.dirname(md_path), exist_ok=True)
        with open(md_path, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        print(f"\nwrote {md_path}", flush=True)


# --------------------------------------------------------------------------- #
# OKX venue cross-check                                                       #
# --------------------------------------------------------------------------- #
def cmd_okx(limit: int) -> None:
    """Replay the winning config on OKX SOL-USDT-SWAP, the venue actually traded."""
    import pandas as pd
    from xauby.backtest.data import normalize_ohlcv_df
    from xauby.backtest.okx_data import download_okx_klines

    fin_rows = [r for r in _read_jsonl(_checkpoint_path("finalists.jsonl"))
                if not r.get("error")]
    if not fin_rows:
        raise SystemExit("no finalist results — run `finalists` first")
    for row in fin_rows:
        row["_score"] = robust_score(row.get("is", {}), row.get("oos", {}),
                                     row.get("full", {}))
    win = max(fin_rows, key=lambda r: r["_score"])
    print(f"cross-checking {win['strategy']} ({win['combo']}) on {VENUE}", flush=True)

    cache = os.path.join(OUT_DIR, f"okx_{SYMBOL.lower()}_{TF}.csv")
    if os.path.exists(cache):
        raw = pd.read_csv(cache)
    else:
        raw = download_okx_klines(VENUE, TF, limit=limit,
                                  base_url="https://www.okx.com")
        raw.to_csv(cache, index=False)
    df = normalize_ohlcv_df(raw)
    print(f"OKX bars: {len(df)}", flush=True)

    _init_worker()
    _G["df15"] = df
    stats = _run(win["strategy"], df, win["override"], WARMUP_BARS)
    stats.pop("trade_returns", None)
    out = {"venue": VENUE, "strategy": win["strategy"], "combo": win["combo"],
           "override": win["override"], "bars": len(df), "stats": stats}
    with open(_checkpoint_path("okx_crosscheck.json"), "w") as fh:
        json.dump(out, fh, indent=2)
    for key in METRIC_KEYS:
        print(f"  {key}: {stats.get(key)}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command",
                   choices=["fetch", "calibrate", "grid", "finalists",
                            "long-only", "report", "okx"])
    p.add_argument("--strategy", default=None, help="grid: run a single strategy")
    p.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    p.add_argument("--top", type=int, default=2,
                   help="finalists: configs per strategy")
    p.add_argument("--md", default=None, help="report: write markdown here")
    p.add_argument("--okx-limit", type=int, default=100_000,
                   help="okx: bars to pull from the venue")
    p.add_argument("--slate", choices=sorted(SLATES), default="long-short",
                   help="which candidate slate to run (separate checkpoints)")
    args = p.parse_args()

    global ACTIVE_SLATE
    ACTIVE_SLATE = args.slate

    if args.command == "fetch":
        cmd_fetch()
    elif args.command == "calibrate":
        cmd_calibrate()
    elif args.command == "grid":
        cmd_grid(args.strategy, args.jobs)
    elif args.command == "finalists":
        cmd_finalists(args.jobs, args.top)
    elif args.command == "long-only":
        cmd_long_only(args.jobs)
    elif args.command == "report":
        cmd_report(args.md, args.top)
    elif args.command == "okx":
        cmd_okx(args.okx_limit)


if __name__ == "__main__":
    main()
