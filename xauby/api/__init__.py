# xauby.api package initialization

from xauby.api.client import (
    INTERVAL_MAP, ClockSync, round_step, BinanceAPIError, LiteBinanceClient
)
from xauby.api.ccxt_client import CCXTAPIError, CCXTExchangeClient
from xauby.api.errors import ExchangeAPIError
from xauby.api.websocket import LiteBinanceWebSocket
from xauby.api.interface import IExchangeGateway
import importlib
import logging
import os
from typing import Any, Callable, Optional

logger = logging.getLogger("lite_api")


def _plugin_provider_name(exchange_cfg: dict) -> str:
    """Map exchange config to a registered plugin name."""
    provider = str(exchange_cfg.get("provider") or "").lower()
    if provider == "ccxt" or exchange_cfg.get("ccxt_id"):
        return "ccxt"
    return provider or "binance"


def create_exchange_client(
    config: dict,
    api_key: str = "",
    api_secret: str = "",
    base_url: str = None
) -> IExchangeGateway:
    """Instantiate the exchange gateway via the plugin registry.

    The legacy hardcoded binance/ccxt branches were retired once the registry
    became the live default: both built the exact client classes the registry
    builders now return (``xauby/api/exchanges/``). ``exchange.gateway_class``
    remains the escape hatch for a fully custom gateway.
    """
    exchange_cfg = config.get("exchange", {}) or {}
    gateway_class_path = exchange_cfg.get("gateway_class")

    # Custom gateway_class escape hatch wins first.
    if gateway_class_path:
        try:
            module_path, class_name = gateway_class_path.rsplit(".", 1)
            module = importlib.import_module(module_path)
            gateway_class = getattr(module, class_name)
            params = exchange_cfg.get("params", {})
            logger.info(f"Loading custom exchange gateway class: {gateway_class_path}")
            return gateway_class(api_key, api_secret, base_url, **params)
        except Exception as e:
            logger.error(f"Failed to load custom gateway_class {gateway_class_path}: {e}")
            raise

    # Resolve credentials from the exchange-specific env names when not supplied
    # (preserves the env fallback the legacy factory provided for callers that
    # don't pre-resolve via resolve_exchange_credentials).
    if not api_key or not api_secret or not base_url:
        from xauby.runtime.exchange_config import credential_env_names

        key_env, secret_env, base_url_env = credential_env_names(config)
        api_key = api_key or os.environ.get(key_env, "")
        api_secret = api_secret or os.environ.get(secret_env, "")
        base_url = base_url or exchange_cfg.get("base_url") or os.environ.get(base_url_env)

    from xauby.api.exchange_registry import build_gateway

    name = _plugin_provider_name(exchange_cfg)
    try:
        return build_gateway(name, config, api_key, api_secret, base_url)
    except KeyError as exc:
        raise ValueError(
            f"No exchange gateway plugin for {name!r}; refusing cross-exchange fallback"
        ) from exc


def create_exchange_websocket(
    config: dict,
    symbols: list[str],
    on_tick: Callable[[dict], None],
    on_status: Optional[Callable[[str], None]] = None,
    ws_url: Optional[str] = None,
    on_market: Optional[Callable[[dict], None]] = None,
) -> Any:
    """Create a streaming client for the configured exchange via the registry.

    The provider's websocket plugin is resolved by name: binance streams via
    ``BinanceWebSocket``; the ccxt plugin streams via ccxt.pro when available,
    else returns ``None`` so the engine falls back to REST polling.
    """
    exchange_cfg = (config or {}).get("exchange", {}) or {}
    from xauby.api.ws_registry import create_ws_adapter

    return create_ws_adapter(
        _plugin_provider_name(exchange_cfg),
        symbols=symbols,
        on_tick=on_tick,
        on_status=on_status,
        config=config,
        ws_url=ws_url,
        on_market=on_market,
    )
