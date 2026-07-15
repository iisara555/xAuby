from __future__ import annotations

from copy import deepcopy
from typing import Any

MAX_CONFIGURED_PAIRS = 3
MAX_ACTIVE_PAIRS = 1

CATALOG: dict[str, Any] = {
    "exchanges": [
        {"id": "okx", "label": "OKX", "market_types": ["swap"], "live_certified": True}
    ],
    "presets": [
        {
            "id": "okx-xau-actionzone-v1",
            "label": "XAU ActionZone",
            "exchange_id": "okx",
            "market_type": "swap",
            "symbol": "XAUUSDT",
            "asset": "XAU",
            "strategy": "xauby_actionzone",
            "primary_timeframe": "4h",
            "confirm_timeframe": "1d",
            "live_certified": True,
        },
        {
            "id": "sim-btc-supertrend-v1",
            "label": "BTC Supertrend EMA200",
            "exchange_id": "okx",
            "market_type": "swap",
            "symbol": "BTCUSDT",
            "asset": "BTC",
            "strategy": "indicator_supertrend_ema200",
            "primary_timeframe": "4h",
            "confirm_timeframe": "1d",
            "live_certified": False,
        },
        {
            "id": "sim-sol-pullback-v1",
            "label": "SOL EMA Pullback",
            "exchange_id": "okx",
            "market_type": "swap",
            "symbol": "SOLUSDT",
            "asset": "SOL",
            "strategy": "sol_ema_pullback",
            "primary_timeframe": "1h",
            "confirm_timeframe": "4h",
            "live_certified": False,
        },
    ],
    "risk": {
        "risk_pct": {"default": 0.01, "min": 0.001, "max": 0.01},
        "max_position_per_trade_pct": {"default": 10.0, "min": 1.0, "max": 10.0},
        "max_daily_loss_pct": {"default": 3.0, "min": 1.0, "max": 3.0},
        "max_leverage": {"default": 1.0, "min": 1.0, "max": 2.0},
        "max_open_positions": {"default": 1, "min": 1, "max": 1},
        "stop_loss_required": True,
    },
    "limits": {"configured_pairs": MAX_CONFIGURED_PAIRS, "active_pairs": MAX_ACTIVE_PAIRS},
}


def public_catalog() -> dict[str, Any]:
    return deepcopy(CATALOG)


def preset_by_id(preset_id: str) -> dict[str, Any]:
    for preset in CATALOG["presets"]:
        if preset["id"] == preset_id:
            return deepcopy(preset)
    raise ValueError("unknown certified preset")


def safe_risk(values: dict[str, Any] | None = None) -> dict[str, Any]:
    supplied = values or {}
    result: dict[str, Any] = {}
    for key, bounds in CATALOG["risk"].items():
        if not isinstance(bounds, dict):
            result[key] = bounds
            continue
        value = float(supplied.get(key, bounds["default"]))
        if not float(bounds["min"]) <= value <= float(bounds["max"]):
            raise ValueError(f"{key} must be between {bounds['min']} and {bounds['max']}")
        result[key] = int(value) if key == "max_open_positions" else value
    return result


def validate_profile(preset_ids: list[str], active_preset_id: str, risk: dict[str, Any]) -> dict[str, Any]:
    ids = list(dict.fromkeys(str(item) for item in preset_ids))
    if not 1 <= len(ids) <= MAX_CONFIGURED_PAIRS:
        raise ValueError("configure between 1 and 3 certified presets")
    presets = [preset_by_id(item) for item in ids]
    if active_preset_id not in ids:
        raise ValueError("active preset must be one of the configured presets")
    return {"preset_ids": ids, "active_preset_id": active_preset_id, "presets": presets,
            "risk": safe_risk(risk)}
