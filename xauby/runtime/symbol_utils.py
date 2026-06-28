"""Shared symbol and timeframe normalization for runtime modules."""
from __future__ import annotations

import os

DEFAULT_ALLOWED_TIMEFRAMES = ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w")
ALLOWED_TIMEFRAMES = DEFAULT_ALLOWED_TIMEFRAMES
KNOWN_QUOTE_ASSETS = (
    "USDT",
    "USDC",
    "BUSD",
    "FDUSD",
    "TUSD",
    "USD",
    "THB",
    "EUR",
    "TRY",
    "BTC",
    "ETH",
    "BNB",
)

if os.path.exists("bot_config.yaml"):
    try:
        import yaml

        with open("bot_config.yaml", "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
            custom = cfg.get("trading", {}).get("allowed_timeframes") or cfg.get("allowed_timeframes")
            if custom:
                ALLOWED_TIMEFRAMES = tuple(str(x).strip().lower() for x in custom)
    except Exception:
        pass


def normalize_symbol(raw: str, quote: str = "USDT") -> str:
    s = str(raw or "").upper().replace("_", "").strip()
    if not s:
        return ""
    if s.endswith(quote):
        return s
    return f"{s}{quote}"


def normalize_market_symbol(raw: str, quote: str = "USDT") -> str:
    """Normalize whitelist symbols while preserving explicit market symbols.

    Whitelist assets are usually stored as base assets (``BTC`` -> ``BTCUSDT``),
    but cross-quote markets such as ``BTCTHB`` are already complete symbols and
    must not receive the default quote suffix.
    """
    s = str(raw or "").upper().replace("_", "").strip()
    if not s:
        return ""
    quote = str(quote or "USDT").upper()
    if s.endswith(quote):
        return s
    if any(s.endswith(q) and len(s) > len(q) for q in KNOWN_QUOTE_ASSETS):
        return s
    return f"{s}{quote}"


def normalize_tf(tf: str) -> str:
    t = str(tf or "4h").strip().lower()
    mapping = {"1d": "1d", "4h": "4h", "1h": "1h", "15m": "15m", "5m": "5m", "1m": "1m"}
    return mapping.get(t, t)
