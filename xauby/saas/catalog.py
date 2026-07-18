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
        "label": "XAU ActionZone · CDC Pure",
        "exchange_id": "okx",
        "market_type": "swap",
        "symbol": "XAUUSDT",
        "asset": "XAU",
        "strategy": "xauby_actionzone",
        "primary_timeframe": "4h",
        "confirm_timeframe": "1d",
        "allowed_sides": ["long", "short"],
        "max_leverage": 1,
        "live_certified": True,
        "cdc_pure_certified": True,
        "stop_loss_required": False,
        "execution_profile": {
            "name": "cdc_pure",
            "enable_short": True,
            "use_d1_regime_filter": False,
            "fresh_zone_window": 3,
            "disable_stop_loss": True,
            "breakeven_sl_enabled": False,
            "minimal_roi": {"0": 8.0, "1440": 5.0, "4320": 3.0},
            "ap_smoothing": 2,
            "require_slow_slope": True,
            "slow_slope_bars": 3,
            "position_pct": 0.95,
            "partial_tp_pct": 12.0,
            "partial_tp_fraction": 0.5,
        },
        "strategy_traits": [
            "Long + short · stop-and-reverse",
            "D1 regime filter: off",
            "Partial TP: bank 50% at +12%",
            "Slope filter: on (EMA26, 3 bars)",
            "Exit: CDC zone flip / ROI ladder · no exchange stop",
        ],
        "backtest": {
            "status": "validated",
            "score_label": "PF 1.7",
            "period": "Jan 2024 – Jul 2026",
            "duration": "2.6 years · full cycle",
            "win_rate_pct": 44.6,
            "max_drawdown_pct": 8.4,
            "trades": 166,
            "source": "July 2026 study · PAXGUSDT proxy · 4H · long+short · net of fee/funding",
        },
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
        "backtest": {
            "status": "insufficient",
            "score_label": "Insufficient",
            "period": "Jun 2023 – Jun 2026",
            "duration": "3 years",
            "win_rate_pct": None,
            "max_drawdown_pct": None,
            "trades": 1,
            "source": "Regime-specific OOS · sample too small",
        },
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
        "backtest": {
            "status": "pending",
            "score_label": "Pending",
            "period": "Mar 2026 – Jun 2026",
            "duration": "3.5 months data",
            "win_rate_pct": None,
            "max_drawdown_pct": None,
            "trades": None,
            "source": "BTCTHB certified run not published",
        },
    },
]

CATALOG: dict[str, Any] = {
    "targets": TARGETS,
    "exchanges": TARGETS,
    "presets": PRESETS,
    "risk": {
        "risk_pct": {"default": 0.01, "min": 0.001, "max": 0.01},
        # Position allocation is capped at 95% so a configured position can
        # leave a 5% quote-currency buffer.  The default remains 10%; high
        # allocation is opt-in and still subject to the one-position,
        # 1x-leverage and strategy-exit/circuit-breaker gates.
        "max_position_per_trade_pct": {"default": 10.0, "min": 1.0, "max": 95.0},
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
    bounded_risk = safe_risk(risk)
    # Only a catalog-certified CDC Pure preset may run without an exchange
    # stop-loss. All other presets remain stop-loss protected.
    bounded_risk["stop_loss_required"] = bool(presets[0].get("stop_loss_required", True))
    return {
        "preset_ids": ids,
        "active_preset_id": active_preset_id,
        "presets": presets,
        "target_id": presets[0]["target_id"],
        "risk": bounded_risk,
    }
