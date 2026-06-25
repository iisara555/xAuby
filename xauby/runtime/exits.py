"""Engine-managed exit rules shared by live trading and backtest/replay.

Keeping the fixed take-profit math here (instead of inside the engine) means the
live order path AND the backtest ``PositionSimulator`` resolve it identically, so
a strategy's backtest reflects the TP exits that actually happen live. Putting it
in an engine-only block would break that parity (see CLAUDE.md).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

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
