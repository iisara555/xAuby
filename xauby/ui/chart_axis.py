"""Y-axis tick helpers for terminal charts (no heavy deps)."""
from __future__ import annotations

import math
from typing import Dict, List, Optional


def nice_price_step(price_range: float) -> float:
    """Pick a readable Y-axis step (~8 labels across the chart)."""
    if price_range <= 0:
        return 1.0
    raw = price_range / 8.0
    if raw <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(raw))
    for mult in (1.0, 2.0, 5.0, 10.0):
        step = mult * magnitude
        if raw <= step:
            return step
    return 10.0 * magnitude


def format_price_axis(price: float, price_step: Optional[float] = None) -> str:
    """Compact Y-axis price label with dynamic decimals based on price_step or price magnitude."""
    if price_step is not None and price_step > 0:
        decimals = max(0, -math.floor(math.log10(price_step)))
    else:
        # Fallback heuristic based on price magnitude
        if price >= 1_000:
            decimals = 0
        elif price >= 100:
            decimals = 1
        elif price >= 1:
            decimals = 2
        else:
            decimals = 4
    return f"${price:,.{decimals}f}"


def price_axis_label_rows(
    min_l: float,
    max_h: float,
    height: int,
    price_step: float,
) -> Dict[int, float]:
    """Map chart row index -> tick price (at most one label per row)."""
    if height <= 0:
        return {}
    price_range = max_h - min_l
    if price_range <= 0:
        return {0: min_l}

    decimals = 0
    if price_step > 0:
        decimals = max(0, -math.floor(math.log10(price_step)))

    tick = math.floor(min_l / price_step) * price_step
    label_prices: List[float] = []
    while tick <= max_h + price_step * 0.5:
        if min_l - price_step * 0.01 <= tick <= max_h + price_step * 0.01:
            label_prices.append(round(tick, decimals + 4))
        tick += price_step

    if not label_prices:
        return {height - 1: max_h, 0: min_l}

    label_rows: Dict[int, float] = {}
    for lp in label_prices:
        best_r: Optional[int] = None
        best_dist = float("inf")
        denom = max(1, height - 1)
        for r in range(height):
            row_price = min_l + r * price_range / denom
            dist = abs(row_price - lp)
            if dist < best_dist:
                best_dist = dist
                best_r = r
        if best_r is not None and best_r not in label_rows:
            label_rows[best_r] = lp
    return label_rows
