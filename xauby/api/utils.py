"""Exchange-neutral helpers shared by every gateway adapter.

These operate on the normalized ``exchangeInfo`` shape that every
:class:`~xauby.api.interface.IExchangeGateway` produces, plus a couple of pure
utilities (client-id generation, step rounding). Keeping them here lets the
engine stay gateway-agnostic instead of importing ``LiteBinanceClient`` for
static helpers.

Import direction: this module must not import from ``xauby.api.client`` or
``xauby.api.ccxt_client`` (those import from here).
"""

from __future__ import annotations

import time
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

# Conservative defaults used when an exchange does not expose filters for a pair.
DEFAULT_SYMBOL_FILTERS: Dict[str, Any] = {
    "stepSize": 0.000001,
    "tickSize": 0.000001,
    "stepSizeStr": "0.000001",
    "tickSizeStr": "0.000001",
    "minQty": 0.0,
    "minNotional": 10.0,
    "baseAssetPrecision": 8,
    "quoteAssetPrecision": 8,
}


def make_client_id(prefix: str = "xb") -> str:
    """Generate an exchange-neutral client order id (<=36 chars)."""
    safe = "".join(c for c in prefix if c.isalnum() or c in "-_")[:12]
    return f"xauby-{safe}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"


def round_step(value: float, step: Any) -> float:
    """Round ``value`` down to the nearest multiple of ``step``."""
    if step is None:
        return float(value)
    try:
        s = Decimal(str(step))
    except Exception:
        return float(value)
    if s <= 0:
        return float(value)
    try:
        d = Decimal(str(value))
    except Exception:
        return float(value)
    return float((d // s) * s)


def find_symbol_entry(symbols: List[Dict[str, Any]], sym: str) -> Optional[Dict[str, Any]]:
    """Resolve the exact symbol entry from a normalized exchangeInfo list.

    exchangeInfo may return every pair even when a single symbol was requested.
    """
    target = sym.upper().replace("_", "")
    for entry in symbols or []:
        if str(entry.get("symbol", "")).upper() == target:
            return entry
    return None


def _safe_positive_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        x = float(val)
    except (TypeError, ValueError):
        return None
    if x <= 0:
        return None
    return x


def _positive_float_and_str(*vals: Any) -> tuple[Optional[float], Optional[str]]:
    for val in vals:
        parsed = _safe_positive_float(val)
        if parsed:
            return parsed, str(val).strip()
    return None, None


def parse_exchange_filters(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Parse a normalized exchangeInfo entry into lot/price/notional filters."""
    filters = dict(DEFAULT_SYMBOL_FILTERS)
    filters["baseAssetPrecision"] = int(entry.get("baseAssetPrecision", 8))
    filters["quoteAssetPrecision"] = int(entry.get("quoteAssetPrecision", 8))
    for f in entry.get("filters", []):
        ftype = str(f.get("filterType", "")).upper()
        if ftype == "LOT_SIZE":
            val, val_str = _positive_float_and_str(f.get("stepSize"), f.get("minQty"))
            if val:
                filters["stepSize"] = val
                filters["stepSizeStr"] = val_str or str(val)
            val_min = _safe_positive_float(f.get("minQty"))
            if val_min:
                filters["minQty"] = val_min
        elif ftype == "PRICE_FILTER":
            val, val_str = _positive_float_and_str(f.get("tickSize"), f.get("minPrice"))
            if val:
                filters["tickSize"] = val
                filters["tickSizeStr"] = val_str or str(val)
        elif ftype in ("MIN_NOTIONAL", "NOTIONAL"):
            val = _safe_positive_float(f.get("minNotional") or f.get("notional"))
            if val:
                filters["minNotional"] = val
    return filters
