"""Exchange-neutral config resolution.

Single source of truth for values that used to be hardcoded against Binance /
USDT / a 0.1% fee. Every layer (live engine, sim broker, backtest, replay)
resolves these through the same helpers so simulation and live stay in parity.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional, Tuple

# Final fallback when nothing is configured. Binance spot taker fee (0.1%).
DEFAULT_FEE_PCT = 0.001
DEFAULT_QUOTE_ASSET = "USDT"
DEFAULT_PROVIDER = "binance"
DEFAULT_BINANCE_BASE_URL = "https://api.binance.th"


def _exchange_cfg(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not config:
        return {}
    return config.get("exchange") or {}


def resolve_fee_pct(config: Optional[Dict[str, Any]]) -> float:
    """Resolve the exchange-wide taker fee fraction (e.g. 0.001 == 0.1%).

    Precedence: ``exchange.fee_pct`` -> ``backtest.fee_pct`` ->
    ``trading.fee_pct`` -> :data:`DEFAULT_FEE_PCT`. The backtest/trading
    fallbacks are kept for backward compatibility with configs written before
    the ``exchange`` block existed.
    """
    config = config or {}
    candidates = (
        _exchange_cfg(config).get("fee_pct"),
        (config.get("backtest") or {}).get("fee_pct"),
        (config.get("trading") or {}).get("fee_pct"),
    )
    for value in candidates:
        if value is None:
            continue
        try:
            fee = float(value)
        except (TypeError, ValueError):
            continue
        if fee >= 0:
            return fee
    return DEFAULT_FEE_PCT


def resolve_quote_asset(config: Optional[Dict[str, Any]]) -> str:
    """Resolve the default quote asset (e.g. ``USDT``) for pairs."""
    raw = _exchange_cfg(config).get("quote_asset")
    if raw:
        return str(raw).upper()
    return DEFAULT_QUOTE_ASSET


def resolve_provider(config: Optional[Dict[str, Any]]) -> str:
    """Resolve the configured exchange provider id, lowercased."""
    cfg = _exchange_cfg(config)
    raw = cfg.get("provider") or cfg.get("ccxt_id") or cfg.get("name") or DEFAULT_PROVIDER
    return str(raw).lower()


def _credential_prefix(config: Optional[Dict[str, Any]]) -> str:
    """Env-var prefix for credentials (e.g. ``BINANCE``, ``KRAKEN``)."""
    cfg = _exchange_cfg(config)
    # ``provider: ccxt`` is a transport, not an exchange id — prefer ccxt_id.
    raw = cfg.get("ccxt_id")
    if not raw:
        provider = str(cfg.get("provider") or "").lower()
        raw = cfg.get("provider") if provider not in ("", "ccxt") else None
    raw = raw or cfg.get("name") or DEFAULT_PROVIDER
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", str(raw)).strip("_").upper()
    return cleaned or "BINANCE"


def credential_env_names(config: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    """Resolve the (api_key, api_secret, base_url) env var names.

    Explicit ``api_key_env`` / ``api_secret_env`` / ``base_url_env`` win;
    otherwise they default to ``<PREFIX>_API_KEY`` etc. so each exchange reads
    its own credentials instead of always ``BINANCE_*``.
    """
    cfg = _exchange_cfg(config)
    prefix = _credential_prefix(config)
    return (
        str(cfg.get("api_key_env") or f"{prefix}_API_KEY"),
        str(cfg.get("api_secret_env") or f"{prefix}_API_SECRET"),
        str(cfg.get("base_url_env") or f"{prefix}_BASE_URL"),
    )


def resolve_exchange_credentials(
    config: Optional[Dict[str, Any]],
) -> Tuple[str, str, Optional[str]]:
    """Resolve (api_key, api_secret, base_url) from the environment + config.

    Single source of truth so the engine, health probe, and factory read the
    same exchange-driven env vars rather than hardcoding ``BINANCE_*``.
    base_url precedence: ``exchange.base_url`` -> ``<PREFIX>_BASE_URL`` env ->
    ``None`` (let the adapter apply its own default).
    """
    cfg = _exchange_cfg(config)
    key_env, secret_env, base_url_env = credential_env_names(config)
    base_url = cfg.get("base_url") or os.environ.get(base_url_env) or None
    return (
        os.environ.get(key_env, ""),
        os.environ.get(secret_env, ""),
        base_url,
    )
