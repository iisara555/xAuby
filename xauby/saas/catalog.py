from __future__ import annotations

from copy import deepcopy
from typing import Any

MAX_CONFIGURED_PAIRS = 1
MAX_ACTIVE_PAIRS = 1

TARGETS = [
    {
        "id": "okx-swap",
        "exchange_id": "okx",
        "label": "OKX Perpetual",
        "market_type": "swap",
        "credential_fields": ["api_key", "api_secret", "passphrase"],
        "live_certified": True,
        "manual_allowed_sides": ["long", "short"],
        "manual_long_live_certified": True,
        "manual_short_live_certified": False,
    },
    {
        "id": "binance-global-futures",
        "exchange_id": "binanceusdm",
        "label": "Binance Global Futures",
        "market_type": "swap",
        "credential_fields": ["api_key", "api_secret"],
        "live_certified": True,
        "manual_allowed_sides": ["long", "short"],
        "manual_long_live_certified": True,
        "manual_short_live_certified": False,
    },
    {
        "id": "binance-th-spot",
        "exchange_id": "binance_th",
        "label": "Binance TH Spot",
        "market_type": "spot",
        "credential_fields": ["api_key", "api_secret"],
        "live_certified": True,
        "manual_allowed_sides": ["long"],
        "manual_long_live_certified": True,
        "manual_short_live_certified": False,
    },
]

PRESETS = [
    {
        "id": "okx-xau-actionzone-v1",
        "target_id": "okx-swap",
        "label": "XAU ActionZone",
        "exchange_id": "okx",
        "market_type": "swap",
        "symbol": "XAUUSDT",
        "asset": "XAU",
        "strategy": "xauby_actionzone",
        "primary_timeframe": "4h",
        "confirm_timeframe": "1d",
        "allowed_sides": ["long"],
        "max_leverage": 1,
        "live_certified": True,
    },
    {
        "id": "binance-btc-supertrend-v1",
        "target_id": "binance-global-futures",
        "label": "BTC Supertrend EMA200",
        "exchange_id": "binanceusdm",
        "market_type": "swap",
        "symbol": "BTCUSDT",
        "asset": "BTC",
        "strategy": "supertrend_ema200",
        "primary_timeframe": "1h",
        "confirm_timeframe": "4h",
        "allowed_sides": ["long"],
        "max_leverage": 1,
        "live_certified": True,
    },
    {
        "id": "binance-th-btc-supertrend-v1",
        "target_id": "binance-th-spot",
        "label": "BTC/THB Supertrend EMA200",
        "exchange_id": "binance_th",
        "market_type": "spot",
        "symbol": "BTCTHB",
        "asset": "BTC",
        "strategy": "supertrend_ema200",
        "primary_timeframe": "1h",
        "confirm_timeframe": "4h",
        "allowed_sides": ["long"],
        "max_leverage": 1,
        "live_certified": True,
    },
]

CATALOG: dict[str, Any] = {
    "targets": TARGETS,
    "exchanges": TARGETS,
    "presets": PRESETS,
    "risk": {
        "risk_pct": {"default": 0.01, "min": 0.001, "max": 0.01},
        "max_position_per_trade_pct": {"default": 10.0, "min": 1.0, "max": 10.0},
        "max_daily_loss_pct": {"default": 3.0, "min": 1.0, "max": 3.0},
        "max_leverage": {"default": 1.0, "min": 1.0, "max": 1.0},
        "max_open_positions": {"default": 1, "min": 1, "max": 1},
        "stop_loss_required": True,
    },
    "limits": {"configured_pairs": MAX_CONFIGURED_PAIRS, "active_pairs": MAX_ACTIVE_PAIRS},
    "features": {"manual_trading": False, "public_signup": False, "invite_only": True},
}


def public_catalog() -> dict[str, Any]:
    return deepcopy(CATALOG)


def target_by_id(target_id: str) -> dict[str, Any]:
    for target in TARGETS:
        if target["id"] == target_id:
            return deepcopy(target)
    raise ValueError("unknown exchange target")


def preset_by_id(preset_id: str) -> dict[str, Any]:
    for preset in PRESETS:
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


def validate_profile(
    preset_ids: list[str], active_preset_id: str, risk: dict[str, Any]
) -> dict[str, Any]:
    ids = list(dict.fromkeys(str(item) for item in preset_ids))
    if len(ids) != 1:
        raise ValueError("select exactly one certified preset")
    presets = [preset_by_id(item) for item in ids]
    if active_preset_id != ids[0]:
        raise ValueError("active preset must match the selected preset")
    return {
        "preset_ids": ids,
        "active_preset_id": active_preset_id,
        "presets": presets,
        "target_id": presets[0]["target_id"],
        "risk": safe_risk(risk),
    }
