"""WebSocket adapter plugin registry & auto-discovery.

Mirrors the strategy/indicator registries. Each exchange plugin module under
``xauby/api/exchanges/`` registers its streaming adapter with
``@register_ws("<provider>")``; the factory resolves one by name. Adding a new
exchange's streaming support is a drop-in file — no edits here.
"""
from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from typing import Any, Callable, Dict, List, Optional, Type

logger = logging.getLogger("xauby.api.ws_registry")

_WS_REGISTRY: Dict[str, Type[Any]] = {}
_DISCOVERED: bool = False


def register_ws(name: str):
    """Class decorator registering a websocket adapter under ``name`` (lowercased)."""

    def _wrap(cls: Type[Any]) -> Type[Any]:
        _WS_REGISTRY[name.lower()] = cls
        return cls

    return _wrap


def _discover_plugins() -> None:
    """Import every ``exchange_*`` module so the decorators run."""
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


def available_ws_adapters() -> List[str]:
    _discover_plugins()
    return sorted(_WS_REGISTRY.keys())


def create_ws_adapter(
    name: str,
    symbols: List[str],
    on_tick: Callable[[Dict[str, Any]], None],
    on_status: Optional[Callable[[Dict[str, Any]], None]] = None,
    config: Optional[Dict[str, Any]] = None,
    ws_url: Optional[str] = None,
    on_market: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Optional[Any]:
    """Instantiate the streaming adapter registered for ``name``.

    Returns ``None`` when no adapter is registered for the provider (the engine
    treats ``None`` as "REST-polling only"). Adapters may also return ``None``
    from their constructor path via :func:`unavailable` if an optional
    dependency (e.g. ``ccxt.pro``) is missing.
    """
    _discover_plugins()
    cls = _WS_REGISTRY.get(name.lower())
    if cls is None:
        return None
    try:
        kwargs = {
            "symbols": symbols, "on_tick": on_tick, "on_status": on_status,
            "config": config, "ws_url": ws_url,
        }
        if "on_market" in inspect.signature(cls).parameters:
            kwargs["on_market"] = on_market
        return cls(**kwargs)
    except _AdapterUnavailable as exc:
        logger.info("WebSocket adapter %r unavailable: %s; falling back to polling", name, exc)
        return None


class _AdapterUnavailable(RuntimeError):
    """Raised by an adapter constructor when its optional dependency is missing."""


def unavailable(reason: str) -> _AdapterUnavailable:
    return _AdapterUnavailable(reason)
