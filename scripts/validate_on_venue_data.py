"""Re-validate the live pair configs on OKX venue data instead of a proxy.

Roadmap P0.2. Every certificate in docs/research/ was produced on Binance data —
XAU via the PAXGUSDT spot proxy, BTC via Binance klines — while the engine trades
OKX perpetual swaps. This script replays the *deployed* config against candles
pulled from OKX itself, so the numbers describe the venue we actually trade.

Three modes, because the two pairs are in very different situations:

  --pair btc     BTC-USDT-SWAP has ~6.6y of OKX history: a full re-validation,
                 directly comparable to the Binance-based study.

  --pair xau     XAU-USDT-SWAP only lists from 2025-04-09 (~1.3y). Too short for
                 a walk-forward, so this is an out-of-sample confirmation of the
                 PAXG-based study, not a replacement certificate.

  --pair proxy   The check nobody has run: replay the SAME config on real
                 XAU-USDT-SWAP and on a gold-token proxy over their OVERLAPPING
                 window. The 6-year XAU certificate rests entirely on the
                 assumption that a gold token stands in for the gold swap; this
                 measures the error in that assumption on data where both exist.
                 See validate_proxy() for why the proxy here is OKX XAUT-USDT
                 rather than the configured Binance PAXGUSDT.

Usage:
    python scripts/validate_on_venue_data.py --pair all
    python scripts/validate_on_venue_data.py --pair proxy --json-out out.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Dict, List, Optional

import pandas as pd

from xauby.backtest.data import (
    download_klines,
    normalize_ohlcv_df,
    prepare_raw_dataframe,
)
from xauby.backtest.okx_data import download_okx_klines
from xauby.backtest.service import (
    BacktestReplayBundle,
    _prepare_backtest_config,
    _resolve_fee_pct,
    resolve_strategy_name,
    run_replay_from_bundle,
)
from xauby.observability.replay_validation import load_bot_config

OKX_URL = "https://www.okx.com"

# Metrics reported for every run. Mirrors the certificate's table so a reader can
# put the two side by side without recomputing anything.
METRIC_KEYS = [
    ("trades", "total_trades"),
    ("win_rate", "win_rate"),
    ("profit_factor", "profit_factor"),
    ("net_profit_pct", "net_profit_pct"),
    ("max_drawdown_pct", "max_drawdown_pct"),
    ("cagr_pct", "cagr_pct"),
    ("calmar", "calmar"),
    ("sharpe", "sharpe"),
]


def _okx_frame(symbol: str, timeframe: str, *, limit: int = 20000) -> pd.DataFrame:
    """Deployed-config candles for `symbol` straight from OKX."""
    df = download_okx_klines(symbol, timeframe, limit=limit, base_url=OKX_URL)
    if df.empty:
        raise SystemExit(f"OKX returned no candles for {symbol} {timeframe}")
    return prepare_raw_dataframe(normalize_ohlcv_df(df))


def _bundle_for(
    symbol: str,
    df: pd.DataFrame,
    *,
    engine_config: Dict[str, Any],
    strategy_name: Optional[str] = None,
    label: str = "",
) -> BacktestReplayBundle:
    """Wrap a candle frame in a replay bundle using the DEPLOYED config.

    The config comes from _prepare_backtest_config, the same resolver live and
    replay use, so nothing here can quietly diverge from what the engine runs.
    D1 regime data is not attached: both live pairs have use_d1_regime_filter
    false, so a D1 frame would be unused — and silently enabling it is precisely
    the mismatch that made the July certificate unreproducible.
    """
    sym = symbol.upper().replace("_", "")
    strat_name = strategy_name or resolve_strategy_name(sym, engine_config, None)
    merged_cfg, strat_cfg, primary_tf, _regime_tf, _use_d1, config_used = (
        _prepare_backtest_config(sym, strat_name, engine_config, None)
    )
    return BacktestReplayBundle(
        symbol=sym,
        strategy_name=strat_name,
        merged_cfg=merged_cfg,
        strat_cfg=dict(strat_cfg),
        primary_tf=primary_tf,
        regime_tf=None,
        use_d1=False,
        fee_pct=_resolve_fee_pct(engine_config, merged_cfg.get("trading") or {}),
        config_used=dict(config_used),
        df=df,
        df_regime=None,
        period_start=str(df.iloc[0].get("timestamp", "")),
        period_end=str(df.iloc[-1].get("timestamp", "")),
        bars=len(df),
        regime_bars=0,
        data_symbol=label or sym,
        used_data_proxy=bool(label and label != sym),
    )


def _run(bundle: BacktestReplayBundle) -> Dict[str, Any]:
    result = run_replay_from_bundle(bundle)
    stats = dict(result.stats or {})
    row: Dict[str, Any] = {
        "bars": bundle.bars,
        "data": bundle.data_symbol,
        "strategy": bundle.strategy_name,
        "tf": bundle.primary_tf,
    }
    for out_key, stat_key in METRIC_KEYS:
        row[out_key] = stats.get(stat_key)
    if not bundle.df.empty and "open_time" in bundle.df.columns:
        row["start"] = str(
            pd.to_datetime(int(bundle.df["open_time"].iloc[0]), unit="ms").date()
        )
        row["end"] = str(
            pd.to_datetime(int(bundle.df["open_time"].iloc[-1]), unit="ms").date()
        )
    return row


def _clip_to_overlap(a: pd.DataFrame, b: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Restrict both frames to the timestamp range they share."""
    lo = max(int(a["open_time"].iloc[0]), int(b["open_time"].iloc[0]))
    hi = min(int(a["open_time"].iloc[-1]), int(b["open_time"].iloc[-1]))
    ca = a[(a["open_time"] >= lo) & (a["open_time"] <= hi)].reset_index(drop=True)
    cb = b[(b["open_time"] >= lo) & (b["open_time"] <= hi)].reset_index(drop=True)
    return ca, cb


def _fmt(rows: List[Dict[str, Any]]) -> str:
    cols = ["label", "data", "bars", "start", "end", "trades", "win_rate",
            "profit_factor", "net_profit_pct", "max_drawdown_pct", "cagr_pct",
            "calmar", "sharpe"]
    head = {"label": "run", "data": "source", "bars": "bars", "start": "from",
            "end": "to", "trades": "n", "win_rate": "WR%", "profit_factor": "PF",
            "net_profit_pct": "net%", "max_drawdown_pct": "MDD%",
            "cagr_pct": "CAGR%", "calmar": "Calmar", "sharpe": "Sharpe"}
    widths = {c: len(head[c]) for c in cols}
    body = []
    for r in rows:
        cells = {}
        for c in cols:
            v = r.get(c)
            if isinstance(v, float):
                v = f"{v:.2f}"
            cells[c] = "-" if v is None else str(v)
            widths[c] = max(widths[c], len(cells[c]))
        body.append(cells)
    lines = ["  ".join(head[c].ljust(widths[c]) for c in cols)]
    lines.append("  ".join("-" * widths[c] for c in cols))
    for cells in body:
        lines.append("  ".join(cells[c].ljust(widths[c]) for c in cols))
    return "\n".join(lines)


def validate_btc(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    df = _okx_frame("BTCUSDT", "4h")
    row = _run(_bundle_for("BTCUSDT", df, engine_config=cfg, label="BTC-USDT-SWAP"))
    row["label"] = "BTC okx-swap"
    return [row]


def validate_xau(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    df = _okx_frame("XAUUSDT", "4h")
    row = _run(_bundle_for("XAUUSDT", df, engine_config=cfg, label="XAU-USDT-SWAP"))
    row["label"] = "XAU okx-swap"
    return [row]


def validate_proxy(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Same config, same window: the real gold swap vs a gold-token proxy.

    Why the proxy is XAUT-USDT and not the configured PAXGUSDT:

    * `backtest.data_base_url` is OKX (the deployed value), and OKX does not list
      PAXG-USDT-SWAP, so resolving the XAU proxy through the normal loader raises
      OKX 51001. Before the P0.1 fix the same call failed silently into an empty
      frame and fell back to stale cache, which is why this never surfaced.
    * PAXG's deep history lives on Binance Global, which is geo-blocked from this
      environment — and note it answers a block with HTTP 200 and a JSON error
      body, so `download_klines` turns it into an empty frame rather than a
      failure. That is the same swallow, one layer up.
    * OKX PAXG-USDT spot exists but only goes back 0.78y — shallower than the swap
      it would be extending, so it is useless as a history extension.
    * OKX XAUT-USDT (Tether Gold) has ~4.0y, roughly 3x the swap's own history,
      on the venue we actually trade. It is the better proxy on both counts.

    So this measures the proxy assumption with the instrument that can actually
    serve it here. The 6-year PAXG figure in the certificate stays unverified from
    this environment; that limitation is reported, not papered over.
    """
    okx = _okx_frame("XAUUSDT", "4h")
    proxy_symbol = "XAUT-USDT"
    proxy_df = _okx_frame(proxy_symbol, "4h")

    okx_clip, proxy_clip = _clip_to_overlap(okx, proxy_df)
    if okx_clip.empty or proxy_clip.empty:
        raise SystemExit("no overlapping window between XAU-USDT-SWAP and the proxy")

    rows = []
    a = _run(_bundle_for("XAUUSDT", okx_clip, engine_config=cfg, label="XAU-USDT-SWAP"))
    a["label"] = "overlap: real swap"
    rows.append(a)
    b = _run(_bundle_for("XAUUSDT", proxy_clip, engine_config=cfg, label=proxy_symbol))
    b["label"] = "overlap: XAUT proxy"
    rows.append(b)
    # The proxy's full depth is the reason to use it at all: report what the
    # config scores over the whole 4y, beyond the swap's own listing window.
    c = _run(_bundle_for("XAUUSDT", proxy_df, engine_config=cfg, label=proxy_symbol))
    c["label"] = "XAUT full depth"
    rows.append(c)

    # Price-series correlation is the proxy assumption stated directly, and it is
    # independent of any strategy logic.
    merged = okx_clip[["open_time", "close"]].merge(
        proxy_clip[["open_time", "close"]], on="open_time", suffixes=("_okx", "_proxy")
    )
    if len(merged) > 2:
        ret_okx = merged["close_okx"].pct_change().dropna()
        ret_proxy = merged["close_proxy"].pct_change().dropna()
        rows.append({
            "label": "proxy fidelity",
            "data": f"{len(merged)} aligned bars",
            "profit_factor": round(float(merged["close_okx"].corr(merged["close_proxy"])), 4),
            "net_profit_pct": round(float(ret_okx.corr(ret_proxy)), 4),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pair", default="all", choices=["all", "btc", "xau", "proxy"])
    ap.add_argument("--config", default="bot_config.yaml")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    cfg = load_bot_config(args.config) if args.config else load_bot_config()

    rows: List[Dict[str, Any]] = []
    if args.pair in ("all", "btc"):
        rows += validate_btc(cfg)
    if args.pair in ("all", "xau"):
        rows += validate_xau(cfg)
    if args.pair in ("all", "proxy"):
        rows += validate_proxy(cfg)

    print(_fmt(rows))
    print()
    print("NOTE: 'proxy fidelity' row reports close-price correlation under PF and")
    print("      bar-return correlation under net% — not trading metrics.")
    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(rows, fh, indent=2, default=str)
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
