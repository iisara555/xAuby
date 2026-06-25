"""Read/write coin_whitelist.json and sync bot_config.yaml data.pairs."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from xauby.runtime.symbol_utils import normalize_symbol as _normalize_symbol, normalize_tf as _normalize_tf

DEFAULT_WHITELIST_PATH = "coin_whitelist.json"


def _whitelist_path(project_root: str, path: Optional[str] = None) -> str:
    p = path or DEFAULT_WHITELIST_PATH
    if not os.path.isabs(p):
        p = os.path.join(project_root, p)
    return p


def load_whitelist(project_root: str = ".", path: Optional[str] = None) -> Dict[str, Any]:
    fp = _whitelist_path(project_root, path)
    if not os.path.exists(fp):
        return {"version": 1, "quote_asset": "USDT", "assets": []}
    with open(fp, "r", encoding="utf-8") as f:
        return json.load(f) or {}


def save_whitelist(data: Dict[str, Any], project_root: str = ".", path: Optional[str] = None) -> None:
    fp = _whitelist_path(project_root, path)
    os.makedirs(os.path.dirname(fp) or ".", exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _find_asset(assets: List[Dict], base: str) -> Optional[Dict]:
    base = base.upper().replace("USDT", "").strip()
    for a in assets:
        if str(a.get("symbol", "")).upper() == base:
            return a
    return None


def add_whitelist_asset(
    base_symbol: str,
    *,
    enabled: bool = True,
    primary_timeframe: str = "4h",
    confirm_timeframe: str = "1d",
    project_root: str = ".",
    path: Optional[str] = None,
) -> str:
    """Add or enable asset; returns full symbol e.g. BTCUSDT."""
    wl = load_whitelist(project_root, path)
    assets = list(wl.get("assets") or [])
    base = str(base_symbol).upper().replace("USDT", "").replace("_", "").strip()
    entry = _find_asset(assets, base)
    if entry is None:
        entry = {
            "symbol": base,
            "enabled": enabled,
            "primary_timeframe": _normalize_tf(primary_timeframe),
            "confirm_timeframe": _normalize_tf(confirm_timeframe),
        }
        assets.append(entry)
    else:
        entry["enabled"] = enabled
        entry.setdefault("primary_timeframe", _normalize_tf(primary_timeframe))
        entry.setdefault("confirm_timeframe", _normalize_tf(confirm_timeframe))
    wl["assets"] = assets
    save_whitelist(wl, project_root, path)
    quote = str(wl.get("quote_asset", "USDT") or "USDT")
    sym = _normalize_symbol(base, quote)
    sync_data_pairs_yaml([_normalize_symbol(a.get("symbol", ""), quote) for a in assets if a.get("enabled")], project_root)
    return sym


def update_whitelist_asset(
    base_symbol: str,
    *,
    enabled: Optional[bool] = None,
    project_root: str = ".",
    path: Optional[str] = None,
) -> None:
    wl = load_whitelist(project_root, path)
    assets = list(wl.get("assets") or [])
    base = str(base_symbol).upper().replace("USDT", "").strip()
    entry = _find_asset(assets, base)
    if entry is None:
        raise KeyError(f"Asset {base} not in whitelist")
    if enabled is not None:
        entry["enabled"] = enabled
    wl["assets"] = assets
    save_whitelist(wl, project_root, path)
    quote = str(wl.get("quote_asset", "USDT") or "USDT")
    enabled_syms = [
        _normalize_symbol(a.get("symbol", ""), quote)
        for a in assets
        if a.get("enabled")
    ]
    sync_data_pairs_yaml(enabled_syms, project_root)


def set_pair_strategy_config(
    base_symbol: str,
    *,
    strategy: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    project_root: str = ".",
    path: Optional[str] = None,
) -> None:
    """Update the strict-whitelist strategy source of truth for one asset."""
    wl = load_whitelist(project_root, path)
    assets = list(wl.get("assets") or [])
    base = str(base_symbol).upper().replace("_", "").strip()
    quote = str(wl.get("quote_asset", "USDT") or "USDT").upper()
    if base.endswith(quote):
        base = base[: -len(quote)]
    entry = _find_asset(assets, base)
    if entry is None:
        raise KeyError(f"Asset {base} not in whitelist")
    if strategy is not None:
        entry["strategy"] = str(strategy)
    if params:
        strategy_params = entry.setdefault("strategy_params", {})
        if not isinstance(strategy_params, dict):
            strategy_params = {}
            entry["strategy_params"] = strategy_params
        strategy_params.update(params)
    wl["assets"] = assets
    save_whitelist(wl, project_root, path)


def set_pair_timeframes(
    base_symbol: str,
    primary_timeframe: str,
    confirm_timeframe: Optional[str] = None,
    *,
    project_root: str = ".",
    path: Optional[str] = None,
) -> None:
    wl = load_whitelist(project_root, path)
    assets = list(wl.get("assets") or [])
    base = str(base_symbol).upper().replace("USDT", "").strip()
    entry = _find_asset(assets, base)
    if entry is None:
        raise KeyError(f"Asset {base} not in whitelist")
    entry["primary_timeframe"] = _normalize_tf(primary_timeframe)
    if confirm_timeframe is not None:
        entry["confirm_timeframe"] = _normalize_tf(confirm_timeframe)
    wl["assets"] = assets
    save_whitelist(wl, project_root, path)


def sync_data_pairs_yaml(symbols: List[str], project_root: str = ".", config_path: str = "bot_config.yaml") -> None:
    """Update data.pairs list in bot_config.yaml (line-based, preserves comments)."""
    cfg_path = config_path if os.path.isabs(config_path) else os.path.join(project_root, config_path)
    if not os.path.exists(cfg_path):
        return
    unique = []
    seen = set()
    for s in symbols:
        sym = str(s).upper().replace("_", "")
        if sym and sym not in seen:
            seen.add(sym)
            unique.append(sym)
    pairs_line = "  pairs: [" + ", ".join(f'"{p}"' for p in unique) + "]\n"

    with open(cfg_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    in_data = False
    replaced = False
    out: List[str] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("data:") and not line.startswith((" ", "\t")):
            in_data = True
            out.append(line)
            i += 1
            continue
        if in_data and stripped and not line.startswith((" ", "\t")):
            in_data = False
        if in_data and stripped.startswith("pairs:") and not replaced:
            out.append(pairs_line)
            replaced = True
            i += 1
            # Drop any orphaned block-sequence items that belonged to the old
            # multi-line `pairs:` list (e.g. "  - XAUTUSDT"). Leaving them after
            # an inline `pairs: [...]` line produces invalid YAML and silently
            # breaks every reader that uses yaml.safe_load.
            while i < n:
                nxt_stripped = lines[i].strip()
                if nxt_stripped.startswith("- ") or nxt_stripped == "-":
                    i += 1
                    continue
                break
            continue
        out.append(line)
        i += 1
    lines = out

    if not replaced:
        for i, line in enumerate(lines):
            if line.strip().startswith("data:"):
                lines.insert(i + 1, pairs_line)
                replaced = True
                break

    with open(cfg_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
