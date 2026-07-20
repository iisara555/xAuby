from __future__ import annotations

from copy import deepcopy
from typing import Any

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
}


def exchange_profile(target_id: str) -> dict[str, Any]:
    profile = EXCHANGE_PROFILES.get(str(target_id))
    if profile is None:
        raise ValueError("unknown exchange target")
    return deepcopy(profile)

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
        "allocation_pct": 65.0,
        "max_position_per_trade_pct": 25.0,
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
        "label": "BTC Supertrend EMA200 · Binance 1H",
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
    {
        "id": "okx-btc-supertrend-v1",
        "target_id": "okx-swap",
        "label": "BTC Supertrend EMA200 · 4H",
        "exchange_id": "okx",
        "market_type": "swap",
        "symbol": "BTCUSDT",
        "asset": "BTC",
        "strategy": "supertrend_ema200",
        "primary_timeframe": "4h",
        "confirm_timeframe": "",
        "allowed_sides": ["long", "short"],
        "max_leverage": 1,
        "allocation_pct": 30.0,
        "risk_pct": 0.02,
        "max_position_per_trade_pct": 25.0,
        "live_certified": True,
        "stop_loss_required": True,
        "execution_profile": {
            "enable_short": True,
            "ema_period": 200,
            "atr_period": 10,
            "supertrend_mult": 4.0,
            "confirm_bars": 1,
            "entry_on_flip_only": True,
            "rsi_period": 14,
            "rsi_min": 0.0,
            "rsi_max": 100.0,
            "volume_ma_period": 20,
            "vol_min_ratio": 0.0,
            "sl_atr_mult": 3.0,
            "trailing_atr_mult": 2.0,
            "fixed_tp_pct": 0.0,
            "breakeven_sl_enabled": True,
            "breakeven_activation_atr_mult": 1.2,
            "breakeven_buffer_atr_mult": 0.05,
            "exit_on_supertrend_flip": True,
            "exit_on_ema_loss": True,
            "max_calc_bars": 420,
        },
        "strategy_traits": [
            "Long + short · 1× isolated perpetual",
            "Single timeframe: 4H",
            "Risk sizing: 2% equity / stop distance",
            "Pair allocation cap: 30%",
            "Exit: SuperTrend flip / EMA200 loss · 3× ATR stop",
        ],
        "backtest": {
            "status": "validated",
            "score_label": "+9.8% OOS",
            "period": "Jan 2021 – Jun 2026",
            "duration": "66 OOS months · fixed config",
            "win_rate_pct": None,
            "max_drawdown_pct": None,
            "trades": 111,
            "source": "July 2026 certification · BTCUSDT 4H proxy · long+short · net of costs",
        },
    },
    {
        "id": "binance-eth-supertrend-v1",
        "target_id": "binance-global-futures",
        "label": "ETH Supertrend EMA200",
        "exchange_id": "binanceusdm",
        "market_type": "swap",
        "symbol": "ETHUSDT",
        "asset": "ETH",
        "strategy": "supertrend_ema200",
        "primary_timeframe": "1h",
        "confirm_timeframe": "4h",
        "allowed_sides": ["long"],
        "max_leverage": 1,
        "live_certified": False,
        "stop_loss_required": True,
        "backtest": {
            "status": "pending",
            "score_label": "Pending",
            "period": "—",
            "duration": "ETHUSDT certified run not published",
            "win_rate_pct": None,
            "max_drawdown_pct": None,
            "trades": None,
            "source": "SIM only until an ETH run is certified",
        },
    },
    {
        "id": "binance-spot-btc-supertrend-v1",
        "target_id": "binance-global-spot",
        "label": "BTC Supertrend EMA200 (Spot)",
        "exchange_id": "binance",
        "market_type": "spot",
        "symbol": "BTCUSDT",
        "asset": "BTC",
        "strategy": "supertrend_ema200",
        "primary_timeframe": "1h",
        "confirm_timeframe": "4h",
        "allowed_sides": ["long"],
        "max_leverage": 1,
        "live_certified": False,
        "stop_loss_required": True,
        "backtest": {
            "status": "pending",
            "score_label": "Pending",
            "period": "—",
            "duration": "Binance Global spot run not published",
            "win_rate_pct": None,
            "max_drawdown_pct": None,
            "trades": None,
            "source": "SIM only until a spot run is certified",
        },
    },
    {
        "id": "binance-spot-eth-supertrend-v1",
        "target_id": "binance-global-spot",
        "label": "ETH Supertrend EMA200 (Spot)",
        "exchange_id": "binance",
        "market_type": "spot",
        "symbol": "ETHUSDT",
        "asset": "ETH",
        "strategy": "supertrend_ema200",
        "primary_timeframe": "1h",
        "confirm_timeframe": "4h",
        "allowed_sides": ["long"],
        "max_leverage": 1,
        "live_certified": False,
        "stop_loss_required": True,
        "backtest": {
            "status": "pending",
            "score_label": "Pending",
            "period": "—",
            "duration": "Binance Global spot run not published",
            "win_rate_pct": None,
            "max_drawdown_pct": None,
            "trades": None,
            "source": "SIM only until a spot run is certified",
        },
    },
    {
        "id": "binance-th-eth-supertrend-v1",
        "target_id": "binance-th-spot",
        "label": "ETH/THB Supertrend EMA200",
        "exchange_id": "binance_th",
        "market_type": "spot",
        "symbol": "ETHTHB",
        "asset": "ETH",
        "strategy": "supertrend_ema200",
        "primary_timeframe": "1h",
        "confirm_timeframe": "4h",
        "allowed_sides": ["long"],
        "max_leverage": 1,
        "live_certified": False,
        "stop_loss_required": True,
        "backtest": {
            "status": "pending",
            "score_label": "Pending",
            "period": "—",
            "duration": "ETHTHB certified run not published",
            "win_rate_pct": None,
            "max_drawdown_pct": None,
            "trades": None,
            "source": "SIM only until an ETHTHB run is certified",
        },
    },
]

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
