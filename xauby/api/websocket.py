"""Binance websocket pricing listener.

Kept here for backward-compatible imports (``from xauby.api.websocket import
LiteBinanceWebSocket``). The implementation now lives in the plugin module
:mod:`xauby.api.exchanges.exchange_binance` on top of the reusable
:class:`xauby.api.ws_base.ThreadedWebSocketBase`.
"""
from __future__ import annotations

from xauby.api.exchanges.exchange_binance import BINANCE_WS_URL, BinanceWebSocket

# Backward-compatible alias used across the codebase and tests.
LiteBinanceWebSocket = BinanceWebSocket

__all__ = ["LiteBinanceWebSocket", "BinanceWebSocket", "BINANCE_WS_URL"]
