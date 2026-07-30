"""Pure timeframe helpers safe for strategy plugins to import."""

from __future__ import annotations

import re

_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def timeframe_seconds(timeframe: str, default: int = 14400) -> int:
    """Convert a timeframe string like ``4h``, ``1d``, or ``15m`` to seconds."""
    match = re.fullmatch(r"(\d+)([mhdw])", str(timeframe or "").strip().lower())
    if not match:
        return default
    return int(match.group(1)) * _UNIT_SECONDS[match.group(2)]
