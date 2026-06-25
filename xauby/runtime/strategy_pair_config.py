"""Per-pair strategy parameter overrides from coin_whitelist.json."""

from __future__ import annotations

from typing import Any, Dict, Optional

from xauby.runtime.pair_config import load_whitelist

_BACKTEST_ONLY_KEYS = frozenset({"backtest_data_proxy"})


def _base_from_symbol(symbol: str, quote: str = "USDT") -> str:
    sym = str(symbol or "").upper().replace("_", "")
    if sym.endswith(quote):
        return sym[: -len(quote)]
    return sym


def pair_strategy_params(symbol: str, project_root: str = ".") -> Dict[str, Any]:
    """Return strategy_params block for a whitelist asset (may be empty)."""
    wl = load_whitelist(project_root)
    quote = str(wl.get("quote_asset", "USDT") or "USDT")
    base = _base_from_symbol(symbol, quote)
    for asset in wl.get("assets") or []:
        if str(asset.get("symbol", "")).upper() == base:
            params = asset.get("strategy_params") or {}
            return dict(params) if isinstance(params, dict) else {}
    return {}


def backtest_data_proxy_for_symbol(symbol: str, project_root: str = ".") -> str:
    """Optional forced candle proxy symbol from canonical strategy config.

    Preferred:
      bot_config.yaml -> strategy.symbols.<SYMBOL>.backtest_data_proxy

    Legacy fallback:
      coin_whitelist.json -> assets[].strategy_params.backtest_data_proxy
    """
    import os
    import yaml

    sym = str(symbol or "").upper().replace("_", "")
    config_path = os.path.join(project_root, "bot_config.yaml")
    try:
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            symbols = (cfg.get("strategy") or {}).get("symbols") or {}
            quote = str((cfg.get("portfolio") or {}).get("quote_asset") or "USDT").upper()
            base = _base_from_symbol(sym, quote)
            overrides = symbols.get(sym) or symbols.get(base) or {}
            if isinstance(overrides, dict):
                proxy = str(overrides.get("backtest_data_proxy") or "").strip().upper().replace("_", "")
                if proxy:
                    return proxy
    except Exception:
        pass

    params = pair_strategy_params(symbol, project_root)
    proxy = str(params.get("backtest_data_proxy") or "").strip().upper().replace("_", "")
    return proxy


def set_pair_strategy_params(
    base_symbol: str,
    params: Dict[str, Any],
    *,
    project_root: str = ".",
    path: Optional[str] = None,
) -> None:
    """Write strategy_params for a whitelist asset."""
    from xauby.runtime.pair_config import load_whitelist, save_whitelist

    wl = load_whitelist(project_root, path)
    assets = list(wl.get("assets") or [])
    base = _base_from_symbol(base_symbol)
    entry = None
    for asset in assets:
        if str(asset.get("symbol", "")).upper() == base:
            entry = asset
            break
    if entry is None:
        raise KeyError(f"Asset {base} not in whitelist")
    entry["strategy_params"] = dict(params)
    wl["assets"] = assets
    save_whitelist(wl, project_root, path)


def merge_strategy_config(
    base_cfg: Dict[str, Any],
    symbol: str,
    *,
    project_root: str = ".",
    for_live: bool = True,
) -> Dict[str, Any]:
    """Overlay whitelist strategy_params onto the global strategy block."""
    merged = dict(base_cfg or {})
    overrides = pair_strategy_params(symbol, project_root)
    if not overrides:
        return merged
    for key, val in overrides.items():
        if for_live and key in _BACKTEST_ONLY_KEYS:
            continue
        merged[key] = val
    return merged
