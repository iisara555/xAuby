#!/usr/bin/env python3
"""Venue-specific long-only grids for Binance TH BTCUSDT and XAUTUSDT.

This is a research-only harness. It writes JSON under ``core/`` by default and
never edits a preset, tenant configuration, certificate, or live process.
XAUT selection uses Binance Global PAXGUSDT; Binance TH XAUTUSDT is only a
native cross-check and cannot produce a venue certificate before 12 months.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Mapping

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from scripts import btc_supertrend_okx_pf_grid as btc
from scripts import xau_okx_pf_grid as xau
from xauby.backtest.data import load_or_download_candles, normalize_ohlcv_df, prepare_raw_dataframe
from xauby.observability.replay_validation import load_bot_config

TH = {"base_url": "https://api.binance.th", "fee": 0.001}
GLOBAL = {"base_url": "https://api.binance.com", "fee": 0.001}


def frame(symbol: str, timeframe: str, source: Mapping[str, Any]) -> pd.DataFrame:
    data = load_or_download_candles(symbol, timeframe, limit=20000, base_url=str(source["base_url"]))
    if data.empty:
        raise SystemExit(f"no candles: {symbol} {timeframe} from {source['base_url']}")
    return prepare_raw_dataframe(normalize_ohlcv_df(data))


def costs(cfg: Dict[str, Any], fee: float) -> Dict[str, Any]:
    result = dict(cfg)
    result["exchange"] = {**dict(result.get("exchange") or {}), "fee_pct": fee, "market_type": "spot", "quote_asset": "USDT"}
    result["derivatives"] = {**dict(result.get("derivatives") or {}), "market_type": "spot"}
    return result


def positive(metrics: Mapping[str, Any], minimum: int) -> bool:
    return float(metrics.get("net_profit_pct") or 0) > 0 and int(metrics.get("total_trades") or 0) >= minimum


def rank(row: Mapping[str, Any]) -> tuple[float, float, float]:
    is_pf = float((row.get("is") or {}).get("profit_factor") or 0)
    oos_pf = float((row.get("oos") or {}).get("profit_factor") or 0)
    oos_net = float((row.get("oos") or {}).get("net_profit_pct") or 0)
    return min(is_pf, oos_pf), oos_pf, oos_net


def folds(module: Any, item: Mapping[str, Any], primary: pd.DataFrame, daily: pd.DataFrame, label: str) -> list[Dict[str, Any]]:
    rows = []
    size = len(primary) // 5
    for index in range(5):
        lo = index * size
        hi = len(primary) if index == 4 else (index + 1) * size
        warm = max(0, lo - 300)
        metrics = module._run_frame(
            primary.iloc[warm:hi].reset_index(drop=True), daily,
            item["override"], **({"label": label} if module is xau else {}),
            skip_bars=lo - warm,
        )
        rows.append({"fold": index + 1, **metrics})
    return rows


def run_btc(cfg: Dict[str, Any]) -> Dict[str, Any]:
    primary = frame("BTCUSDT", "1h", TH)
    daily = frame("BTCUSDT", "1d", TH)
    cfg = costs(cfg, TH["fee"])
    btc.SYMBOL, btc.VENUE = "BTCUSDT", "Binance TH BTCUSDT"
    prep = btc.prepare(cfg)
    split = int(len(primary) * 0.70)
    warm = max(0, split - 300)
    btc._G.clear()
    btc._G.update(cfg=cfg, prep=prep, native4=primary, native1=daily, split=split,
                  is_df=primary.iloc[:split].reset_index(drop=True),
                  oos_df=primary.iloc[warm:].reset_index(drop=True), oos_skip=split - warm)
    items = [item for item in btc.grid_items() if item["variant"].startswith("long-only")]
    rows = [btc._eval_grid(item) for item in items]
    eligible = [row for row in rows if "error" not in row and positive(row["is"], 30) and positive(row["oos"], 10)]
    winner = max(eligible, key=rank) if eligible else None
    if winner:
        winner = {**winner, "folds": folds(btc, winner, primary, daily, btc.VENUE)}
    return {"pair": "BTCUSDT", "venue": "binance_th", "selection_source": "native", "trials": len(items), "eligible": len(eligible), "winner": winner, "rows": rows}


def run_xaut(cfg: Dict[str, Any]) -> Dict[str, Any]:
    proxy = frame("PAXGUSDT", "4h", GLOBAL)
    proxy_daily = frame("PAXGUSDT", "1d", GLOBAL)
    native = frame("XAUTUSDT", "4h", TH)
    native_daily = frame("XAUTUSDT", "1d", TH)
    cfg = costs(cfg, GLOBAL["fee"])
    prep = xau.prepare(cfg)
    split = int(len(proxy) * 0.70)
    warm = max(0, split - 300)
    xau._G.clear()
    xau._G.update(cfg=cfg, prep=prep, proxy4=proxy, proxy1=proxy_daily,
                  native4=native, native1=native_daily, split=split,
                  is_df=proxy.iloc[:split].reset_index(drop=True),
                  oos_df=proxy.iloc[warm:].reset_index(drop=True), oos_skip=split - warm)
    items = [item for item in xau.grid_items() if item["variant"].startswith("long-only")]
    rows = [xau._eval_grid(item) for item in items]
    eligible = [row for row in rows if "error" not in row and positive(row["is"], 40) and positive(row["oos"], 18)]
    winner = max(eligible, key=rank) if eligible else None
    if winner:
        winner = {**winner, "folds": folds(xau, winner, proxy, proxy_daily, "Binance Global PAXGUSDT")}
        winner["native_crosscheck"] = xau._run_frame(native, native_daily, winner["override"], label="Binance TH XAUTUSDT", skip_bars=0)
    return {"pair": "XAUTUSDT", "venue": "binance_th", "selection_source": "proxy: binance_global PAXGUSDT", "certification_eligible": False, "reason": "proxy selection cannot certify Binance TH; native history must reach 12 months", "trials": len(items), "eligible": len(eligible), "winner": winner, "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", choices=("btc", "xaut", "all"), default="all")
    parser.add_argument("--config", default="bot_config.yaml")
    parser.add_argument("--out", default="core/binance_th_spot_grid.json")
    args = parser.parse_args()
    cfg = load_bot_config(args.config)
    result = {}
    if args.pair in ("btc", "all"):
        result["btc"] = run_btc(cfg)
    if args.pair in ("xaut", "all"):
        result["xaut"] = run_xaut(cfg)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
