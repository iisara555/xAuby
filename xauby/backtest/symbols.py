"""Backtest-only symbols and candle data proxies (not on live whitelist)."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from xauby.runtime.pair_config import load_whitelist
from xauby.runtime.symbol_utils import normalize_symbol as _normalize_symbol

# Pairs allowed for backtest replay but not live trading whitelist.
BACKTEST_ONLY_ASSETS: Dict[str, Dict[str, Any]] = {
    "PAXG": {
        "primary_timeframe": "4h",
        "confirm_timeframe": "1d",
    },
}

# Require at least this fraction of a full 6-month primary window before skipping proxy.
PROXY_MIN_HISTORY_FRACTION = 0.75


def _normalize_pair(symbol: str, quote: str = "USDT") -> str:
    sym = str(symbol or "").upper().replace("_", "")
    if sym and not sym.endswith(quote):
        sym = f"{sym}{quote}"
    return sym


def _base_symbol(symbol: str, quote: str = "USDT") -> str:
    sym = _normalize_pair(symbol, quote)
    if sym.endswith(quote):
        return sym[: -len(quote)]
    return sym


def data_proxy_map(project_root: str = ".") -> Dict[str, str]:
    """Build symbol→proxy map from whitelist strategy_params."""
    wl = load_whitelist(project_root)
    quote = str(wl.get("quote_asset", "USDT") or "USDT").upper()
    out: Dict[str, str] = {}
    for asset in wl.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        sym = _normalize_symbol(str(asset.get("symbol", "")), quote)
        params = asset.get("strategy_params") or {}
        if not isinstance(params, dict):
            continue
        proxy = str(params.get("backtest_data_proxy") or "").strip()
        if proxy:
            out[sym] = _normalize_pair(proxy, quote)
    return out


def backtest_timeframes_for_symbol(symbol: str, quote: str = "USDT") -> Tuple[Optional[str], Optional[str]]:
    """Timeframes for backtest-only assets (e.g. PAXG not on whitelist)."""
    base = _base_symbol(symbol, quote)
    entry = BACKTEST_ONLY_ASSETS.get(base)
    if not entry:
        return None, None
    primary = entry.get("primary_timeframe")
    confirm = entry.get("confirm_timeframe")
    return (
        str(primary) if primary else None,
        str(confirm) if confirm else None,
    )


def min_bars_for_proxy(primary_timeframe: str) -> int:
    """Minimum primary bars to skip proxy (fraction of 6-month window)."""
    from xauby.backtest.data import get_six_months_candle_count

    expected = int(get_six_months_candle_count(primary_timeframe or "4h"))
    return max(100, int(expected * PROXY_MIN_HISTORY_FRACTION))


def resolve_data_proxy(
    requested_symbol: str,
    primary_bars: int,
    *,
    primary_timeframe: str = "4h",
    force_proxy: Optional[str] = None,
    project_root: str = ".",
) -> Optional[str]:
    """Return proxy symbol when history is too short or force_proxy is set."""
    if force_proxy:
        proxy = _normalize_pair(force_proxy)
        requested = _normalize_pair(requested_symbol)
        if proxy and proxy != requested:
            return proxy
        return None

    sym = _normalize_pair(requested_symbol)
    proxy = data_proxy_map(project_root).get(sym)
    if not proxy:
        return None
    if primary_bars >= min_bars_for_proxy(primary_timeframe):
        return None
    return _normalize_pair(proxy)
