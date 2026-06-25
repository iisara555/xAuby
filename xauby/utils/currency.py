"""USDT/THB exchange-rate helper with disk-backed in-memory cache.

The live Binance TH USDT/THB market is preferred.  Public USD/THB FX feeds
are used only when that market is unavailable.  We never invent a rate: on a
network failure the last observed rate is used, or conversion is unavailable.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger("xauby.utils.currency")

from xauby.runtime.paths import usd_thb_rate_path
_CACHE_FILE = usd_thb_rate_path()
_TTL = 600          # refresh every 10 minutes

_rate_mem: Optional[float] = None
_rate_ts: float = 0.0


class CurrencyRateUnavailable(RuntimeError):
    """Raised when no observed USDT/THB or USD/THB rate is available."""


def _sources():
    """Return public price sources ordered by conversion accuracy."""
    return (
        (
            "https://api.binance.th/api/v1/ticker/24hr?symbol=USDTTHB",
            lambda d: float(d.get("lastPrice", 0) or d.get("price", 0)),
        ),
        (
            "https://api.frankfurter.app/latest?from=USD&to=THB",
            lambda d: float(d["rates"]["THB"]),
        ),
        (
            "https://open.er-api.com/v6/latest/USD",
            lambda d: float(d["rates"]["THB"]),
        ),
    )


def get_usd_thb_rate() -> float:
    """Return USD→THB rate, refreshing at most once per _TTL seconds."""
    global _rate_mem, _rate_ts
    now = time.time()
    if _rate_mem is not None and (now - _rate_ts) < _TTL:
        return _rate_mem

    # Disk cache is good enough?
    try:
        if os.path.exists(_CACHE_FILE):
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            cached_rate = float(d.get("rate", 0))
            cached_ts = float(d.get("ts", 0))
            if cached_rate > 0 and (now - cached_ts) < _TTL:
                _rate_mem = cached_rate
                _rate_ts = cached_ts
                return _rate_mem
    except Exception:
        pass

    # Fetch a fresh observed rate — try multiple public sources in order.
    import urllib.request
    _HEADERS = {"User-Agent": "xauby-bot/1.0 (currency-helper)"}
    for url, extractor in _sources():
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read())
            rate = extractor(data)
            # Reject malformed upstream values before caching them.  The wide
            # bounds avoid encoding an assumed exchange rate in application
            # logic while still catching unit/currency mistakes.
            if 1.0 < rate < 1_000.0:
                _rate_mem = rate
                _rate_ts = now
                try:
                    from xauby.runtime.paths import ensure_runtime_dir
                    ensure_runtime_dir()
                    with open(_CACHE_FILE, "w", encoding="utf-8") as f:
                        json.dump({"rate": rate, "ts": now, "source": url}, f)
                except Exception:
                    pass
                logger.debug("USD/THB rate refreshed from %s: %.4f", url, rate)
                return rate
        except Exception as e:
            logger.debug("USD/THB rate fetch failed (%s): %s", url, e)

    # Use stale in-memory rate if available
    if _rate_mem is not None:
        return _rate_mem
    # Last resort: try stale disk cache
    try:
        if os.path.exists(_CACHE_FILE):
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            rate = float(d.get("rate", 0))
            if rate > 0:
                _rate_mem = rate
                _rate_ts = now - _TTL  # mark as stale so next call retries
                return _rate_mem
    except Exception:
        pass
    raise CurrencyRateUnavailable(
        "No observed USDT/THB rate is available from the network or cache"
    )


def usdt_to_thb(amount: float) -> float:
    """Convert a USDT amount to Thai Baht using the cached rate."""
    return amount * get_usd_thb_rate()


def format_thb(amount: float, compact: bool = False) -> str:
    """Format a THB amount as a Thai Baht string.

    compact=True  →  ฿34.5k / ฿1.2M
    compact=False →  ฿34,512
    """
    if compact:
        if abs(amount) >= 1_000_000:
            return f"฿{amount / 1_000_000:.1f}M"
        if abs(amount) >= 1_000:
            return f"฿{amount / 1_000:.1f}k"
        return f"฿{amount:.0f}"
    return f"฿{amount:,.0f}"
