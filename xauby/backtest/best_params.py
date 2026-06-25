"""Per-symbol backtest best-parameter storage."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from xauby.backtest.constants import BEST_PARAMS_FILE, CACHE_DIR

_SCHEMA_VERSION = 2


def _normalize_symbol(symbol: str) -> str:
    return str(symbol or "").upper().replace("_", "")


def _read_store() -> Dict[str, Any]:
    if not os.path.exists(BEST_PARAMS_FILE):
        return {}
    try:
        with open(BEST_PARAMS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_store(data: Dict[str, Any]) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(BEST_PARAMS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _is_legacy_flat(data: Dict[str, Any]) -> bool:
    if "by_symbol" in data:
        return False
    return any(k in data for k in ("sl_atr_mult", "rsi_min", "net_profit_pct"))


def load_best_params(symbol: Optional[str] = None) -> Dict[str, Any]:
    """Load optimized best params for a symbol, with legacy flat-file fallback."""
    data = _read_store()
    if not data:
        return {}

    sym = _normalize_symbol(symbol) if symbol else ""
    if "by_symbol" in data and isinstance(data["by_symbol"], dict):
        by_sym = data["by_symbol"]
        if sym:
            entry = by_sym.get(sym)
            if isinstance(entry, dict):
                return dict(entry)
            return {}
        if by_sym:
            first = next(iter(by_sym.values()), None)
            if isinstance(first, dict):
                return dict(first)
        return {}

    if _is_legacy_flat(data):
        return dict(data)
    return {}


def save_best_parameters(symbol: str, best_result: Dict[str, Any]) -> None:
    """Persist optimized best params for a single symbol."""
    sym = _normalize_symbol(symbol)
    if not sym:
        raise ValueError("symbol is required")

    data = _read_store()
    if _is_legacy_flat(data) and "by_symbol" not in data:
        data = {
            "schema_version": _SCHEMA_VERSION,
            "by_symbol": {},
        }
    if "by_symbol" not in data or not isinstance(data["by_symbol"], dict):
        data["by_symbol"] = {}
    data["schema_version"] = _SCHEMA_VERSION
    data["by_symbol"][sym] = dict(best_result)
    _write_store(data)
