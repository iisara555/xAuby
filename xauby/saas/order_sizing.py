"""Pure sizing math for manual order previews.

Kept out of ``app.py`` so it can be unit-tested without a FastAPI test
client (that dependency is not installed in every environment this repo
runs in).

Audit finding F-3 (docs/audit_system_2026-07-21.md): the manual-order
preview risk-sized every non-CDC-pure pair off a synthetic ``mark * 2%``
stop distance, while the certified live strategy (e.g. supertrend_ema200 at
3.0x ATR) sizes and stops its own positions off the pair's real ATR. On BTC
that made a manual preview ~1.5x larger than the engine would open for the
same signal — still bounded by the allocation cap, but not size-parity with
the strategy. This module sizes from the pair's live ATR when the runtime
snapshot has one, falling back to the fixed percent only when it doesn't.
"""
from __future__ import annotations

from typing import Any, Mapping, Tuple

# Strategy plugins name their ATR indicator differently
# (supertrend_ema200 -> "atr", xauby_actionzone -> "atr_4h"). Check the known
# names in order rather than coupling this module to any one strategy.
ATR_INDICATOR_KEYS: Tuple[str, ...] = ("atr", "atr_4h", "atr_1h", "atr_1d")

DEFAULT_SL_ATR_MULT = 2.5  # supertrend_ema200's own default_config fallback
DEFAULT_FALLBACK_PCT = 0.02  # prior synthetic stop distance, kept as last resort


def resolve_pair_atr(indicators: Mapping[str, Any] | None) -> float:
    """Best-effort live ATR from a runtime snapshot's indicators dict.

    Returns 0.0 when ``indicators`` is missing or carries no positive ATR
    under any known key — callers must treat 0.0 as "unavailable", not zero
    volatility.
    """
    if not isinstance(indicators, Mapping):
        return 0.0
    for key in ATR_INDICATOR_KEYS:
        try:
            value = float(indicators.get(key) or 0.0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def risk_based_stop_distance(
    *,
    mark_price: float,
    atr: float,
    sl_atr_mult: float = DEFAULT_SL_ATR_MULT,
    fallback_pct: float = DEFAULT_FALLBACK_PCT,
) -> Tuple[float, str]:
    """Stop-loss distance (price units) for risk-based manual sizing.

    Returns ``(distance, basis)`` where ``basis`` is ``"atr"`` when a live
    ATR was used (matching how the engine itself sizes and stops the
    position) or ``"fixed_pct"`` when no ATR was available and the previous
    fixed-percent heuristic was used instead — callers can surface the basis
    so a fallback preview is never silently presented as strategy-matched.
    """
    if atr > 0 and sl_atr_mult > 0:
        return atr * sl_atr_mult, "atr"
    return max(0.0, mark_price) * fallback_pct, "fixed_pct"
