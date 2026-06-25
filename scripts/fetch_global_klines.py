"""Fetch multi-year klines from Binance GLOBAL public API into the backtest CSV cache format.

Binance.th only serves ~6 months of history; strategy validation needs full
bull/bear/chop cycles. This writes a SEPARATE ``*_full.csv`` next to the
normal cache so the TUI backtest and force_download never touch it.

Usage:
    python scripts/fetch_global_klines.py --symbol BTCUSDT --timeframe 1h --years 3
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

GLOBAL_KLINES_URL = "https://api.binance.com/api/v3/klines"
COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "count", "taker_buy_base",
    "taker_buy_quote", "ignore",
]


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    """Paginate forward from start_ms; returns raw kline rows (oldest first)."""
    out: list = []
    cursor = start_ms
    while cursor < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": cursor,
            "limit": 1000,
        }
        for attempt in range(5):
            try:
                r = requests.get(GLOBAL_KLINES_URL, params=params, timeout=15)
                if r.status_code in (418, 429) or r.status_code >= 500:
                    wait = 2.0 ** attempt
                    print(f"  HTTP {r.status_code}; retrying in {wait:.0f}s...")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                data = r.json()
                break
            except requests.RequestException as e:
                wait = 2.0 ** attempt
                print(f"  request failed ({e}); retrying in {wait:.0f}s...")
                time.sleep(wait)
        else:
            raise RuntimeError("Binance global API kept failing; aborting")

        if not data:
            break
        out.extend(data)
        cursor = int(data[-1][0]) + 1
        print(f"  fetched {len(out)} bars (up to {pd.to_datetime(int(data[-1][0]), unit='ms')})")
        if len(data) < 1000:
            break
        time.sleep(0.3)
    return out


def to_cache_df(klines: list) -> pd.DataFrame:
    df = pd.DataFrame(klines, columns=COLUMNS)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    df["open_time"] = df["open_time"].astype("int64")
    df = df.drop_duplicates(subset="open_time").sort_values("open_time").reset_index(drop=True)
    df["timestamp"] = df["open_time"] // 1000
    return df


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch multi-year Binance global klines for backtesting")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--years", type=float, default=3.0)
    parser.add_argument("--out", default=None, help="Output CSV (default core/backtest_candles_{tf}_{symbol}_full.csv)")
    args = parser.parse_args()

    sym = args.symbol.upper().replace("_", "")
    tf = args.timeframe.lower()
    out_path = args.out or os.path.join(
        ROOT, "core", f"backtest_candles_{tf}_{sym.lower()}_full.csv"
    )

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - int(args.years * 365.25 * 86400 * 1000)
    print(f"Fetching {sym} {tf} from {pd.to_datetime(start_ms, unit='ms')} to now (Binance global)...")
    klines = fetch_klines(sym, tf, start_ms, now_ms)
    if not klines:
        print("No data returned")
        return 1

    df = to_cache_df(klines)
    # Drop the still-forming final candle so every row is a closed bar.
    df = df.iloc[:-1]
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.to_csv(out_path, index=False)
    first = pd.to_datetime(int(df["open_time"].iloc[0]), unit="ms")
    last = pd.to_datetime(int(df["open_time"].iloc[-1]), unit="ms")
    print(f"Wrote {len(df)} closed bars to {out_path}")
    print(f"Range: {first} -> {last}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
