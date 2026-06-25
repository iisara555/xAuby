"""Regime-aware trade policy layer.

Sits between the strategy signal and order execution: the regime classifier
already runs every tick but its output was never wired into the decision
path. This layer adjusts the action/risk based on the classified regime
without touching any strategy plugin.

Disabled by default (``regime_policy.enabled: false``) — test in simulate
mode first. Every adjustment is reported so the engine can emit an event and
keep the decision auditable in replay.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


PANIC_RISK_STATES = {"PANIC_RISK"}
PANIC_REGIMES = {"PANIC_SELL"}


@dataclass
class RegimePolicyDecision:
    """Outcome of applying the regime policy to one signal."""

    action: str
    risk_pct_multiplier: float = 1.0
    sl_atr_mult_delta: float = 0.0
    reasons: List[str] = field(default_factory=list)

    @property
    def adjusted(self) -> bool:
        return bool(self.reasons)


def regime_policy_enabled(cfg: Dict[str, Any]) -> bool:
    return bool(((cfg or {}).get("regime_policy") or {}).get("enabled", False))


def apply_regime_policy(gold_regime: Any, action: str) -> RegimePolicyDecision:
    """Adjust ``action``/risk for the current regime.

    Rules:
    * PANIC regime or panic risk state  -> block BUY entirely.
    * CHOPPY trend                      -> halve risk_pct for new entries.
    * EXTREME_EXPANSION volatility      -> widen initial SL by +0.5 ATR.

    SELL/HOLD pass through untouched — the policy only restrains entries,
    never blocks an exit.
    """
    decision = RegimePolicyDecision(action=action)
    if gold_regime is None:
        return decision

    regime = str(getattr(gold_regime, "regime", "") or "")
    risk_state = str(getattr(gold_regime, "risk_state", "") or "")
    trend_strength = str(getattr(gold_regime, "trend_strength", "") or "")
    volatility_state = str(getattr(gold_regime, "volatility_state", "") or "")

    if action == "BUY" and (regime in PANIC_REGIMES or risk_state in PANIC_RISK_STATES):
        decision.action = "HOLD"
        decision.reasons.append(
            f"BUY blocked: panic regime (regime={regime}, risk_state={risk_state})"
        )
        return decision

    if action == "BUY" and trend_strength == "CHOPPY":
        decision.risk_pct_multiplier = 0.5
        decision.reasons.append(
            f"risk_pct halved: choppy trend (trend_strength={trend_strength})"
        )

    if action == "BUY" and volatility_state == "EXTREME_EXPANSION":
        decision.sl_atr_mult_delta = 0.5
        decision.reasons.append(
            f"sl_atr_mult +0.5: extreme volatility expansion (volatility_state={volatility_state})"
        )

    return decision
