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

OKX_BASE_URL = "https://www.okx.com"
DEFAULT_INST_ID = "XAU-USDT-SWAP"


def okx_bar(timeframe: str) -> str:
    tf = str(timeframe or "4h").strip().lower()
    mapping = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "1H",
        "2h": "2H",
        "4h": "4H",
        "6h": "6H",
        "12h": "12H",
        "1d": "1D",
    }
    return mapping.get(tf, timeframe)


def normalize_symbol(inst_id: str) -> str:
    raw = str(inst_id or DEFAULT_INST_ID).upper()
    if raw.endswith("-SWAP"):
        raw = raw[:-5]
    return raw.replace("-", "").replace("/", "").replace("_", "")


def candle_cache_path(inst_id: str, timeframe: str, *, cache_dir: str = CACHE_DIR) -> str:
    sym = normalize_symbol(inst_id).lower()
    tf = str(timeframe or "4h").lower().replace("_", "")
    return os.path.join(cache_dir, f"backtest_candles_okx_{tf}_{sym}.csv")


def funding_cache_path(inst_id: str, *, cache_dir: str = CACHE_DIR) -> str:
    sym = normalize_symbol(inst_id).lower()
    return os.path.join(cache_dir, f"funding_okx_{sym}.csv")


def _get_json(path: str, params: Dict[str, Any], *, base_url: str = OKX_BASE_URL) -> Dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    headers = {"User-Agent": "xauby-okx-validation/1.0"}
    last_error: Optional[Exception] = None
    for attempt in range(5):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=20)
            if response.status_code in (418, 429) or response.status_code >= 500:
                time.sleep(2.0**attempt)
                continue
            response.raise_for_status()
            payload = response.json()
            if str(payload.get("code", "0")) != "0":
                raise RuntimeError(f"OKX error {payload.get('code')}: {payload.get('msg')}")
            return payload
        except Exception as exc:  # pragma: no cover - retry branch is timing-dependent
            last_error = exc
            time.sleep(2.0**attempt)
    raise RuntimeError(f"OKX request failed: {last_error}")


def fetch_candles(
    inst_id: str = DEFAULT_INST_ID,
    timeframe: str = "4h",
    *,
    limit: int = 300,
    pages: int = 1,
    base_url: str = OKX_BASE_URL,
) -> List[List[str]]:
    """Fetch OKX historical candles, oldest first."""
    rows: List[List[str]] = []
    cursor: Optional[str] = None
    remaining = max(1, int(pages))
    while remaining > 0 and len(rows) < limit:
        batch_limit = min(300, max(1, limit - len(rows)))
        params: Dict[str, Any] = {
            "instId": inst_id,
            "bar": okx_bar(timeframe),
            "limit": batch_limit,
        }
        if cursor:
            params["after"] = cursor
        payload = _get_json("/api/v5/market/history-candles", params, base_url=base_url)
        data = payload.get("data") or []
        if not data:
            break
        rows.extend(data)
        oldest_ts = str(data[-1][0])
        if cursor == oldest_ts:
            break
        cursor = oldest_ts
        remaining -= 1
        time.sleep(0.12)
    dedup = {int(row[0]): row for row in rows}
    return [dedup[key] for key in sorted(dedup)]


def candles_to_cache_df(rows: Iterable[List[str]], timeframe: str) -> pd.DataFrame:
    parsed = []
    for row in rows:
        if len(row) < 8:
            continue
        confirm = str(row[8]) if len(row) > 8 else "1"
        if confirm == "0":
            continue
        open_time = int(row[0])
        parsed.append(
            {
                "open_time": open_time,
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[6] or 0.0),
                "close_time": open_time + _timeframe_ms(timeframe) - 1,
                "quote_volume": float(row[7] or 0.0),
                "count": 0,
                "taker_buy_base": 0.0,
                "taker_buy_quote": 0.0,
                "ignore": 0,
                "timestamp": open_time // 1000,
            }
        )
    df = pd.DataFrame(parsed)
    if df.empty:
        return df
    return df.drop_duplicates(subset="open_time").sort_values("open_time").reset_index(drop=True)


def _timeframe_ms(timeframe: str) -> int:
    tf = str(timeframe or "4h").lower()
    unit = tf[-1]
    value = int(tf[:-1] or "1")
    if unit == "m":
        return value * 60_000
    if unit == "h":
        return value * 3_600_000
    if unit == "d":
        return value * 86_400_000
    raise ValueError(f"Unsupported timeframe: {timeframe}")


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
