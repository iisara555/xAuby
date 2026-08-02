"""Deterministic state and gates for the per-pair strategy arena.

Certificates remain the source of truth for catalogue eligibility.  Forward-SIM
measurements are treated as immutable run records and a pool only keeps a
bounded index of those records.  The helpers in this module intentionally do
not place orders or change the live engine configuration.
"""
from __future__ import annotations

import math
import re
import time
from copy import deepcopy
from typing import Any

from xauby.saas.certification import config_fingerprint
from xauby.saas.catalog import preset_by_id

POOL_VERSION = 2
MAX_CANDIDATES = 4
MAX_EVALUATION_HISTORY = 90
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
    return result if math.isfinite(result) else float(default)


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
        "config_fingerprint": config_fingerprint(preset),
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
    comparable_champion: bool | None = None,
    require_provenance: bool = False,
) -> tuple[bool, list[str]]:
    """Evaluate the forward-SIM promotion gate.

    ``comparable_champion`` and ``require_provenance`` are opt-in so older
    callers that only use this pure helper keep their behaviour.  The SaaS API
    enables both: a production promotion must compare the same forward window
    and must reference a concrete, reviewable evaluator run.
    """
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
    if require_provenance:
        reasons.extend(evaluation_provenance_reasons(values))
    if comparable_champion is False and champion_score is not None:
        reasons.append("Champion needs a comparable forward-SIM evaluation")
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
        "certificate_config_fingerprint": config_fingerprint(preset),
        "joined_at": timestamp,
        "evaluation": baseline,
        "eligible_for_promotion": False,
        "eligibility_reasons": [baseline["note"]],
        "winning_evaluations": 0,
        "evaluation_history_count": 0,
    }


def new_pool(symbol: str, target_id: str, preset: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_symbol(symbol)
    return {
        "version": POOL_VERSION,
        "revision": 0,
        "symbol": normalized,
        "target_id": str(target_id),
        "policy": deepcopy(DEFAULT_POLICY),
        "champion_id": str(preset["id"]),
        "candidates": [candidate_from_preset(preset, role="champion")],
        "promotion": None,
        "history": [],
        "evaluation_history": [],
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
    if len(pool.get("candidates", [])) >= MAX_CANDIDATES:
        raise ValueError("a pair can have at most four strategies in the MVP")
    pool.setdefault("candidates", []).append(candidate_from_preset(preset, role="challenger"))
    return pool


def candidate(pool: dict[str, Any], preset_id: str) -> dict[str, Any] | None:
    return next(
        (item for item in pool.get("candidates", []) if item.get("preset_id") == preset_id),
        None,
    )


def normalize_pool(pool: dict[str, Any]) -> dict[str, Any]:
    """Upgrade an old JSON pool in memory without changing runtime config."""
    pool.setdefault("version", POOL_VERSION)
    pool["version"] = max(int(_number(pool.get("version"), POOL_VERSION)), POOL_VERSION)
    pool["revision"] = max(0, int(_number(pool.get("revision"), 0)))
    if not isinstance(pool.get("evaluation_history"), list):
        pool["evaluation_history"] = []
    else:
        # Keep a malformed/old pool from turning a read endpoint into an
        # unbounded response.  New writes are bounded by append_evaluation too.
        pool["evaluation_history"] = pool["evaluation_history"][-MAX_EVALUATION_HISTORY:]
    if not isinstance(pool.get("candidates"), list):
        pool["candidates"] = []
    for item in pool["candidates"]:
        if not isinstance(item, dict):
            continue
        item.setdefault("winning_evaluations", 0)
        item.setdefault("evaluation_history_count", 0)
        item.setdefault("eligible_for_promotion", False)
        item.setdefault("eligibility_reasons", [])
        try:
            preset = preset_by_id(str(item.get("preset_id") or ""))
            item.setdefault("certificate_config_fingerprint", config_fingerprint(preset))
        except ValueError:
            # Removed catalogue entries remain visible for audit, but cannot
            # acquire a new promotion-ready evaluation.
            pass
    return pool


def evaluation_provenance_reasons(metrics: dict[str, Any] | None) -> list[str]:
    """Return missing provenance fields that make a run ineligible.

    The endpoint may still store an incomplete reviewed result for diagnostics,
    but it cannot be used to promote a strategy until every identity field is
    present.  ``config_fingerprint`` identifies the exact strategy profile;
    the window/venue fields identify the common comparison sample.
    """
    values = metrics or {}
    required = {
        "run_id": "evaluator run id is required",
        "artifact_sha256": "evaluator artifact hash is required",
        "config_fingerprint": "strategy config fingerprint is required",
        "venue": "evaluation venue is required",
        "timeframe": "evaluation timeframe is required",
        "data_window_start": "evaluation window start is required",
        "data_window_end": "evaluation window end is required",
        "fees_pct": "evaluation fee model is required",
        "slippage_pct": "evaluation slippage model is required",
        "fill_model": "evaluation fill model is required",
    }
    def present(value: Any) -> bool:
        # Numeric zero is a valid fee/slippage model.  Using ``value or ""``
        # here would incorrectly turn a legitimate zero into a missing field.
        return value is not None and str(value).strip() != ""

    reasons = [message for key, message in required.items() if not present(values.get(key))]
    for key in ("fees_pct", "slippage_pct"):
        if key not in values or values.get(key) in (None, ""):
            continue
        parsed = _number(values.get(key), float("nan"))
        if not math.isfinite(parsed) or parsed < 0 or parsed > 100:
            reasons.append(f"{key} must be a finite non-negative number")
    artifact = str(values.get("artifact_sha256") or "").strip()
    if artifact and not re.fullmatch(r"[0-9a-fA-F]{64}", artifact):
        reasons.append("artifact_sha256 must be a SHA-256 hex digest")
    fingerprint = str(values.get("config_fingerprint") or "").strip()
    if fingerprint and not re.fullmatch(r"[0-9a-fA-F]{16,64}", fingerprint):
        reasons.append("config_fingerprint must be a hexadecimal digest")
    return reasons


def candidate_evaluation_provenance_reasons(
    candidate_item: dict[str, Any] | None,
    metrics: dict[str, Any] | None,
) -> list[str]:
    """Add the candidate-to-certificate fingerprint binding to base checks."""
    values = metrics or {}
    reasons = evaluation_provenance_reasons(values)
    expected = str(
        (candidate_item or {}).get("certificate_config_fingerprint")
        or ((candidate_item or {}).get("evaluation") or {}).get("config_fingerprint")
        or ""
    ).strip()
    supplied = str(values.get("config_fingerprint") or "").strip()
    if expected and supplied and expected != supplied:
        reasons.append("evaluation config fingerprint does not match the candidate certificate")
    return reasons


def evaluations_comparable(
    challenger: dict[str, Any] | None,
    champion: dict[str, Any] | None,
) -> bool:
    """Whether two forward-SIM records share the same comparison sample."""
    left, right = challenger or {}, champion or {}
    keys = (
        "source",
        "venue",
        "timeframe",
        "data_window_start",
        "data_window_end",
        "fees_pct",
        "slippage_pct",
        "fill_model",
    )
    if any(left.get(key) is None or str(left.get(key)).strip() == "" for key in keys):
        return False
    if any(right.get(key) is None or str(right.get(key)).strip() == "" for key in keys):
        return False
    if left.get("source") != "forward_sim" or right.get("source") != "forward_sim":
        return False
    return all(str(left.get(key)) == str(right.get(key)) for key in keys[1:])


def _winning_streak(
    pool: dict[str, Any], candidate_id: str, champion_id: str | None
) -> int:
    streak = 0
    for record in reversed(pool.get("evaluation_history", [])):
        if not isinstance(record, dict) or str(record.get("candidate_id")) != str(candidate_id):
            continue
        if str(record.get("champion_id") or "") != str(champion_id or ""):
            break
        if not bool(record.get("base_gate_passed")):
            break
        streak += 1
    return streak


def append_evaluation(
    pool: dict[str, Any],
    candidate_id: str,
    metrics: dict[str, Any],
    *,
    now: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Append one idempotent evaluator result and derive its promotion state.

    Returns ``(pool, record, inserted)``.  Replaying the same ``run_id`` for a
    candidate is a no-op, which makes worker retries safe.
    """
    normalize_pool(pool)
    item = candidate(pool, candidate_id)
    if item is None:
        raise ValueError("strategy candidate not found")
    values = dict(metrics or {})
    run_id = str(values.get("run_id") or "").strip()
    if run_id:
        for existing in pool.get("evaluation_history", []):
            if (
                isinstance(existing, dict)
                and str(existing.get("candidate_id")) == str(candidate_id)
                and str(existing.get("run_id") or "") == run_id
            ):
                return pool, deepcopy(existing), False

    timestamp = float(now if now is not None else time.time())
    values["source"] = "forward_sim"
    values["evaluated_at"] = timestamp
    values["score"] = score_metrics(values)
    champion_id = str(pool.get("champion_id") or "")
    champion = candidate(pool, champion_id)
    champion_evaluation = (champion or {}).get("evaluation") or {}
    champion_score = None
    comparable = None
    if champion_id and champion_id != str(candidate_id):
        champion_score = _number(champion_evaluation.get("score"), -9999.0)
        comparable = evaluations_comparable(values, champion_evaluation)

    threshold = int((pool.get("policy") or DEFAULT_POLICY).get("winning_evaluations", 3))
    _, base_reasons = promotion_eligibility(
        values,
        policy=pool.get("policy"),
        champion_score=champion_score,
        winning_evaluations=threshold,
        comparable_champion=comparable,
        require_provenance=True,
    )
    provenance_reasons = candidate_evaluation_provenance_reasons(item, values)
    for reason in provenance_reasons:
        if reason not in base_reasons:
            base_reasons.append(reason)
    record = {
        **values,
        "candidate_id": str(candidate_id),
        "champion_id": champion_id or None,
        "base_gate_passed": not base_reasons,
        "comparable_champion": comparable,
    }
    pool.setdefault("evaluation_history", []).append(record)
    pool["evaluation_history"] = pool["evaluation_history"][-MAX_EVALUATION_HISTORY:]
    streak = _winning_streak(pool, str(candidate_id), champion_id or None)
    eligible, reasons = promotion_eligibility(
        values,
        policy=pool.get("policy"),
        champion_score=champion_score,
        winning_evaluations=streak,
        comparable_champion=comparable,
        require_provenance=True,
    )
    for reason in provenance_reasons:
        if reason not in reasons:
            reasons.append(reason)
    item["evaluation"] = values
    item["winning_evaluations"] = streak
    item["evaluation_history_count"] = sum(
        1 for row in pool["evaluation_history"]
        if isinstance(row, dict) and str(row.get("candidate_id")) == str(candidate_id)
    )
    item["eligible_for_promotion"] = eligible
    item["eligibility_reasons"] = reasons
    item["status"] = "eligible" if eligible else "warming"
    record["provenance_valid"] = not provenance_reasons
    values["provenance_valid"] = not provenance_reasons
    record["eligible_for_promotion"] = eligible
    record["eligibility_reasons"] = reasons
    record["winning_evaluations"] = streak
    return pool, deepcopy(record), True
