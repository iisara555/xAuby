"""Fetch OKX XAU-USDT-SWAP candles/funding into local validation caches."""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from xauby.backtest.constants import CACHE_DIR

# Candle fetching lives in xauby.backtest.okx_data so xauby.backtest.data can
# use it too; this module keeps the CLI, the caches, and funding history.
# Re-exported for back-compat with scripts/evaluate_okx_xau_migration.py and
# tests/test_okx_xau_migration_tools.py.
from xauby.backtest.okx_data import (  # noqa: E402
    OKX_BASE_URL,
    candles_to_cache_df,
    fetch_candles,
    normalize_symbol,
    okx_bar,
)
from xauby.backtest.okx_data import get_json as _get_json  # noqa: E402
from xauby.backtest.okx_data import timeframe_ms as _timeframe_ms  # noqa: E402

DEFAULT_INST_ID = "XAU-USDT-SWAP"


def candle_cache_path(inst_id: str, timeframe: str, *, cache_dir: str = CACHE_DIR) -> str:
    sym = normalize_symbol(inst_id).lower()
    tf = str(timeframe or "4h").lower().replace("_", "")
    return os.path.join(cache_dir, f"backtest_candles_okx_{tf}_{sym}.csv")


def funding_cache_path(inst_id: str, *, cache_dir: str = CACHE_DIR) -> str:
    sym = normalize_symbol(inst_id).lower()
    return os.path.join(cache_dir, f"funding_okx_{sym}.csv")

def fetch_funding_history(
    inst_id: str = DEFAULT_INST_ID,
    *,
    limit: int = 100,
    base_url: str = OKX_BASE_URL,
) -> pd.DataFrame:
    payload = _get_json(
        "/api/v5/public/funding-rate-history",
        {"instId": inst_id, "limit": min(100, max(1, int(limit)))},
        base_url=base_url,
    )
    rows = []
    for row in payload.get("data") or []:
        ts = int(row.get("fundingTime") or 0)
        rows.append(
            {
                "funding_time": ts,
                "timestamp": ts // 1000,
                "funding_rate": float(row.get("fundingRate") or row.get("realizedRate") or 0.0),
                "inst_id": row.get("instId") or inst_id,
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.drop_duplicates(subset="funding_time").sort_values("funding_time").reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch OKX XAU swap validation data")
    parser.add_argument("--inst-id", default=DEFAULT_INST_ID)
    parser.add_argument("--symbol", default="", help="Alias for --inst-id, e.g. XAU-USDT-SWAP")
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--limit", type=int, default=900)
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--cache-dir", default=CACHE_DIR)
    parser.add_argument("--base-url", default=OKX_BASE_URL)
    parser.add_argument("--skip-funding", action="store_true")
    args = parser.parse_args()

    inst_id = args.symbol or args.inst_id
    rows = fetch_candles(
        inst_id,
        args.timeframe,
        limit=args.limit,
        pages=args.pages,
        base_url=args.base_url,
    )
    df = candles_to_cache_df(rows, args.timeframe)
    if df.empty:
        print("No OKX candle data returned")
        return 1
    os.makedirs(args.cache_dir, exist_ok=True)
    out = candle_cache_path(inst_id, args.timeframe, cache_dir=args.cache_dir)
    df.to_csv(out, index=False)
    first = pd.to_datetime(int(df["open_time"].iloc[0]), unit="ms")
    last = pd.to_datetime(int(df["open_time"].iloc[-1]), unit="ms")
    print(f"Wrote {len(df)} closed bars to {out}")
    print(f"Range: {first} -> {last}")

    if not args.skip_funding:
        funding = fetch_funding_history(inst_id, base_url=args.base_url)
        if not funding.empty:
            f_out = funding_cache_path(inst_id, cache_dir=args.cache_dir)
            funding.to_csv(f_out, index=False)
            avg = funding["funding_rate"].mean()
            print(f"Wrote {len(funding)} funding rows to {f_out} (avg={avg:.8f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
