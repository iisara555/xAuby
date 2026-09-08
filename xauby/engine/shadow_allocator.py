"""Pure portfolio-risk allocation for shadow strategy sleeves.

This module has no exchange, broker, order, or credential dependency.  It
turns independently observed candidate signals into research-only risk budgets
so several strategies and symbols can be compared under one portfolio heat
limit before any live execution design is considered.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence


_ZERO_RISK_REGIMES = frozenset({"NO_TRADE", "PANIC", "CRISIS", "EXTREME_RISK"})


def regime_risk_multiplier(
    regime: str | None,
    *,
    overrides: Mapping[str, float] | None = None,
) -> float:
    """Map a regime to risk only; it never predicts trade direction."""
    label = str(regime or "UNKNOWN").strip().upper()
    if overrides and label in overrides:
        value = float(overrides[label])
    elif label in _ZERO_RISK_REGIMES or "NO_TRADE" in label or "PANIC" in label:
        value = 0.0
    elif "TRANSITION" in label or "HIGH_VOL" in label:
        value = 0.5
    elif any(token in label for token in ("SIDEWAYS", "CHOP", "RANGE")):
        value = 0.65
    elif "TREND" in label or "ACCUMULATION" in label:
        value = 1.0
    else:
        value = 0.5
    if not math.isfinite(value) or not 0.0 <= value <= 1.5:
        raise ValueError("regime risk multiplier must be between 0 and 1.5")
    return value


@dataclass(frozen=True)
class ShadowAllocationInput:
    candidate_id: str
    symbol: str
    side: str | None
    base_risk_budget: float
    realized_vol: float
    target_vol: float
    regime: str = "UNKNOWN"
    regime_multiplier: float | None = None
    evidence_score: float = 1.0
    signal_confidence: float = 1.0
    healthy: bool = True


@dataclass(frozen=True)
class ShadowAllocatorConfig:
    max_portfolio_heat: float = 0.02
    max_symbol_heat: float = 0.0125
    min_conflict_dominance: float = 0.25
    min_vol_multiplier: float = 0.25
    max_vol_multiplier: float = 1.5


@dataclass(frozen=True)
class CandidateRiskBudget:
    candidate_id: str
    symbol: str
    side: str
    raw_risk_budget: float
    risk_budget: float
    volatility_multiplier: float
    regime_multiplier: float
    blocked_reason: str = ""


@dataclass(frozen=True)
class SymbolRiskDecision:
    symbol: str
    decision: str
    long_risk_budget: float
    short_risk_budget: float
    net_risk_budget: float
    gross_risk_budget: float
    conflict: bool
    dominance: float


@dataclass(frozen=True)
class ShadowAllocationDecision:
    candidates: tuple[CandidateRiskBudget, ...]
    symbols: tuple[SymbolRiskDecision, ...]
    total_gross_heat: float
    total_net_heat: float
    research_only: bool = True
    executable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [asdict(item) for item in self.candidates],
            "symbols": [asdict(item) for item in self.symbols],
            "total_gross_heat": self.total_gross_heat,
            "total_net_heat": self.total_net_heat,
            "research_only": self.research_only,
            "executable": self.executable,
        }


def _finite(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _validated_config(config: ShadowAllocatorConfig) -> ShadowAllocatorConfig:
    portfolio = _finite(config.max_portfolio_heat, "max_portfolio_heat")
    symbol = _finite(config.max_symbol_heat, "max_symbol_heat")
    dominance = _finite(config.min_conflict_dominance, "min_conflict_dominance")
    min_vol = _finite(config.min_vol_multiplier, "min_vol_multiplier")
    max_vol = _finite(config.max_vol_multiplier, "max_vol_multiplier")
    if not 0 < portfolio <= 1:
        raise ValueError("max_portfolio_heat must be in (0, 1]")
    if not 0 < symbol <= portfolio:
        raise ValueError("max_symbol_heat must be in (0, max_portfolio_heat]")
    if not 0 <= dominance <= 1:
        raise ValueError("min_conflict_dominance must be in [0, 1]")
    if not 0 < min_vol <= max_vol:
        raise ValueError("volatility multiplier bounds are invalid")
    return config


def allocate_shadow_risk(
    candidates: Sequence[ShadowAllocationInput],
    *,
    config: ShadowAllocatorConfig | None = None,
    regime_overrides: Mapping[str, float] | None = None,
) -> ShadowAllocationDecision:
    """Allocate candidate risk with vol scaling, caps, and conflict netting.

    Risk is never scaled upward merely to consume a cap.  Opposing sleeves stay
    visible in candidate budgets, while each symbol decision is netted and
    abstains when neither side reaches the configured dominance threshold.
    """
    cfg = _validated_config(config or ShadowAllocatorConfig())
    seen: set[str] = set()
    raw_rows: list[CandidateRiskBudget] = []
    for candidate in candidates:
        candidate_id = str(candidate.candidate_id or "").strip()
        symbol = str(candidate.symbol or "").upper().replace("_", "")
        side = str(candidate.side or "FLAT").upper()
        if not candidate_id or not symbol:
            raise ValueError("candidate id and symbol are required")
        if candidate_id in seen:
            raise ValueError(f"duplicate candidate id: {candidate_id}")
        seen.add(candidate_id)
        if side not in {"LONG", "SHORT", "FLAT", "HOLD"}:
            raise ValueError(f"invalid candidate side: {side}")
        base_risk = _finite(candidate.base_risk_budget, "base_risk_budget")
        evidence = _finite(candidate.evidence_score, "evidence_score")
        confidence = _finite(candidate.signal_confidence, "signal_confidence")
        realized_vol = _finite(candidate.realized_vol, "realized_vol")
        target_vol = _finite(candidate.target_vol, "target_vol")
        if not 0 <= base_risk <= 1:
            raise ValueError("base_risk_budget must be in [0, 1]")
        if not 0 <= evidence <= 1 or not 0 <= confidence <= 1:
            raise ValueError("evidence and confidence scores must be in [0, 1]")
        if realized_vol <= 0 or target_vol <= 0:
            raise ValueError("realized_vol and target_vol must be positive")
        multiplier = (
            regime_risk_multiplier(candidate.regime, overrides=regime_overrides)
            if candidate.regime_multiplier is None
            else _finite(candidate.regime_multiplier, "regime_multiplier")
        )
        if not 0 <= multiplier <= 1.5:
            raise ValueError("regime_multiplier must be between 0 and 1.5")
        vol_multiplier = min(
            cfg.max_vol_multiplier,
            max(cfg.min_vol_multiplier, target_vol / realized_vol),
        )
        blocked_reason = ""
        if not candidate.healthy:
            blocked_reason = "candidate_unhealthy"
        elif side in {"FLAT", "HOLD"}:
            blocked_reason = "no_directional_signal"
        elif multiplier == 0:
            blocked_reason = "regime_blocks_risk"
        raw_budget = (
            0.0
            if blocked_reason
            else base_risk * evidence * confidence * multiplier * vol_multiplier
        )
        raw_rows.append(
            CandidateRiskBudget(
                candidate_id=candidate_id,
                symbol=symbol,
                side="FLAT" if side == "HOLD" else side,
                raw_risk_budget=raw_budget,
                risk_budget=raw_budget,
                volatility_multiplier=vol_multiplier,
                regime_multiplier=multiplier,
                blocked_reason=blocked_reason,
            )
        )

    total_raw = sum(item.raw_risk_budget for item in raw_rows)
    portfolio_scale = min(1.0, cfg.max_portfolio_heat / total_raw) if total_raw else 1.0
    portfolio_rows = [
        CandidateRiskBudget(
            **{
                **asdict(item),
                "risk_budget": item.raw_risk_budget * portfolio_scale,
            }
        )
        for item in raw_rows
    ]

    by_symbol: dict[str, list[CandidateRiskBudget]] = {}
    for item in portfolio_rows:
        by_symbol.setdefault(item.symbol, []).append(item)
    final_rows: list[CandidateRiskBudget] = []
    for symbol in sorted(by_symbol):
        rows = by_symbol[symbol]
        symbol_gross = sum(item.risk_budget for item in rows)
        symbol_scale = min(1.0, cfg.max_symbol_heat / symbol_gross) if symbol_gross else 1.0
        for item in rows:
            final_rows.append(
                CandidateRiskBudget(
                    **{
                        **asdict(item),
                        "risk_budget": item.risk_budget * symbol_scale,
                    }
                )
            )

    symbol_decisions: list[SymbolRiskDecision] = []
    for symbol in sorted(by_symbol):
        rows = [item for item in final_rows if item.symbol == symbol]
        long_risk = sum(item.risk_budget for item in rows if item.side == "LONG")
        short_risk = sum(item.risk_budget for item in rows if item.side == "SHORT")
        gross = long_risk + short_risk
        conflict = long_risk > 0 and short_risk > 0
        dominance = abs(long_risk - short_risk) / gross if gross else 0.0
        if gross == 0 or (conflict and dominance < cfg.min_conflict_dominance):
            decision = "FLAT"
            net = 0.0
        elif long_risk > short_risk:
            decision = "LONG"
            net = long_risk - short_risk
        else:
            decision = "SHORT"
            net = short_risk - long_risk
        symbol_decisions.append(
            SymbolRiskDecision(
                symbol=symbol,
                decision=decision,
                long_risk_budget=long_risk,
                short_risk_budget=short_risk,
                net_risk_budget=net,
                gross_risk_budget=gross,
                conflict=conflict,
                dominance=dominance,
            )
        )

    ordered_candidates = tuple(sorted(final_rows, key=lambda item: item.candidate_id))
    decisions = tuple(symbol_decisions)
    return ShadowAllocationDecision(
        candidates=ordered_candidates,
        symbols=decisions,
        total_gross_heat=sum(item.risk_budget for item in ordered_candidates),
        total_net_heat=sum(item.net_risk_budget for item in decisions),
    )
