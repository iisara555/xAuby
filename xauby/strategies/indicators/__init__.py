"""Chart indicator plugins — compute series + display metadata for TUI."""
from __future__ import annotations

from xauby.strategies.indicators.registry import (
    available_indicators,
    indicators_for_strategy,
    load_indicator,
    register,
)

__all__ = [
    "available_indicators",
    "indicators_for_strategy",
    "load_indicator",
    "register",
]
