"""Validated derivatives settings shared by engine and exchange adapters."""
from __future__ import annotations

from typing import Any, Dict

from xauby.runtime.config_error import ConfigError


def derivatives_settings(config: Dict[str, Any]) -> Dict[str, Any]:
    exchange = config.get("exchange", {}) or {}
    derivatives = config.get("derivatives", {}) or {}
    market_type = str(exchange.get("market_type", "spot") or "spot").lower()
    margin_mode = str(exchange.get("margin_mode", "spot" if market_type == "spot" else "isolated")).lower()
    position_mode = str(exchange.get("position_mode", "one_way") or "one_way").lower()
    settle_asset = str(exchange.get("settle_asset") or exchange.get("quote_asset") or "USDT").upper()
    default_leverage = float(derivatives.get("default_leverage", 1.0) or 1.0)
    max_leverage = float(derivatives.get("max_leverage", 3.0) or 3.0)
    if market_type not in ("spot", "swap"):
        raise ConfigError("exchange.market_type must be spot or swap")
    if market_type == "swap" and margin_mode != "isolated":
        raise ConfigError("v1 perpetual support requires exchange.margin_mode=isolated")
    if position_mode != "one_way":
        raise ConfigError("v1 perpetual support requires exchange.position_mode=one_way")
    if not (1.0 <= default_leverage <= max_leverage <= 3.0):
        raise ConfigError("derivatives leverage must satisfy 1 <= default <= max <= 3")
    return {
        "market_type": market_type,
        "margin_mode": margin_mode,
        "position_mode": position_mode,
        "settle_asset": settle_asset,
        "default_leverage": default_leverage,
        "max_leverage": max_leverage,
        "funding_guard_enabled": bool(derivatives.get("funding_guard_enabled", True)),
        "max_abs_funding_rate": float(derivatives.get("max_abs_funding_rate", 0.003) or 0.003),
        "min_liquidation_distance_pct": float(derivatives.get("min_liquidation_distance_pct", 20.0) or 20.0),
    }
