from __future__ import annotations

from copy import deepcopy
from typing import Any

from .certification import CERTIFICATION_STATUSES, apply_certification
from .preset_specs import PRESET_SPECS

MAX_CONFIGURED_PAIRS = 3
MAX_ACTIVE_PAIRS = 3

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
        "manual_short_live_certified": True,
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
        "manual_short_live_certified": True,
    },
    {
        "id": "binance-global-spot",
        "exchange_id": "binance",
        "label": "Binance Global Spot",
        "market_type": "spot",
        "credential_fields": ["api_key", "api_secret"],
        "live_certified": False,
        "manual_allowed_sides": ["long"],
        "manual_long_live_certified": False,
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
    {
        "id": "binance-th-spot-usdt",
        "exchange_id": "binance_th",
        "label": "Binance TH Spot · USDT",
        "market_type": "spot",
        "credential_fields": ["api_key", "api_secret"],
        "live_certified": False,
        "manual_allowed_sides": ["long"],
        "manual_long_live_certified": False,
        "manual_short_live_certified": False,
    },
]

# Full per-target recipe for the tenant ``exchange:`` block plus the
# whitelist envelope. The supervisor writes ``exchange`` wholesale (never
# merges) so no key from a previous target can leak across a switch.
# Units: ``exchange.fee_pct`` is a fraction; ``whitelist.sim_fee_pct`` is a
# percent (the engine divides by 100 in pair_registry).
EXCHANGE_PROFILES: dict[str, dict[str, Any]] = {
    "okx-swap": {
        "exchange": {
            "provider": "ccxt",
            "ccxt_id": "okx",
            "name": "okx",
            "quote_asset": "USDT",
            "settle_asset": "USDT",
            "fee_pct": 0.0005,
            "market_type": "swap",
            "margin_mode": "isolated",
            "position_mode": "one_way",
            "api_key_env": "OKX_API_KEY",
            "api_secret_env": "OKX_API_SECRET",
            "base_url_env": "OKX_BASE_URL",
            "params": {"options": {"defaultType": "swap"}},
            "sandbox": False,
        },
        "derivatives": {"market_type": "swap"},
        "whitelist": {"quote_asset": "USDT", "sim_fee_pct": 0.05, "min_quote_balance": 10.0},
    },
    "binance-global-futures": {
        "exchange": {
            "provider": "ccxt",
            "ccxt_id": "binanceusdm",
            "name": "binanceusdm",
            "quote_asset": "USDT",
            "settle_asset": "USDT",
            "fee_pct": 0.0005,
            "market_type": "swap",
            "margin_mode": "isolated",
            "position_mode": "one_way",
            "api_key_env": "BINANCEUSDM_API_KEY",
            "api_secret_env": "BINANCEUSDM_API_SECRET",
            "base_url_env": "BINANCEUSDM_BASE_URL",
            "params": {"options": {"defaultType": "swap"}},
            "sandbox": False,
        },
        "derivatives": {"market_type": "swap"},
        "whitelist": {"quote_asset": "USDT", "sim_fee_pct": 0.05, "min_quote_balance": 10.0},
    },
    "binance-global-spot": {
        "exchange": {
            "provider": "ccxt",
            "ccxt_id": "binance",
            "name": "binance_global",
            "quote_asset": "USDT",
            "fee_pct": 0.001,
            "market_type": "spot",
            "api_key_env": "BINANCE_API_KEY",
            "api_secret_env": "BINANCE_API_SECRET",
            "base_url_env": "BINANCE_GLOBAL_BASE_URL",
            "params": {"options": {"defaultType": "spot"}},
            "sandbox": False,
        },
        "derivatives": {"market_type": "spot"},
        "whitelist": {"quote_asset": "USDT", "sim_fee_pct": 0.1, "min_quote_balance": 10.0},
    },
    "binance-th-spot": {
        # Native Binance TH client (api.binance.th, /api/v1 surface, TH
        # gstream WS). Must NOT carry a ccxt_id: its presence would route the
        # gateway factory to the generic ccxt adapter, and ccxt has no
        # binance_th exchange.
        "exchange": {
            "provider": "binance",
            "name": "binance_th",
            "quote_asset": "THB",
            "settle_asset": "THB",
            "fee_pct": 0.0025,
            "market_type": "spot",
            "base_url": "https://api.binance.th",
            "api_key_env": "BINANCE_TH_API_KEY",
            "api_secret_env": "BINANCE_TH_API_SECRET",
            "base_url_env": "BINANCE_TH_BASE_URL",
        },
        "derivatives": {"market_type": "spot"},
        "whitelist": {"quote_asset": "THB", "sim_fee_pct": 0.25, "min_quote_balance": 350.0},
    },
    "binance-th-spot-usdt": {
        "exchange": {
            "provider": "binance",
            "name": "binance_th",
            "quote_asset": "USDT",
            "settle_asset": "USDT",
            "fee_pct": 0.001,
            "market_type": "spot",
            "base_url": "https://api.binance.th",
            "api_key_env": "BINANCE_TH_API_KEY",
            "api_secret_env": "BINANCE_TH_API_SECRET",
            "base_url_env": "BINANCE_TH_BASE_URL",
        },
        "derivatives": {"market_type": "spot"},
        "whitelist": {"quote_asset": "USDT", "sim_fee_pct": 0.1, "min_quote_balance": 10.0},
    },
}


def exchange_profile(target_id: str) -> dict[str, Any]:
    profile = EXCHANGE_PROFILES.get(str(target_id))
    if profile is None:
        raise ValueError("unknown exchange target")
    return deepcopy(profile)

# The published catalog. Each spec is merged with the verdict and evidence read
# from its certificate record; a spec that tries to state either itself raises,
# and approval without a passing verdict requires an operator_override. Built at
# import so a bad catalog fails at startup rather than at the first request.
PRESETS = [apply_certification(spec) for spec in PRESET_SPECS]

CATALOG: dict[str, Any] = {
    "targets": TARGETS,
    "exchanges": TARGETS,
    "presets": PRESETS,
    "risk": {
        "risk_pct": {"default": 0.01, "min": 0.001, "max": 0.01},
        # Position allocation is owned by each certified preset. This global
        # value remains only as a legacy fallback for presets without a pair
        # cap; the workspace displays the selected pair allocations and the
        # resulting unallocated cash instead of inventing a fixed buffer.
        "max_position_per_trade_pct": {"default": 10.0, "min": 1.0, "max": 95.0},
        "max_daily_loss_pct": {"default": 6.0, "min": 1.0, "max": 6.0},
        "max_leverage": {"default": 1.0, "min": 1.0, "max": 1.0},
        "max_open_positions": {"default": 1, "min": 1, "max": MAX_ACTIVE_PAIRS},
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
    if not 1 <= len(ids) <= MAX_CONFIGURED_PAIRS:
        raise ValueError(f"select between 1 and {MAX_CONFIGURED_PAIRS} certified presets")
    presets = [preset_by_id(item) for item in ids]
    target_ids = {preset["target_id"] for preset in presets}
    if len(target_ids) != 1:
        raise ValueError("all pairs must trade on the same exchange")
    symbols = [preset["symbol"] for preset in presets]
    if len(set(symbols)) != len(symbols):
        raise ValueError("each pair may be selected only once")
    if active_preset_id not in ids:
        raise ValueError("active preset must be one of the selected presets")
    supplied_risk = dict(risk or {})
    # max_open_positions is derived from the pair count, never user input.
    supplied_risk.pop("max_open_positions", None)
    bounded_risk = safe_risk(supplied_risk)
    bounded_risk["max_open_positions"] = len(ids)
    # Only a catalog-certified CDC Pure preset may run without an exchange
    # stop-loss; the per-asset flags in the whitelist keep every other pair
    # stop-loss protected even when this global flag relaxes.
    bounded_risk["stop_loss_required"] = all(
        bool(preset.get("stop_loss_required", True)) for preset in presets
    )
    return {
        "preset_ids": ids,
        "active_preset_id": active_preset_id,
        "presets": presets,
        "target_id": presets[0]["target_id"],
        "risk": bounded_risk,
    }
