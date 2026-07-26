"""OKX candle fetching for the backtest data layer.

Extracted from ``scripts/fetch_okx_xau_history.py`` so ``xauby.backtest.data``
can serve the venue the engine actually trades. The script still owns the CLI and
the funding-history side and re-exports these helpers, so the dependency runs
``scripts -> xauby`` and not the other way round.

Why this exists at all: ``download_klines`` only ever spoke Binance, while
``bot_config.yaml`` has pointed ``backtest.data_source`` at OKX since the swap
migration. The mismatch produced a request to ``<okx>/api/v1/klines`` that failed
into an empty DataFrame with no error, so every backtest silently ran on whatever
happened to be cached — Binance spot. See docs/roadmap_2026H2.md P0.1.

Output frames use the same 13 Binance-shaped columns the rest of the backtest
layer expects, which is the normalization target documented in
``xauby/api/interface.py``.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

OKX_BASE_URL = "https://www.okx.com"

# OKX uses capitalised units for >= 1h bars ("4H", "1D"), lower case below.
_BAR_MAP = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1H",
    "2h": "2H",
    "4h": "4H",
    "6h": "6H",
    "12h": "12H",
    "1d": "1D",
    "1w": "1W",
}

# OKX caps history-candles at 100 rows per call (market-candles allows 300, but
# only covers recent history), so paginate in 100s when walking back years.
_MAX_PAGE = 100


def okx_bar(timeframe: str) -> str:
    """Map an xAuby timeframe to an OKX ``bar`` value."""
    tf = str(timeframe or "4h").strip().lower()
    return _BAR_MAP.get(tf, timeframe)


def normalize_symbol(inst_id: str) -> str:
    """``XAU-USDT-SWAP`` -> ``XAUUSDT`` for cache filenames and symbol lookups."""
    raw = str(inst_id or "").upper()
    if raw.endswith("-SWAP"):
        raw = raw[:-5]
    return raw.replace("-", "").replace("/", "").replace("_", "")


def to_inst_id(symbol: str, *, market_type: str = "swap") -> str:
    """``XAUUSDT`` -> ``XAU-USDT-SWAP``.

    Passes through anything already hyphenated so callers can hand over an
    explicit instId. Only USDT-quoted symbols are derivable; anything else is
    rejected rather than guessed, because a wrong instId returns an empty result
    that would look exactly like "no history".
    """
    raw = str(symbol or "").upper().replace("_", "").replace("/", "")
    if "-" in raw:
        return raw
    if not raw.endswith("USDT"):
        raise ValueError(
            f"cannot derive an OKX instId from {symbol!r}: only USDT-quoted "
            "symbols are supported — pass an explicit instId like XAU-USDT-SWAP"
        )
    base = raw[: -len("USDT")]
    if not base:
        raise ValueError(f"cannot derive an OKX instId from {symbol!r}")
    suffix = "-SWAP" if str(market_type or "swap").lower() == "swap" else ""
    return f"{base}-USDT{suffix}"


def timeframe_ms(timeframe: str) -> int:
    """Timeframe length in milliseconds."""
    tf = str(timeframe or "4h").lower()
    unit = tf[-1]
    value = int(tf[:-1] or "1")
    if unit == "m":
        return value * 60_000
    if unit == "h":
        return value * 3_600_000
    if unit == "d":
        return value * 86_400_000
    if unit == "w":
        return value * 604_800_000
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def get_json(
    path: str,
    params: Dict[str, Any],
    *,
    base_url: str = OKX_BASE_URL,
    attempts: int = 5,
) -> Dict[str, Any]:
    """GET an OKX v5 endpoint with backoff, raising on a non-zero API code."""
    url = f"{str(base_url).rstrip('/')}{path}"
    headers = {"User-Agent": "xauby-okx-backtest/1.0"}
    last_error: Optional[Exception] = None
    for attempt in range(max(1, int(attempts))):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=20)
            if response.status_code in (418, 429) or response.status_code >= 500:
                time.sleep(2.0**attempt)
                continue
            response.raise_for_status()
            payload = response.json()
            if str(payload.get("code", "0")) != "0":
                raise RuntimeError(
                    f"OKX error {payload.get('code')}: {payload.get('msg')}"
                )
            return payload
        except Exception as exc:  # retry branch is timing-dependent
            last_error = exc
            time.sleep(2.0**attempt)
    raise RuntimeError(f"OKX request failed for {url}: {last_error}")


def fetch_candles(
    inst_id: str,
    timeframe: str = "4h",
    *,
    limit: int = 300,
    pages: Optional[int] = None,
    base_url: str = OKX_BASE_URL,
) -> List[List[str]]:
    """Fetch OKX historical candles, oldest first, de-duplicated.

    Walks backwards with the ``after`` cursor until ``limit`` rows are collected
    or the venue stops returning data. ``pages`` caps the number of requests;
    when omitted it is derived from ``limit`` so a caller asking for N bars
    actually gets N bars instead of one page.
    """
    wanted = max(1, int(limit))
    max_pages = int(pages) if pages is not None else (wanted // _MAX_PAGE) + 2
    rows: List[List[str]] = []
    cursor: Optional[str] = None
    for _ in range(max(1, max_pages)):
        if len(rows) >= wanted:
            break
        params: Dict[str, Any] = {
            "instId": inst_id,
            "bar": okx_bar(timeframe),
            "limit": min(_MAX_PAGE, wanted - len(rows)) or _MAX_PAGE,
        }
        if cursor:
            params["after"] = cursor
        payload = get_json("/api/v5/market/history-candles", params, base_url=base_url)
        data = payload.get("data") or []
        if not data:
            break
        rows.extend(data)
        oldest_ts = str(data[-1][0])
        if cursor == oldest_ts:
            break
        cursor = oldest_ts
        time.sleep(0.12)
    dedup = {int(row[0]): row for row in rows}
    return [dedup[key] for key in sorted(dedup)]


def candles_to_cache_df(rows: Iterable[List[str]], timeframe: str) -> pd.DataFrame:
    """Convert OKX candle rows into the Binance-shaped frame the layer expects.

    Unconfirmed candles (``confirm == "0"``) are skipped here as well as by
    ``drop_forming_bar`` downstream — OKX tells us directly, so there is no
    reason to rely on clock arithmetic for the bar it already flagged.
    """
    span = timeframe_ms(timeframe)
    parsed = []
    for row in rows:
        if len(row) < 8:
            continue
        if len(row) > 8 and str(row[8]) == "0":
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
                "close_time": open_time + span - 1,
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
    return (
        df.drop_duplicates(subset="open_time")
        .sort_values("open_time")
        .reset_index(drop=True)
    )


def download_okx_klines(
    symbol: str,
    timeframe: str,
    *,
    limit: int = 300,
    base_url: str = OKX_BASE_URL,
    market_type: str = "swap",
) -> pd.DataFrame:
    """OKX counterpart to ``download_klines``: symbol in, OHLCV frame out."""
    inst_id = to_inst_id(symbol, market_type=market_type)
    rows = fetch_candles(inst_id, timeframe, limit=limit, base_url=base_url)
    df = candles_to_cache_df(rows, timeframe)
    if df.empty:
        logger.warning(
            "OKX returned no candles for %s (%s %s)", inst_id, symbol, timeframe
        )
    return df
