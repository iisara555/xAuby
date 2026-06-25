"""Architecture feature flags from bot_config.yaml."""
from __future__ import annotations

from typing import Any, Dict, List

DEFAULT_ARCHITECTURE: Dict[str, Any] = {
    "whitelist_strict": False,
    "indicator_registry_enabled": False,
    "sync_yaml_pairs_from_whitelist": True,
    "tui_indicator_registry": False,
    "per_symbol_execution_mode": False,
    "sim_broker_enabled": False,
    "sim_fill_on_close": True,
    "regime_router_enabled": False,
    "regime_confidence_filter": False,
    "event_bus_enabled": False,
    "exchange_plugin_registry_enabled": False,
    "strategy_chart_indicators": {
        "cdc_action_zone": ["cdc_action_zone"],
        "supertrend_ema200": ["supertrend", "ema200"],
        "btc_ema_pullback": ["btc_ema_pullback"],
        "ict_lite_strategy": ["ict_lite"],
        "bbkc_squeeze": ["bbkc_squeeze"],
        "bbrsi_mean_reversion": ["bbrsi_mean_reversion"],
        "rsi2_meanrev": ["rsi2_meanrev"],
        "vol_breakout": ["vol_breakout"],
        "supertrend_short": ["supertrend", "ema200"],
    },
}

# Regime -> strategy plugin id. Values MUST be registered strategy ids (see
# xauby/strategies/registry.py) or None (NO_TRADE). The refactor spec table uses
# display aliases that map to the canonical plugin ids below:
#   spec "supertrend"          -> registered "supertrend_ema200"
#   spec "BBRSIMeanReversion"  -> registered "bbrsi_mean_reversion"
DEFAULT_REGIME_ROUTER_MAPPING: Dict[str, str | None] = {
    "BULL_BREAKOUT": "cdc_action_zone",
    "BULL_TREND_STRONG": "cdc_action_zone",
    "BULL_TREND_WEAK": "cdc_action_zone",
    "LOW_VOL_ACCUMULATION": "bbkc_squeeze",
    "LOW_VOL_RANGE": "bbkc_squeeze",
    "VOLATILITY_EXPANSION": "supertrend_ema200",
    "SIDEWAYS_CHOP": "bbrsi_mean_reversion",
    "BEAR_TREND_WEAK": "bbrsi_mean_reversion",
    "PANIC_SELL": None,
    "BEAR_BREAKDOWN": None,
    "BEAR_TREND_STRONG": None,
}


def architecture_block(cfg: Dict[str, Any]) -> Dict[str, Any]:
    raw = cfg.get("architecture") or {}
    if not isinstance(raw, dict):
        raw = {}
    merged = dict(DEFAULT_ARCHITECTURE)
    merged.update(raw)
    chart_map = dict(DEFAULT_ARCHITECTURE["strategy_chart_indicators"])
    user_map = raw.get("strategy_chart_indicators") or {}
    if isinstance(user_map, dict):
        chart_map.update(user_map)
    merged["strategy_chart_indicators"] = chart_map
    return merged


def whitelist_strict(cfg: Dict[str, Any]) -> bool:
    return bool(architecture_block(cfg).get("whitelist_strict"))


def indicator_registry_enabled(cfg: Dict[str, Any]) -> bool:
    return bool(architecture_block(cfg).get("indicator_registry_enabled"))


def tui_indicator_registry(cfg: Dict[str, Any]) -> bool:
    return bool(architecture_block(cfg).get("tui_indicator_registry"))


def per_symbol_execution_mode(cfg: Dict[str, Any]) -> bool:
    return bool(architecture_block(cfg).get("per_symbol_execution_mode"))


def sim_broker_enabled(cfg: Dict[str, Any]) -> bool:
    return bool(architecture_block(cfg).get("sim_broker_enabled"))


def sim_fill_on_close(cfg: Dict[str, Any]) -> bool:
    return bool(architecture_block(cfg).get("sim_fill_on_close", True))


def regime_router_enabled(cfg: Dict[str, Any]) -> bool:
    return bool(architecture_block(cfg).get("regime_router_enabled"))


def regime_confidence_filter(cfg: Dict[str, Any]) -> bool:
    return bool(architecture_block(cfg).get("regime_confidence_filter"))


def event_bus_enabled(cfg: Dict[str, Any]) -> bool:
    return bool(architecture_block(cfg).get("event_bus_enabled"))


def exchange_plugin_registry_enabled(cfg: Dict[str, Any]) -> bool:
    """DEPRECATED / vestigial. The exchange gateway + websocket factories always
    route through the plugin registries (xauby/api/exchanges/) now; the legacy
    hardcoded factory was removed. Retained for backward-compatible config/UI
    reads only — it no longer gates anything."""
    return bool(architecture_block(cfg).get("exchange_plugin_registry_enabled"))


def strategy_sandbox_strict(cfg: Dict[str, Any]) -> bool:
    """If True, strategies failing the static sandbox scan are rejected at load
    time instead of merely warned (recommended for untrusted/marketplace plugins)."""
    return bool(architecture_block(cfg).get("strategy_sandbox_strict"))


def strategy_validate_strict(cfg: Dict[str, Any]) -> bool:
    """If True, a strategy whose ``config_errors()`` is non-empty is rejected at
    load time instead of merely warned — so a fatally misconfigured strategy
    (e.g. rsi_min >= rsi_max) cannot silently trade live. Recommended for
    production. Default False preserves the warn-and-run behavior."""
    return bool(architecture_block(cfg).get("strategy_validate_strict"))


def sync_yaml_pairs_from_whitelist(cfg: Dict[str, Any]) -> bool:
    return bool(architecture_block(cfg).get("sync_yaml_pairs_from_whitelist", True))


def strategy_chart_indicators(cfg: Dict[str, Any], strategy_name: str) -> List[str]:
    mapping = architecture_block(cfg).get("strategy_chart_indicators") or {}
    names = mapping.get(strategy_name) or mapping.get(str(strategy_name))
    if isinstance(names, list) and names:
        return [str(n) for n in names]
    return []


def regime_router_mapping(cfg: Dict[str, Any]) -> Dict[str, str | None]:
    block = cfg.get("regime_router") or {}
    user_map = block.get("mapping") if isinstance(block, dict) else None
    merged = dict(DEFAULT_REGIME_ROUTER_MAPPING)
    if isinstance(user_map, dict):
        for key, val in user_map.items():
            if val in (None, "None", "null", ""):
                merged[str(key)] = None
            else:
                merged[str(key)] = str(val)
    return merged


def regime_confidence_threshold(cfg: Dict[str, Any]) -> float:
    block = cfg.get("regime_router") or {}
    if not isinstance(block, dict):
        return 0.7
    try:
        return float(block.get("confidence_threshold", 0.7))
    except (TypeError, ValueError):
        return 0.7
