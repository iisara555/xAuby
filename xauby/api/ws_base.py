"""Exchange websocket plugin contract + reusable threaded base.

The engine only duck-types against :class:`ExchangeWebSocket`. Concrete adapters
either subclass :class:`ThreadedWebSocketBase` (for ``websocket-client`` style
single-connection streams like Binance) or implement the protocol directly (e.g.
the ccxt.pro asyncio adapter).

The tick dict shape every adapter must emit to ``on_tick`` is::

    {
        "symbol": str,                 # uppercase, no underscores
        "last": float,                 # last/close price
        "bid": float,
        "ask": float,
        "percent_change_24h": float,
        "timestamp": float,            # time.time()
        "monotonic_ts": float,         # time.monotonic() (staleness check)
    }

Status events emitted to ``on_status`` are
``{"event": "ws_disconnected" | "ws_reconnected", ...}``.
"""
from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

try:
    import websocket
except ImportError:
    websocket = None

logger = logging.getLogger("lite_api")


@runtime_checkable
class ExchangeWebSocket(Protocol):
    """The streaming surface the engine relies on (see engine/base.py, loop.py)."""

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def trim_price_cache(self, active_symbols: Optional[List[str]] = None) -> int: ...

    def get_latest_price(self, symbol: str) -> Optional[float]: ...

    def tick_age_seconds(self, symbol: str) -> Optional[float]: ...


class ThreadedWebSocketBase(ABC):
    """Reusable ``websocket-client`` driver: thread loop, reconnect, cache, status.

    Subclasses implement the two exchange-specific hooks :meth:`_stream_url` and
    :meth:`_parse_message`; everything else (threading, exponential-backoff
    reconnect, the price cache, and status emission) is shared.
    """

    def __init__(
        self,
        symbols: List[str],
        on_tick: Callable[[Dict[str, Any]], None],
        ws_url: Optional[str] = None,
        on_status: Optional[Callable[[Dict[str, Any]], None]] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.symbols = [s.upper().replace("_", "") for s in symbols]
        self.on_tick = on_tick
        self.on_status = on_status
        ws_cfg = (config or {}).get("websocket") or {}
        self.ws_url = ws_url or str(ws_cfg.get("url") or self.default_url())
        self.ping_interval = max(0, int(ws_cfg.get("ping_interval", 20)))
        self.ping_timeout = max(1, int(ws_cfg.get("ping_timeout", 10)))
        self.reconnect_max_delay = max(1.0, float(ws_cfg.get("reconnect_max_delay", 60.0)))
        self.reconnect_multiplier = max(1.0, float(ws_cfg.get("reconnect_multiplier", 2.0)))
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.ws: Optional[Any] = None
        self.price_cache: Dict[str, Dict[str, Any]] = {}
        self.cache_lock = threading.Lock()
        self.reconnect_delay = 1.0
        self._disconnected_at = 0.0
        self._conn_lock = threading.Lock()

    # --- exchange-specific hooks -------------------------------------------------

    @staticmethod
    def default_url() -> str:
        """Fallback websocket URL when none is configured."""
        return ""

    @abstractmethod
    def _stream_url(self) -> str:
        """Full URL (incl. stream subscription query) for the current symbols."""

    @abstractmethod
    def _parse_message(self, message: str) -> Optional[Dict[str, Any]]:
        """Parse a raw frame into the normalized tick dict, or ``None`` to skip."""

    # --- shared machinery --------------------------------------------------------

    def _emit_status(self, event: str, **kwargs: Any) -> None:
        if not self.on_status:
            return
        try:
            self.on_status({"event": event, **kwargs})
        except Exception as e:
            logger.debug(f"on_status callback failed: {e}")

    def start(self) -> None:
        if not websocket:
            logger.error("websocket-client not installed. Cannot start websocket pricing.")
            return
        with self._conn_lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()

    def _run_loop(self) -> None:
        while True:
            with self._conn_lock:
                if not self._running:
                    break
                url = self._stream_url()
                logger.info(f"Connecting websocket to {url}")
                self.ws = websocket.WebSocketApp(
                    url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )

            self.ws.run_forever(
                ping_interval=self.ping_interval,
                ping_timeout=self.ping_timeout,
                ping_payload="ping",
            )

            with self._conn_lock:
                if not self._running:
                    break

            time.sleep(self.reconnect_delay)
            self.reconnect_delay = min(
                self.reconnect_max_delay,
                self.reconnect_delay * self.reconnect_multiplier,
            )

    def _on_open(self, ws: Any) -> None:
        logger.info("Websocket connected")
        downtime = 0.0
        if self._disconnected_at > 0:
            downtime = time.time() - self._disconnected_at
            self._disconnected_at = 0.0
        self.reconnect_delay = 1.0
        if downtime > 30:
            self._emit_status("ws_reconnected", downtime_sec=downtime)

    def _on_message(self, ws: Any, message: str) -> None:
        try:
            tick = self._parse_message(message)
            if not tick:
                return
            symbol = str(tick.get("symbol", "")).upper()
            if not symbol:
                return
            with self.cache_lock:
                self.price_cache[symbol] = tick
            if self.on_tick:
                self.on_tick(tick)
        except Exception as e:
            logger.debug(f"Websocket parse failed: {e}")

    def _on_error(self, ws: Any, error: Any) -> None:
        logger.warning(f"Websocket error: {error}")
        if self._disconnected_at <= 0:
            self._disconnected_at = time.time()
            self._emit_status("ws_disconnected", error=str(error))

    def _on_close(self, ws: Any, code: Any, reason: Any) -> None:
        logger.info(f"Websocket closed: {code} - {reason}")
        if self._disconnected_at <= 0:
            self._disconnected_at = time.time()
            self._emit_status("ws_disconnected", code=code, reason=str(reason))

    def trim_price_cache(self, active_symbols: Optional[List[str]] = None) -> int:
        """Drop cache entries for symbols no longer subscribed (bounded memory)."""
        allowed = {s.upper().replace("_", "") for s in (active_symbols or self.symbols)}
        removed = 0
        with self.cache_lock:
            for key in list(self.price_cache.keys()):
                if key not in allowed:
                    del self.price_cache[key]
                    removed += 1
        return removed

    def get_latest_price(self, symbol: str) -> Optional[float]:
        sym = symbol.upper().replace("_", "")
        with self.cache_lock:
            tick = self.price_cache.get(sym)
            return tick["last"] if tick else None

    def tick_age_seconds(self, symbol: str) -> Optional[float]:
        sym = symbol.upper().replace("_", "")
        with self.cache_lock:
            tick = self.price_cache.get(sym)
        if not tick:
            return None
        mono = float(tick.get("monotonic_ts") or 0.0)
        if mono <= 0:
            return None
        return max(0.0, time.monotonic() - mono)

    def stop(self) -> None:
        with self._conn_lock:
            self._running = False
            if self.ws:
                try:
                    self.ws.close()
                except Exception:
                    pass
        if self._thread:
            self._thread.join(timeout=5.0)


def build_tick(
    symbol: str,
    last: float,
    bid: float = 0.0,
    ask: float = 0.0,
    percent_change_24h: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """Construct the canonical tick dict; returns ``None`` for a non-positive price."""
    if last <= 0:
        return None
    return {
        "symbol": str(symbol).upper().replace("_", ""),
        "last": float(last),
        "bid": float(bid or 0.0),
        "ask": float(ask or 0.0),
        "percent_change_24h": float(percent_change_24h or 0.0),
        "timestamp": time.time(),
        "monotonic_ts": time.monotonic(),
    }


__all__ = [
    "ExchangeWebSocket",
    "ThreadedWebSocketBase",
    "build_tick",
]
