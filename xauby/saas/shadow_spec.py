"""Materialize a credential-free, two-candidate shadow worker specification."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from xauby.saas.certification import config_fingerprint
from xauby.saas.strategy_pool import (
    candidate,
    normalize_pool,
    normalize_symbol,
    preset_for_candidate,
)
from xauby.utils.atomic_io import atomic_json_write

SHADOW_SPEC_VERSION = 1
SHADOW_FILL_MODEL = "signal_close_only_v1_research"


def _canonical_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_shadow_spec(
    pool: dict[str, Any],
    tenant_slug: str,
) -> dict[str, Any] | None:
    """Build one Champion/Challenger spec, or ``None`` until both exist."""
    normalize_pool(pool)
    champion_id = str(pool.get("champion_id") or "")
    champion = candidate(pool, champion_id)
    challengers = [
        item
        for item in pool.get("candidates", [])
        if isinstance(item, dict) and str(item.get("preset_id") or "") != champion_id
    ]
    if champion is None or not challengers:
        return None
    selected = [champion, challengers[0]]
    resolved: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for item in selected:
        preset = preset_for_candidate(str(item.get("preset_id") or ""))
        expected = str(item.get("certificate_config_fingerprint") or "")
        actual = config_fingerprint(preset)
        if not expected or expected != actual:
            raise ValueError("shadow candidate certificate fingerprint changed")
        resolved.append((item, preset))

    symbols = {str(preset.get("symbol") or "").upper() for _, preset in resolved}
    targets = {str(preset.get("target_id") or "") for _, preset in resolved}
    venues = {str(preset.get("exchange_id") or "") for _, preset in resolved}
    primary = {str(preset.get("primary_timeframe") or "").lower() for _, preset in resolved}
    regime = {str(preset.get("confirm_timeframe") or "").lower() for _, preset in resolved}
    if len(symbols) != 1 or len(targets) != 1 or len(venues) != 1:
        raise ValueError("shadow candidates must share symbol, target, and venue")
    if normalize_symbol(str(pool.get("symbol") or "")) != next(iter(symbols)):
        raise ValueError("shadow pool symbol does not match its candidates")
    if str(pool.get("target_id") or "") != next(iter(targets)):
        raise ValueError("shadow pool target does not match its candidates")
    if len(primary) != 1 or len(regime) != 1 or not next(iter(primary)):
        raise ValueError("shadow candidates must use comparable timeframes")

    candidates = []
    for _item, preset in resolved:
        candidates.append(
            {
                "candidate_id": str(preset["id"]),
                "role": "champion" if str(preset["id"]) == champion_id else "challenger",
                "strategy_name": str(preset.get("strategy") or ""),
                "strategy_config": dict(preset.get("execution_profile") or {}),
                "config_fingerprint": config_fingerprint(preset),
            }
        )

    identity = {
        "schema_version": SHADOW_SPEC_VERSION,
        "tenant": str(tenant_slug),
        "symbol": next(iter(symbols)),
        "target_id": next(iter(targets)),
        "venue": next(iter(venues)),
        "timeframe": next(iter(primary)),
        "regime_timeframe": next(iter(regime)),
        "fill_model": SHADOW_FILL_MODEL,
        "fees_pct": 0.05,
        "slippage_pct": 0.02,
        "initial_cash": 1_000.0,
        "allocation_fraction": 0.25,
        "max_bars": 420,
        "candidate_limit": 2,
        "research_only": True,
        "broker_access": False,
        "candidates": candidates,
    }
    spec_hash = _canonical_hash(identity)
    return {
        **identity,
        "spec_hash": spec_hash,
        "run_id": f"shadow-{identity['symbol'].lower()}-{spec_hash[:12]}",
    }


def materialize_shadow_spec(
    pool: dict[str, Any],
    tenant_slug: str,
    runtime_dir: Path,
) -> dict[str, Any] | None:
    """Persist an idempotent worker spec and a non-running prepared status."""
    spec = build_shadow_spec(pool, tenant_slug)
    if spec is None:
        return None
    shadow_dir = Path(runtime_dir) / "shadow" / str(spec["symbol"])
    shadow_dir.mkdir(parents=True, exist_ok=True, mode=0o770)
    spec_path = shadow_dir / "spec.json"
    current: dict[str, Any] = {}
    try:
        current = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if current.get("spec_hash") != spec["spec_hash"]:
        atomic_json_write(str(spec_path), spec, indent=2, mode=0o660)
        atomic_json_write(
            str(shadow_dir / "status.json"),
            {
                "schema_version": 1,
                "status": "prepared",
                "research_only": True,
                "broker_access": False,
                "tenant": tenant_slug,
                "symbol": spec["symbol"],
                "run_id": spec["run_id"],
                "spec_hash": spec["spec_hash"],
                "checked_at": time.time(),
                "candidate_ids": [item["candidate_id"] for item in spec["candidates"]],
                "candidates": {},
                "detail": "worker is prepared but has not evaluated a closed candle",
            },
            indent=2,
            mode=0o660,
        )
    return spec
