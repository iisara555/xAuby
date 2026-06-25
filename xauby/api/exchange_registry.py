"""Exchange REST gateway plugin registry & auto-discovery.

Mirrors :mod:`xauby.api.ws_registry` for the REST side. Each exchange plugin
module under ``xauby/api/exchanges/`` registers a builder with
``@register_exchange("<provider>")``. A builder is any callable
``(config, api_key, api_secret, base_url) -> IExchangeGateway``.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any, Callable, Dict, List, Optional

from xauby.api.interface import IExchangeGateway

logger = logging.getLogger("xauby.api.exchange_registry")

GatewayBuilder = Callable[[Dict[str, Any], str, str, Optional[str]], IExchangeGateway]

_EXCHANGE_REGISTRY: Dict[str, GatewayBuilder] = {}
_DISCOVERED: bool = False


def register_exchange(name: str):
    """Decorator registering a gateway builder under ``name`` (lowercased)."""

    def _wrap(builder: GatewayBuilder) -> GatewayBuilder:
        _EXCHANGE_REGISTRY[name.lower()] = builder
        return builder

    return _wrap


def _discover_plugins() -> None:
    global _DISCOVERED
    if _DISCOVERED:
        return
    import xauby.api.exchanges as pkg

    for mod_info in pkgutil.iter_modules(pkg.__path__):
        if not mod_info.name.startswith("exchange_"):
            continue
        full_name = f"{pkg.__name__}.{mod_info.name}"
        try:
            importlib.import_module(full_name)
        except Exception as e:  # pragma: no cover - defensive
            logger.error("Failed to import exchange plugin %s: %s", full_name, e)
    _DISCOVERED = True


def available_exchanges() -> List[str]:
    _discover_plugins()
    return sorted(_EXCHANGE_REGISTRY.keys())


def build_gateway(
    name: str,
    config: Dict[str, Any],
    api_key: str = "",
    api_secret: str = "",
    base_url: Optional[str] = None,
) -> IExchangeGateway:
    """Build the gateway registered for ``name``.

    Raises:
        KeyError: if no gateway plugin is registered for that provider.
    """
    _discover_plugins()
    builder = _EXCHANGE_REGISTRY.get(name.lower())
    if builder is None:
        raise KeyError(
            f"Unknown exchange provider {name!r}. Available: {available_exchanges()}"
        )
    return builder(config, api_key, api_secret, base_url)
