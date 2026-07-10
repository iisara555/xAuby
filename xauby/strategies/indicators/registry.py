"""Indicator plugin registry and auto-discovery."""
from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any, Dict, List, Type

from xauby.strategies.indicators.base import Indicator

logger = logging.getLogger("xauby.strategies.indicators")

_REGISTRY: Dict[str, Type[Indicator]] = {}
_DISCOVERED: bool = False

DEFAULT_STRATEGY_CHART_MAP: Dict[str, List[str]] = {
    "donchian_trend": ["donchian_trend"],
    "cdc_action_zone": ["cdc_action_zone"],
    "supertrend_ema200": ["supertrend", "ema200"],
    "btc_ema_pullback": ["btc_ema_pullback"],
    "ict_lite_strategy": ["ict_lite"],
    "bbkc_squeeze": ["bbkc_squeeze"],
    "bbrsi_mean_reversion": ["bbrsi_mean_reversion"],
    "simple_scalp_plus": ["simple_scalp_plus"],
    "sol_ema_pullback": ["sol_ema_pullback"],
    "smc_luxalgo": ["smc_luxalgo"],
    "xauby_vwap_pullback": ["xauby_vwap_pullback"],
    "rsi2_meanrev": ["rsi2_meanrev"],
    "vol_breakout": ["vol_breakout"],
    "supertrend_short": ["supertrend", "ema200"],
}


def register(name: str):
    def _wrap(cls: Type[Indicator]) -> Type[Indicator]:
        if not issubclass(cls, Indicator):
            raise TypeError(f"{cls!r} must subclass Indicator")
        cls.name = name
        _REGISTRY[name] = cls
        return cls

    return _wrap


def _discover_plugins() -> None:
    global _DISCOVERED
    if _DISCOVERED:
        return
    import xauby.strategies.indicators as pkg

    for mod_info in pkgutil.iter_modules(pkg.__path__):
        if not mod_info.name.startswith("indicator_"):
            continue
        full_name = f"{pkg.__name__}.{mod_info.name}"
        try:
            importlib.import_module(full_name)
        except Exception as e:
            logger.error("Failed to import indicator plugin %s: %s", full_name, e)
    _DISCOVERED = True


def available_indicators() -> List[str]:
    _discover_plugins()
    return sorted(_REGISTRY.keys())


def load_indicator(name: str, config: Dict[str, Any] | None = None) -> Indicator:
    _discover_plugins()
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown indicator {name!r}. Available: {available_indicators()}"
        )
    cls = _REGISTRY[name]
    return cls(config or {})


def indicators_for_strategy(
    strategy_name: str,
    config: Dict[str, Any] | None = None,
    *,
    chart_map: Dict[str, List[str]] | None = None,
) -> List[Indicator]:
    mapping = chart_map or DEFAULT_STRATEGY_CHART_MAP
    names = mapping.get(strategy_name) or mapping.get(str(strategy_name)) or []
    return [load_indicator(n, config) for n in names]
