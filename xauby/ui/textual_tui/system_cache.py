"""Cached host metrics for TUI header (avoid sampling CPU/RAM every render)."""

from __future__ import annotations

import time
from typing import Tuple

from xauby.ui.system import calculate_cpu_usage, get_ram_usage, get_db_size

_CACHE_TTL = 2.5
_last_fetch: float = 0.0
_cached: Tuple[float, float, float, float, float] = (0.0, 0.0, 0.0, 0.0, 0.0)


def get_cached_host_metrics() -> Tuple[float, float, float, float, float]:
    """Return (cpu_pct, ram_load, ram_used, ram_total, db_size_mb)."""
    global _last_fetch, _cached
    now = time.time()
    if now - _last_fetch < _CACHE_TTL:
        return _cached
    cpu_pct = float(calculate_cpu_usage() or 0.0)
    ram_load, ram_used, ram_total = get_ram_usage()
    db_size_mb = float(get_db_size())
    _cached = (cpu_pct, ram_load, ram_used, ram_total, db_size_mb)
    _last_fetch = now
    return _cached
