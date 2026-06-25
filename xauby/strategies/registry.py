"""Strategy plugin registry & auto-discovery.

Adding a new strategy is a 2-step exercise:

    1. Create ``xauby/strategies/<your_name>/strategy.py`` containing a
       subclass of :class:`xauby.strategies.base.Strategy`.
    2. Decorate it with ``@register("<your_name>")`` (or just set
       ``name = "<your_name>"`` — both work).

The engine never imports your file directly. It calls
:func:`load_strategy("<your_name>", config)` which discovers all plugin
subpackages under ``xauby/strategies/`` and instantiates the requested one.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any, Dict, List, Type

from xauby.strategies.base import Strategy
from xauby.strategies.manifest import strategy_manifest_from_class

logger = logging.getLogger("xauby.strategies")

_REGISTRY: Dict[str, Type[Strategy]] = {}
_DISCOVERED: bool = False


def register(name: str):
    """Class decorator to register a strategy under ``name``."""

    def _wrap(cls: Type[Strategy]) -> Type[Strategy]:
        if not issubclass(cls, Strategy):
            raise TypeError(f"{cls!r} must subclass Strategy")
        cls.name = name
        _REGISTRY[name] = cls
        return cls

    return _wrap


def _discover_plugins() -> None:
    """Import every subpackage of ``xauby.strategies`` so decorators run."""
    global _DISCOVERED
    if _DISCOVERED:
        return
    import xauby.strategies as pkg

    for mod_info in pkgutil.iter_modules(pkg.__path__):
        if not mod_info.ispkg:
            continue
        full_name = f"{pkg.__name__}.{mod_info.name}"
        try:
            importlib.import_module(full_name)
        except Exception as e:  # pragma: no cover - defensive
            logger.error(f"Failed to import strategy plugin {full_name}: {e}")
    _DISCOVERED = True


def available_strategies() -> List[str]:
    """Return the list of registered strategy ids."""
    _discover_plugins()
    return sorted(_REGISTRY.keys())


def strategy_manifest(name: str) -> Dict[str, Any]:
    """Return marketplace-facing manifest metadata for one strategy."""
    _discover_plugins()
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown strategy {name!r}. Available: {available_strategies()}"
        )
    return strategy_manifest_from_class(_REGISTRY[name])


def available_strategy_manifests() -> List[Dict[str, Any]]:
    """Return manifests for every discovered strategy plugin."""
    _discover_plugins()
    return [strategy_manifest_from_class(_REGISTRY[name]) for name in sorted(_REGISTRY.keys())]


def load_strategy(
    name: str, config: Dict[str, Any] | None = None, *, strict: bool = False
) -> Strategy:
    """Instantiate a strategy by registered ``name``.

    The final config is ``cls.default_config() | config`` so that plugin
    defaults are always present and YAML overrides win.

    After instantiation, :meth:`Strategy.validate_config` is called and
    any warnings are logged. When ``strict`` is True, a non-empty
    :meth:`Strategy.config_errors` (the fatal subset) raises ``ConfigError``
    so a misconfigured strategy cannot silently trade.

    Raises:
        KeyError: if no plugin with that name was found.
        ConfigError: if ``strict`` and the strategy reports fatal config errors.
    """
    _discover_plugins()
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown strategy {name!r}. Available: {available_strategies()}"
        )
    cls = _REGISTRY[name]
    merged = {**cls.default_config(), **(config or {})}
    instance = cls(merged)
    warnings = instance.validate_config()
    for w in warnings:
        logger.warning(f"[{name}] config warning: {w}")
    if strict:
        errors = instance.config_errors()
        if errors:
            from xauby.runtime.config_error import ConfigError

            raise ConfigError(
                f"{name}: invalid strategy config (strict mode): " + "; ".join(errors)
            )
    return instance
