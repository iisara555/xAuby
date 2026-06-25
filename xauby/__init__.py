# xauby package initialization.
#
# Public names are exported lazily (PEP 562) so that importing a lightweight
# submodule (e.g. ``xauby.runtime.pair_config``) does not pull in the trading
# engine, pandas, and the exchange stack. ``from xauby import LiteTradingEngine``
# still works — the symbol is resolved on first access.

import importlib

# name -> (module, attribute). Attribute defaults to the name when omitted.
_LAZY_EXPORTS = {
    "LiteTradingEngine": ("xauby.engine.trading", None),
    "LiteDB": ("xauby.database.db", None),
    "create_exchange_client": ("xauby.api", None),
    "IExchangeGateway": ("xauby.api.interface", None),
    "ExchangeAPIError": ("xauby.api.errors", None),
    "LiteBinanceClient": ("xauby.api.client", None),
    "BinanceAPIError": ("xauby.api.client", None),
    "LiteBinanceWebSocket": ("xauby.api.websocket", None),
    "Strategy": ("xauby.strategies", None),
    "Signal": ("xauby.strategies", None),
    "MarketContext": ("xauby.strategies", None),
    "load_strategy": ("xauby.strategies", None),
    "available_strategies": ("xauby.strategies", None),
    "register": ("xauby.strategies", None),
    "CDCActionZoneStrategy": ("xauby.strategies.cdc_action_zone", None),
    "classify_zone": ("xauby.strategies.cdc_action_zone", None),
    # Backward-compatibility alias (old name).
    "CDCZoneStrategy": ("xauby.strategies.cdc_action_zone", "CDCActionZoneStrategy"),
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = target
    module = importlib.import_module(module_name)
    return getattr(module, attr or name)


def __dir__():
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
