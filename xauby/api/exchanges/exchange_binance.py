"""Binance exchange plugin: native REST gateway + websocket adapter."""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from xauby.api.client import LiteBinanceClient
from xauby.api.exchange_registry import register_exchange
from xauby.api.interface import IExchangeGateway
from xauby.api.ws_base import ThreadedWebSocketBase
from xauby.api.ws_registry import register_ws

BINANCE_WS_URL = "wss://www.binance.th/gstream"


@register_exchange("binance")
def build_binance_gateway(
    config: Dict[str, Any],
    api_key: str = "",
    api_secret: str = "",
    base_url: Optional[str] = None,
) -> IExchangeGateway:
    return LiteBinanceClient(api_key, api_secret, base_url, config=config)


@register_ws("binance")
class BinanceWebSocket(ThreadedWebSocketBase):
    """Binance Thailand combined-stream ticker listener."""

    @staticmethod
    def default_url() -> str:
        return BINANCE_WS_URL

    def _stream_url(self) -> str:
        streams = [f"{s.lower()}@ticker" for s in self.symbols]
        return f"{self.ws_url}?streams={'/'.join(streams)}"

    def _parse_message(self, message: str) -> Optional[Dict[str, Any]]:
        data = json.loads(message)
        payload = data.get("data", data)
        symbol = str(payload.get("s", "")).upper()
        if not symbol:
            return None
        last = float(payload.get("c") or payload.get("lastPrice") or 0.0)
        if last <= 0:
            return None
        return {
            "symbol": symbol,
            "last": last,
            "bid": float(payload.get("b") or payload.get("bidPrice") or 0.0),
            "ask": float(payload.get("a") or payload.get("askPrice") or 0.0),
            "percent_change_24h": float(payload.get("P") or payload.get("priceChangePercent") or 0.0),
            "timestamp": time.time(),
            "monotonic_ts": time.monotonic(),
        }
