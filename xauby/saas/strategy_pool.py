"""Small, deterministic helpers for the per-pair strategy arena.

The first version deliberately keeps the pool state small.  Certificates remain
the source of truth for eligibility; a pool only records which certified
presets are competing for one symbol and the latest forward-SIM measurement.
"""
from __future__ import annotations

import re
import time
from copy import deepcopy
from typing import Any

from xauby.saas.catalog import preset_by_id

POOL_VERSION = 1
DEFAULT_POLICY: dict[str, Any] = {
    "min_forward_days": 30,
    "min_forward_trades": 20,
    "min_profit_factor": 1.10,
    "max_drawdown_pct": 25.0,
    "score_margin": 10.0,
    "winning_evaluations": 3,
}


def normalize_symbol(symbol: str) -> str:
    """Use the same compact symbol spelling as the runtime whitelist."""
    return re.sub(r"[^A-Z0-9]", "", str(symbol or "").upper())


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    return result if result == result else float(default)


def _score_label_pf(preset: dict[str, Any]) -> float:
    label = str((preset.get("backtest") or {}).get("score_label") or "")
    match = re.search(r"PF\s*([0-9]+(?:\.[0-9]+)?)", label, re.IGNORECASE)
    return _number(match.group(1), 0.0) if match else 0.0


def certificate_metrics(preset: dict[str, Any]) -> dict[str, Any]:
    """Return comparable baseline metrics exposed by a catalog certificate.

    These are research evidence only.  They are useful for the Arena card but
    do not satisfy forward-SIM promotion gates.
    """
    evidence = preset.get("backtest") or {}
    # The public catalog intentionally keeps the certificate's gate details
    # private.  The evidence block is still safe to show and enough to provide
    # a deterministic baseline score.
    pf = _score_label_pf(preset)
    net = _number(evidence.get("net_return_pct"), 0.0)
    drawdown = abs(_number(evidence.get("max_drawdown_pct"), 0.0))
    trades = int(_number(evidence.get("trades"), 0))
    score = score_metrics(
        {
            "profit_factor": pf,
            "net_return_pct": net,
            "max_drawdown_pct": drawdown,
            "trades": trades,
        }
    )
    return {
        "source": "certificate",
        "profit_factor": pf,
        "net_return_pct": net,
        "max_drawdown_pct": drawdown,
        "trades": trades,
        "score": score,
        "note": "Certificate evidence is a baseline; forward SIM is required for promotion.",
    }


def score_metrics(metrics: dict[str, Any]) -> float:
    """Reuse the existing selector score for a small, explainable MVP score."""
    pf = _number(metrics.get("profit_factor"))
    net = _number(metrics.get("net_return_pct"))
    drawdown = abs(_number(metrics.get("max_drawdown_pct")))
    trades = int(_number(metrics.get("trades", metrics.get("total_trades")), 0))
    if trades <= 0:
        return -9999.0
    return round((pf * 100.0) + net - (drawdown * 0.5) + min(trades, 30) * 0.1, 2)


def promotion_eligibility(
    metrics: dict[str, Any] | None,
    *,
    policy: dict[str, Any] | None = None,
    champion_score: float | None = None,
    winning_evaluations: int = 0,
) -> tuple[bool, list[str]]:
    """Evaluate the intentionally small forward-SIM promotion gate."""
    current = {**DEFAULT_POLICY, **(policy or {})}
    values = metrics or {}
    reasons: list[str] = []
    if str(values.get("source") or "") != "forward_sim":
        reasons.append("forward SIM evaluation is required")
    if int(_number(values.get("forward_days", values.get("days")), 0)) < int(current["min_forward_days"]):
        reasons.append(f"needs {int(current['min_forward_days'])} forward-SIM days")
    if int(_number(values.get("trades", values.get("total_trades")), 0)) < int(current["min_forward_trades"]):
        reasons.append(f"needs {int(current['min_forward_trades'])} forward-SIM trades")
    if _number(values.get("profit_factor")) < float(current["min_profit_factor"]):
        reasons.append(f"profit factor must be at least {float(current['min_profit_factor']):.2f}")
    if abs(_number(values.get("max_drawdown_pct"))) > float(current["max_drawdown_pct"]):
        reasons.append(f"drawdown must stay below {float(current['max_drawdown_pct']):.1f}%")
    score = _number(values.get("score"), score_metrics(values))
    if champion_score is not None and score < float(champion_score) + float(current["score_margin"]):
        reasons.append(f"score must beat Champion by {float(current['score_margin']):.1f}")
    if winning_evaluations < int(current["winning_evaluations"]):
        reasons.append(f"must lead for {int(current['winning_evaluations'])} evaluations")
    return not reasons, reasons


def candidate_from_preset(
    preset: dict[str, Any], *, role: str, now: float | None = None
) -> dict[str, Any]:
    timestamp = float(now if now is not None else time.time())
    baseline = certificate_metrics(preset)
    return {
        "preset_id": str(preset["id"]),
        "label": str(preset.get("label") or preset["id"]),
        "symbol": normalize_symbol(str(preset.get("symbol") or "")),
        "target_id": str(preset.get("target_id") or ""),
        "strategy": str(preset.get("strategy") or ""),
        "role": role,
        "mode": "live" if role == "champion" and preset.get("live_certified") else "shadow",
        "status": "active" if role == "champion" else "warming",
        "certification_status": str(preset.get("certification_status") or "not_assessed"),
        "live_certified": bool(preset.get("live_certified")),
        "joined_at": timestamp,
        "evaluation": baseline,
        "eligible_for_promotion": False,
        "eligibility_reasons": [baseline["note"]],
        "winning_evaluations": 0,
    }


def new_pool(symbol: str, target_id: str, preset: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_symbol(symbol)
    return {
        "version": POOL_VERSION,
        "symbol": normalized,
        "target_id": str(target_id),
        "policy": deepcopy(DEFAULT_POLICY),
        "champion_id": str(preset["id"]),
        "candidates": [candidate_from_preset(preset, role="champion")],
        "promotion": None,
        "history": [],
    }


def preset_for_candidate(preset_id: str) -> dict[str, Any]:
    preset = preset_by_id(str(preset_id))
    if str(preset.get("certification_status")) != "certified":
        raise ValueError("only certified strategies can enter a production pool")
    return preset


def append_candidate(pool: dict[str, Any], preset: dict[str, Any]) -> dict[str, Any]:
    if normalize_symbol(str(preset.get("symbol") or "")) != normalize_symbol(pool.get("symbol", "")):
        raise ValueError("strategy symbol does not match this pair pool")
    if str(preset.get("target_id")) != str(pool.get("target_id")):
        raise ValueError("strategy target does not match this pair pool")
    if any(item.get("preset_id") == preset.get("id") for item in pool.get("candidates", [])):
        raise ValueError("strategy is already in this pair pool")
    if len(pool.get("candidates", [])) >= 4:
        raise ValueError("a pair can have at most four strategies in the MVP")
    pool.setdefault("candidates", []).append(candidate_from_preset(preset, role="challenger"))
    return pool


def candidate(pool: dict[str, Any], preset_id: str) -> dict[str, Any] | None:
    return next(
        (item for item in pool.get("candidates", []) if item.get("preset_id") == preset_id),
        None,
    )
