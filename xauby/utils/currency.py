"""Observed USDT/USD to THB rates with bounded, tenant-capable caching.

The live Binance TH USDT/THB market is preferred because xAuby's OKX
portfolio values are denominated in USDT.  Daily public USD/THB reference
feeds are fallbacks and are explicitly identified as USD rates.  Cached data
is refreshed after 10 minutes and is never used after 24 hours.
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from xauby.runtime.paths import usd_thb_rate_path
from xauby.utils.atomic_io import atomic_json_write

logger = logging.getLogger("xauby.utils.currency")

_CACHE_FILE = usd_thb_rate_path()
_TTL = 600
_MAX_STALE = 86_400
_MIN_PLAUSIBLE_RATE = 5.0
_MAX_PLAUSIBLE_RATE = 100.0
_FETCH_LOCK = threading.Lock()


class CurrencyRateUnavailable(RuntimeError):
    """Raised when no sufficiently recent observed THB rate is available."""


def _unix_date(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0


def _binance_th(data: dict[str, Any], fetched_at: float) -> dict[str, Any]:
    if data.get("symbol") != "USDTTHB":
        raise ValueError("Binance TH returned the wrong symbol")
    bid = float(data.get("bidPrice") or 0)
    ask = float(data.get("askPrice") or 0)
    if bid > 0 and ask >= bid:
        rate = (bid + ask) / 2
        if (ask - bid) / rate > 0.02:
            raise ValueError("Binance TH spread is too wide")
    else:
        rate = float(data.get("lastPrice") or data.get("price") or 0)
    observed_at = float(data.get("closeTime") or 0) / 1000
    if observed_at <= 0:
        observed_at = fetched_at
    if observed_at > fetched_at + 300 or fetched_at - observed_at > 900:
        raise ValueError("Binance TH ticker is stale")
    return {
        "rate": rate,
        "base": "USDT",
        "quote": "THB",
        "source": "binance_th",
        "source_label": "Binance TH",
        "source_url": "https://www.binance.th/en/trade/USDT_THB",
        "observed_at": observed_at,
    }


def _frankfurter_ecb(data: dict[str, Any], fetched_at: float) -> dict[str, Any]:
    if data.get("base") != "USD" or data.get("quote") != "THB":
        raise ValueError("Frankfurter returned the wrong currency pair")
    return {
        "rate": float(data.get("rate") or 0),
        "base": "USD",
        "quote": "THB",
        "source": "frankfurter_ecb",
        "source_label": "ECB via Frankfurter",
        "source_url": "https://frankfurter.dev/providers/ecb/",
        "observed_at": _unix_date(data.get("date")) or fetched_at,
    }


def _exchange_rate_api(data: dict[str, Any], fetched_at: float) -> dict[str, Any]:
    if data.get("result") != "success" or data.get("base_code") != "USD":
        raise ValueError("ExchangeRate-API returned an invalid response")
    rates = data.get("rates") if isinstance(data.get("rates"), dict) else {}
    return {
        "rate": float(rates.get("THB") or 0),
        "base": "USD",
        "quote": "THB",
        "source": "exchange_rate_api",
        "source_label": "ExchangeRate-API",
        "source_url": "https://www.exchangerate-api.com",
        "observed_at": float(data.get("time_last_update_unix") or fetched_at),
    }


def _sources() -> tuple[tuple[str, Callable[[dict[str, Any], float], dict[str, Any]]], ...]:
    """Return public price sources ordered by conversion accuracy."""
    return (
        (
            "https://api.binance.th/api/v1/ticker/24hr?symbol=USDTTHB",
            _binance_th,
        ),
        (
            "https://api.frankfurter.dev/v2/rate/USD/THB?providers=ECB",
            _frankfurter_ecb,
        ),
        (
            "https://open.er-api.com/v6/latest/USD",
            _exchange_rate_api,
        ),
    )


def _validated_rate(value: Any) -> float:
    rate = float(value)
    if not math.isfinite(rate) or not (_MIN_PLAUSIBLE_RATE < rate < _MAX_PLAUSIBLE_RATE):
        raise ValueError("THB rate is outside plausible bounds")
    return rate


def _cache_quote(cache_file: str, now: float) -> dict[str, Any] | None:
    try:
        with open(cache_file, "r", encoding="utf-8") as handle:
            cached = json.load(handle)
        if not isinstance(cached, dict):
            return None
        rate = _validated_rate(cached.get("rate"))
        fetched_at = float(cached.get("fetched_at") or cached.get("ts") or 0)
        if not math.isfinite(fetched_at) or fetched_at <= 0:
            return None
        endpoint_url = str(cached.get("endpoint_url") or cached.get("source") or "")
        source_url = str(cached.get("source_url") or endpoint_url)
        base = str(cached.get("base") or ("USDT" if "binance.th" in endpoint_url else "USD"))
        source = str(cached.get("source_id") or ("binance_th" if "binance.th" in endpoint_url else "cached_fx"))
        source_label = str(cached.get("source_label") or ("Binance TH" if source == "binance_th" else "Cached FX rate"))
        observed_at = float(cached.get("observed_at") or fetched_at)
        age_sec = max(0.0, now - fetched_at)
        return {
            "rate": rate,
            "base": base,
            "quote": "THB",
            "pair": f"{base}/THB",
            "source": source,
            "source_label": source_label,
            "source_url": source_url,
            "endpoint_url": endpoint_url,
            "observed_at": observed_at,
            "fetched_at": fetched_at,
            "age_sec": age_sec,
            "stale": age_sec >= _TTL,
        }
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _fetch_quote(now: float) -> dict[str, Any] | None:
    import urllib.request

    headers = {"User-Agent": "xauby-bot/1.0 (currency-helper)"}
    for url, extractor in _sources():
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=4) as response:
                data = json.loads(response.read())
            if not isinstance(data, dict):
                raise ValueError("rate response is not an object")
            quote = extractor(data, now)
            quote["rate"] = _validated_rate(quote.get("rate"))
            quote.update({
                "pair": f"{quote['base']}/THB",
                "endpoint_url": url,
                "fetched_at": now,
                "age_sec": 0.0,
                "stale": False,
            })
            return quote
        except Exception as error:
            logger.debug("THB rate fetch failed (%s): %s", url, error)
    return None


def get_thb_rate_quote(
    *,
    cache_file: Optional[str] = None,
    now: Optional[float] = None,
) -> dict[str, Any]:
    """Return a validated THB quote with source and freshness metadata.

    A stale quote is returned only when every network source fails and the last
    successful observation was fetched less than 24 hours ago.
    """
    current_time = float(now if now is not None else time.time())
    target = os.fspath(cache_file or _CACHE_FILE)
    cached = _cache_quote(target, current_time)
    if cached and not cached["stale"]:
        return cached

    with _FETCH_LOCK:
        cached = _cache_quote(target, current_time)
        if cached and not cached["stale"]:
            return cached
        fresh = _fetch_quote(current_time)
        if fresh:
            payload = {
                "rate": fresh["rate"],
                "ts": fresh["fetched_at"],
                "fetched_at": fresh["fetched_at"],
                "observed_at": fresh["observed_at"],
                "base": fresh["base"],
                "quote": "THB",
                "source_id": fresh["source"],
                "source_label": fresh["source_label"],
                "source_url": fresh["source_url"],
                "endpoint_url": fresh["endpoint_url"],
            }
            try:
                atomic_json_write(target, payload, indent=2, mode=0o660)
            except OSError as error:
                logger.debug("THB rate cache write failed (%s): %s", target, error)
            logger.debug(
                "THB rate refreshed from %s: %.4f",
                fresh["endpoint_url"],
                fresh["rate"],
            )
            return fresh

    cached = _cache_quote(target, current_time)
    if cached and cached["age_sec"] <= _MAX_STALE:
        return cached
    raise CurrencyRateUnavailable(
        "No observed USDT/THB or USD/THB rate newer than 24 hours is available"
    )


def get_usd_thb_rate() -> float:
    """Return the best observed THB rate (legacy numeric API)."""
    return float(get_thb_rate_quote()["rate"])


def usdt_to_thb(amount: float) -> float:
    """Convert a USDT amount using the best observed THB quote."""
    return amount * get_usd_thb_rate()


def format_thb(amount: float, compact: bool = False) -> str:
    """Format a THB amount as a Thai Baht string."""
    if compact:
        if abs(amount) >= 1_000_000:
            return f"฿{amount / 1_000_000:.1f}M"
        if abs(amount) >= 1_000:
            return f"฿{amount / 1_000:.1f}k"
        return f"฿{amount:.0f}"
    return f"฿{amount:,.0f}"
