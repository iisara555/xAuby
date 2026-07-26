"""Engine-managed exit rules shared by live trading and backtest/replay.

Keeping the fixed take-profit math here (instead of inside the engine) means the
live order path AND the backtest ``PositionSimulator`` resolve it identically, so
a strategy's backtest reflects the TP exits that actually happen live. Putting it
in an engine-only block would break that parity (see CLAUDE.md).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Per-strategy default fixed take-profit (% above entry). 0 / absent = disabled.
# Explicit ``fixed_tp_pct`` in strategy config overrides these.
DEFAULT_FIXED_TP_PCT_BY_STRATEGY: Dict[str, float] = {
    "bbrsi_mean_reversion": 1.2,
    "rsi2_meanrev": 0.8,
    "btc_ema_pullback": 1.5,
    "ict_lite_strategy": 2.0,
}


def resolve_fixed_tp_pct(
    strategy_name: Optional[str],
    strategy_cfg: Optional[Dict[str, Any]],
) -> float:
    """Resolve the fixed take-profit percentage for a strategy (0 = disabled)."""
    default_pct = DEFAULT_FIXED_TP_PCT_BY_STRATEGY.get(str(strategy_name or ""), 0.0)
    cfg = strategy_cfg or {}
    try:
        raw = cfg.get("fixed_tp_pct", default_pct)
        pct = float(raw if raw is not None else default_pct)
    except (TypeError, ValueError):
        pct = default_pct
    return pct if pct > 0 else 0.0


def fixed_take_profit_price(
    entry_price: float,
    strategy_name: Optional[str],
    strategy_cfg: Optional[Dict[str, Any]],
) -> float:
    """Engine-managed fixed TP price for an entry, or 0 when disabled."""
    if entry_price <= 0:
        return 0.0
    pct = resolve_fixed_tp_pct(strategy_name, strategy_cfg)
    if pct <= 0:
        return 0.0
    return entry_price * (1.0 + pct / 100.0)


# --------------------------------------------------------------------------- #
# Minimal ROI ladder (freqtrade-style, time-decaying take-profit)             #
# --------------------------------------------------------------------------- #

def resolve_minimal_roi(
    strategy_cfg: Optional[Dict[str, Any]],
) -> List[Tuple[float, float]]:
    """Parse the strategy's ``minimal_roi`` table into a sorted step list.

    Config shape (freqtrade convention: keys = position age in MINUTES,
    values = required net ROI in PERCENT of entry price — percent, not
    fraction, to match ``fixed_tp_pct`` in this codebase)::

        minimal_roi:
          "0": 10.0      # from entry: take profit at +10%
          "2880": 5.0    # after 2 days: settle for +5%
          "5760": 2.5    # after 4 days: settle for +2.5%

    Returns ``[(age_minutes, roi_pct), ...]`` sorted by age ascending; an
    empty list disables the ladder. Steps with roi <= 0 are dropped (a "0%
    ROI" step would exit every trade instantly on noise; use a small positive
    value to emulate time-based flat exits).
    """
    cfg = strategy_cfg or {}
    raw = cfg.get("minimal_roi")
    if not isinstance(raw, dict) or not raw:
        return []
    steps: List[Tuple[float, float]] = []
    for key, value in raw.items():
        try:
            age = float(key)
            roi = float(value)
        except (TypeError, ValueError):
            continue
        if age < 0 or roi <= 0:
            continue
        steps.append((age, roi))
    steps.sort(key=lambda s: s[0])
    return steps


def resolve_partial_tp(
    strategy_cfg: Optional[Dict[str, Any]],
) -> Tuple[float, float]:
    """Resolve the partial take-profit as ``(trigger_pct, fraction)``.

    ``partial_tp_pct`` > 0 enables it: when unrealized profit reaches that
    percent of entry, ``partial_tp_fraction`` of the position (0 < f < 1,
    default 0.5) is closed once and the remainder rides to the normal
    strategy exit. Returns (0.0, 0.0) when disabled or misconfigured.
    """
    cfg = strategy_cfg or {}
    try:
        pct = float(cfg.get("partial_tp_pct", 0.0) or 0.0)
        fraction = float(cfg.get("partial_tp_fraction", 0.5) or 0.5)
    except (TypeError, ValueError):
        return (0.0, 0.0)
    if pct <= 0 or not (0.0 < fraction < 1.0):
        return (0.0, 0.0)
    return (pct, fraction)


def minimal_roi_pct(
    steps: List[Tuple[float, float]],
    age_minutes: float,
) -> float:
    """Active ROI threshold (percent) for a position of ``age_minutes``.

    The active step is the one with the largest age <= position age
    (freqtrade semantics). 0.0 = no step active yet / ladder disabled.
    """
    active = 0.0
    for age, roi in steps:
        if age_minutes >= age:
            active = roi
        else:
            break
    return active


# --------------------------------------------------------------------------- #
# Cross-key validation                                                        #
# --------------------------------------------------------------------------- #

def validate_exit_config(
    strategy_cfg: Optional[Dict[str, Any]],
    *,
    symbol: str = "",
) -> None:
    """Refuse startup when ``minimal_roi`` makes ``partial_tp_pct`` unreachable.

    Both keys live in the same strategy config but are consumed by different
    stages of the exit pipeline, and the ROI ladder always wins: the engine
    runs ``_apply_minimal_roi_exit`` before ``_maybe_take_partial_tp``, whose
    first guard is ``action == "SELL"`` (xauby/engine/loop.py) — and the
    backtest ``PositionSimulator`` returns straight out of the bar after a
    minimal-ROI close (xauby/observability/replay.py). So when the ladder's
    first rung sits at or below the partial-TP trigger, any move that reaches
    the trigger has already crossed the rung and been closed in full. The
    partial can never fire, on either path.

    The July 2026 XAU config had exactly this shape: ``minimal_roi {"0": 8.0}``
    with ``partial_tp_pct: 12.0``, so the documented "bank half at +12%"
    behaviour never occurred once, and the SaaS catalog advertised it to
    tenants. Silent dead config is worse than a refused start.

    Only a rung active **from entry** (age 0) makes the partial unreachable. A
    ladder whose first rung starts later — ``{"1440": 5.0}`` — leaves a real
    window in which the partial can fire, which is a legitimate "bank half if it
    moons early, otherwise settle" design and is left alone. Deciding whether
    that window is *usefully* long is a strategy question, not a startup check.

    Raises ValueError when the rung active at entry is <= ``partial_tp_pct``.
    """
    cfg = strategy_cfg or {}
    trigger_pct, _fraction = resolve_partial_tp(cfg)
    if trigger_pct <= 0:
        return
    steps = resolve_minimal_roi(cfg)
    if not steps:
        return

    entry_rung_pct = minimal_roi_pct(steps, 0.0)
    if entry_rung_pct <= 0 or entry_rung_pct > trigger_pct:
        return

    where = f"{symbol}: " if symbol else ""
    raise ValueError(
        f"{where}partial_tp_pct={trigger_pct:g}% is unreachable — the "
        f"minimal_roi ladder closes the whole position at {entry_rung_pct:g}% "
        "from entry, and a full exit always wins the tick. Either remove "
        "partial_tp_pct/partial_tp_fraction, or raise the ladder's entry rung "
        "above the partial-TP trigger (note: changing the ladder changes the "
        "strategy and re-opens certification)."
    )
