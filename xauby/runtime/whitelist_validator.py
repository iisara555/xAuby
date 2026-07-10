"""Startup validation for coin_whitelist.json (strict architecture mode)."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Set

from xauby.runtime.config_error import ConfigError
from xauby.runtime.symbol_utils import ALLOWED_TIMEFRAMES, normalize_market_symbol, normalize_tf


def strategy_name_from_whitelist_entry(entry: Dict[str, Any]) -> str:
    raw = str(entry.get("strategy") or entry.get("strategy_name") or "").strip()
    if not raw:
        return ""
    from xauby.strategies.registry import normalize_strategy_name

    return normalize_strategy_name(raw)


def validate_whitelist_file_exists(whitelist_path: str) -> None:
    if not os.path.exists(whitelist_path):
        raise ConfigError(f"Whitelist file not found: {whitelist_path}")


def validate_whitelist_schema(
    wl: Dict[str, Any],
    *,
    strict: bool = True,
) -> List[str]:
    """Return list of enabled full symbols after validation. Raises ConfigError on failure."""
    if not isinstance(wl, dict):
        raise ConfigError("Whitelist must be a JSON object")

    assets = wl.get("assets")
    if not isinstance(assets, list):
        raise ConfigError("Whitelist must contain an 'assets' array")

    quote = str(wl.get("quote_asset", "USDT") or "USDT").upper()
    enabled: List[str] = []
    seen: Set[str] = set()

    for idx, entry in enumerate(assets):
        if not isinstance(entry, dict):
            raise ConfigError(f"Whitelist assets[{idx}] must be an object")
        base = str(entry.get("symbol", "")).strip()
        if not base:
            raise ConfigError(f"Whitelist assets[{idx}] missing 'symbol'")
        sym = normalize_market_symbol(base, quote)
        if sym in seen:
            raise ConfigError(f"Duplicate whitelist symbol: {sym}")
        seen.add(sym)

        if not bool(entry.get("enabled", True)):
            continue

        ptf = normalize_tf(entry.get("primary_timeframe") or "4h")
        if ptf not in ALLOWED_TIMEFRAMES:
            raise ConfigError(f"{sym}: invalid primary_timeframe {ptf!r}")

        ctf_raw = entry.get("confirm_timeframe")
        if ctf_raw not in (None, ""):
            ctf = normalize_tf(str(ctf_raw))
            if ctf not in ALLOWED_TIMEFRAMES:
                raise ConfigError(f"{sym}: invalid confirm_timeframe {ctf!r}")

        if strict:
            strat = strategy_name_from_whitelist_entry(entry)
            if not strat:
                raise ConfigError(f"{sym}: missing 'strategy' in whitelist asset (required in strict mode)")
            from xauby.strategies.registry import available_strategies

            if strat not in available_strategies():
                raise ConfigError(
                    f"{sym}: unknown strategy {strat!r} (available: {available_strategies()})"
                )
            from xauby.strategies.registry import strategy_manifest

            tags = {str(tag).lower() for tag in strategy_manifest(strat).get("tags", [])}
            is_short = "short" in tags
            short_allowed = (
                str(entry.get("mode", "")).lower() == "sim"
                or bool(entry.get("short_live_enabled", False))
            )
            if "research" in tags and not is_short:
                raise ConfigError(
                    f"{sym}: research-only strategy {strat!r} cannot run in the production engine"
                )
            if is_short and not short_allowed:
                raise ConfigError(
                    f"{sym}: short strategy {strat!r} requires mode=sim or short_live_enabled=true"
                )

        enabled.append(sym)

    if not enabled:
        raise ConfigError("Whitelist has no enabled assets")

    return enabled


def validate_exchange_support(
    symbols: List[str],
    supported: Optional[Set[str]],
    *,
    require_supported: bool,
) -> None:
    if not require_supported or supported is None:
        return
    missing = [s for s in symbols if s not in supported]
    if missing:
        raise ConfigError(
            f"Whitelist symbols not on exchange: {', '.join(missing)}"
        )


def validate_whitelist(
    wl: Dict[str, Any],
    *,
    whitelist_path: str = "",
    supported: Optional[Set[str]] = None,
    require_supported_market: bool = True,
    strict: bool = True,
) -> List[str]:
    if whitelist_path:
        validate_whitelist_file_exists(whitelist_path)
    enabled = validate_whitelist_schema(wl, strict=strict)
    validate_exchange_support(
        enabled,
        supported,
        require_supported=require_supported_market,
    )
    return enabled
