"""Generic CCXT exchange plugin: REST gateway + ccxt.pro websocket adapter.

The REST side reuses :class:`CCXTExchangeClient`. The streaming side uses
``ccxt.pro`` (bundled with modern ``ccxt``) ``watch_ticker`` to give real
multi-exchange streaming. When ``ccxt.pro`` is unavailable the adapter declines
construction (raises :func:`unavailable`) so the factory falls back to REST
polling — no crash.
"""
from __future__ import annotations

import asyncio
import os
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from xauby.api.ccxt_client import CCXTExchangeClient
from xauby.api.exchange_registry import register_exchange
from xauby.api.interface import IExchangeGateway
from xauby.api.ws_base import build_tick
from xauby.api.ws_registry import register_ws, unavailable
from xauby.runtime.derivatives_config import derivatives_settings

logger = logging.getLogger("lite_api")


@register_exchange("ccxt")
def build_ccxt_gateway(
    config: Dict[str, Any],
    api_key: str = "",
    api_secret: str = "",
    base_url: Optional[str] = None,
) -> IExchangeGateway:
    return CCXTExchangeClient(api_key, api_secret, base_url, config=config)


def _import_ccxtpro():
    """Indirection so tests can stub the optional ``ccxt.pro`` import."""
    import ccxt.pro as ccxtpro  # type: ignore

    return ccxtpro


@register_ws("ccxt")
class CCXTProWebSocket:
    """Stream tickers from any ccxt.pro-supported exchange via ``watch_ticker``.

    Implements the :class:`~xauby.api.ws_base.ExchangeWebSocket` protocol with an
    asyncio loop running on a dedicated daemon thread.
    """

    def __init__(
        self,
        symbols: List[str],
        on_tick: Callable[[Dict[str, Any]], None],
        on_status: Optional[Callable[[Dict[str, Any]], None]] = None,
        config: Optional[Dict[str, Any]] = None,
        ws_url: Optional[str] = None,
        exchange_instance: Optional[Any] = None,
        on_market: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.symbols = [s.upper().replace("_", "") for s in symbols]
        self.on_tick = on_tick
        self.on_status = on_status
        self.on_market = on_market
        self.config = config or {}
        exchange_cfg = self.config.get("exchange") or {}
        self.exchange_id = str(
            exchange_cfg.get("ccxt_id") or exchange_cfg.get("id") or exchange_cfg.get("name") or "binance"
        ).lower()
        self.quote_asset = str(exchange_cfg.get("quote_asset") or "USDT").upper()
        self._params = dict(exchange_cfg.get("params") or {})
        self.derivatives = derivatives_settings(self.config)
        options = dict(self._params.get("options") or {})
        options.setdefault("defaultType", self.derivatives["market_type"])
        self._params["options"] = options
        try:
            from xauby.runtime.exchange_config import resolve_exchange_credentials
            api_key, api_secret, _ = resolve_exchange_credentials(self.config)
        except Exception:
            api_key, api_secret = "", ""
        if api_key:
            self._params.setdefault("apiKey", api_key)
        if api_secret:
            self._params.setdefault("secret", api_secret)
        prefix = self.exchange_id.upper()
        passphrase = (
            os.environ.get(f"{prefix}_API_PASSPHRASE")
            or os.environ.get(f"{prefix}_PASSWORD")
            or os.environ.get(f"{prefix}_API_PASSWORD")
        )
        if passphrase:
            self._params.setdefault("password", passphrase)
        self._sandbox = bool(exchange_cfg.get("sandbox", False))
        ws_cfg = self.config.get("websocket") or {}
        self.reconnect_max_delay = max(1.0, float(ws_cfg.get("reconnect_max_delay", 60.0)))
        self.reconnect_multiplier = max(1.0, float(ws_cfg.get("reconnect_multiplier", 2.0)))
        # OKX maps a depth of 50 to books50-l2-tbt, which requires VIP4.
        # Use its public books5 channel unless an operator explicitly opts in
        # to a deeper channel. Other exchanges retain the existing depth.
        default_order_book_limit = 5 if self.exchange_id == "okx" else 50
        self.order_book_limit = max(
            1, int(ws_cfg.get("order_book_limit", default_order_book_limit))
        )

        self.price_cache: Dict[str, Dict[str, Any]] = {}
        self.market_cache: Dict[str, Dict[str, Any]] = {}
        self.cache_lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._loop_ready = threading.Event()
        self._start_lock = threading.Lock()
        self._disconnected = False
        self._exchange = exchange_instance

        if exchange_instance is None:
            try:
                self._ccxtpro = _import_ccxtpro()
            except Exception as e:
                raise unavailable(f"ccxt.pro not available: {e}") from e
            if getattr(self._ccxtpro, self.exchange_id, None) is None:
                raise unavailable(f"ccxt.pro has no exchange {self.exchange_id!r}")
        else:
            self._ccxtpro = None

    # --- public ExchangeWebSocket surface ---------------------------------------

    def start(self) -> None:
        with self._start_lock:
            if self._running:
                return
            self._running = True
            self._loop_ready.clear()
            # Own the event loop here (not inside the worker thread) so stop()
            # always has a valid loop reference even if called immediately after
            # start() — closes a race where the engine's hot-reload swaps the WS.
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._running = False
        loop = self._loop
        if loop is not None:
            # is_running() becomes true before _main creates _stop_event.  A
            # callback sent in that gap is lost, so wait for explicit readiness.
            self._loop_ready.wait(timeout=2.0)
            if loop.is_running():
                try:
                    loop.call_soon_threadsafe(self._signal_stop)
                except Exception:
                    pass
        if self._thread:
            self._thread.join(timeout=5.0)

    def _signal_stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()

    def trim_price_cache(self, active_symbols: Optional[List[str]] = None) -> int:
        allowed = {s.upper().replace("_", "") for s in (active_symbols or self.symbols)}
        removed = 0
        with self.cache_lock:
            for key in list(self.price_cache.keys()):
                if key not in allowed:
                    del self.price_cache[key]
                    removed += 1
            for key in list(self.market_cache.keys()):
                if key not in allowed:
                    del self.market_cache[key]
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

    # --- helpers (sync, testable) -----------------------------------------------

    def _emit_status(self, event: str, **kwargs: Any) -> None:
        if not self.on_status:
            return
        try:
            self.on_status({"event": event, **kwargs})
        except Exception as e:
            logger.debug(f"on_status callback failed: {e}")

    def _to_ccxt_symbol(self, symbol: str) -> str:
        raw = str(symbol).upper().replace("_", "")
        markets = getattr(self._exchange, "markets", None) or {}
        if raw in markets:
            return raw
        by_id = (getattr(self._exchange, "markets_by_id", None) or {}).get(raw)
        if isinstance(by_id, list) and by_id:
            for market in by_id:
                if self.derivatives["market_type"] == "swap" and market.get("swap"):
                    return str(market.get("symbol") or raw)
            return str(by_id[0].get("symbol") or raw)
        if isinstance(by_id, dict):
            return str(by_id.get("symbol") or raw)
        if raw.endswith(self.quote_asset):
            base = raw[: -len(self.quote_asset)]
            if self.derivatives["market_type"] == "swap":
                candidate = f"{base}/{self.quote_asset}:{self.derivatives['settle_asset']}"
                if not markets or candidate in markets:
                    return candidate
            return f"{base}/{self.quote_asset}"
        return raw

    def _ticker_to_tick(self, symbol: str, ticker: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        last = float(ticker.get("last") or ticker.get("close") or 0.0)
        return build_tick(
            symbol,
            last,
            bid=float(ticker.get("bid") or 0.0),
            ask=float(ticker.get("ask") or 0.0),
            percent_change_24h=float(ticker.get("percentage") or 0.0),
        )

    def _store_and_emit(self, symbol: str, tick: Dict[str, Any]) -> None:
        sym = str(tick.get("symbol") or symbol).upper().replace("_", "")
        with self.cache_lock:
            self.price_cache[sym] = tick
        if self.on_tick:
            self.on_tick(tick)

    def _emit_market(self, symbol: str, kind: str, payload: Any) -> None:
        event = {
            "event": "market_data",
            "kind": kind,
            "exchange": self.exchange_id,
            "market_type": self.derivatives["market_type"],
            "settle_asset": self.derivatives["settle_asset"],
            "symbol": symbol.upper().replace("_", ""),
            "timestamp": time.time(),
            "monotonic_ts": time.monotonic(),
            "data": payload,
        }
        with self.cache_lock:
            cache = self.market_cache.setdefault(event["symbol"], {})
            cache[kind] = event
        if self.on_market:
            self.on_market(event)

    # --- asyncio machinery ------------------------------------------------------

    def _run(self) -> None:
        loop = self._loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._main())
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(f"ccxt.pro websocket loop ended: {e}")
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def _main(self) -> None:
        self._stop_event = asyncio.Event()
        self._loop_ready.set()
        # stop() may have already flipped _running before the loop started.
        if not self._running:
            # An injected/pre-created exchange is still owned by this adapter
            # and must be released even when startup loses the stop race.
            close_fn = getattr(self._exchange, "close", None)
            if callable(close_fn):
                try:
                    await close_fn()
                except Exception:
                    logger.debug("ccxt.pro close failed", exc_info=True)
            return
        if self._exchange is None:
            exchange_class = getattr(self._ccxtpro, self.exchange_id, None)
            if exchange_class is None:  # pragma: no cover - guarded in __init__
                return
            self._exchange = exchange_class(self._params)
            if self._sandbox and hasattr(self._exchange, "set_sandbox_mode"):
                self._exchange.set_sandbox_mode(True)
        try:
            await self._exchange.load_markets()
        except Exception as e:
            logger.warning(f"ccxt.pro load_markets failed: {e}")

        watchers = []
        for sym in self.symbols:
            watchers.extend((
                asyncio.ensure_future(self._watch_ticker(sym)),
                asyncio.ensure_future(self._watch_trades(sym)),
                asyncio.ensure_future(self._watch_order_book(sym)),
                asyncio.ensure_future(self._watch_candles(sym)),
            ))
        try:
            await self._stop_event.wait()
        finally:
            for w in watchers:
                w.cancel()
            await asyncio.gather(*watchers, return_exceptions=True)
            close_fn = getattr(self._exchange, "close", None)
            if callable(close_fn):
                try:
                    await close_fn()
                except Exception:
                    logger.debug("ccxt.pro close failed", exc_info=True)

    async def _watch_ticker(self, symbol: str) -> None:
        ccxt_symbol = self._to_ccxt_symbol(symbol)
        delay = 1.0
        while self._running:
            try:
                ticker = await self._exchange.watch_ticker(ccxt_symbol)
                delay = 1.0
                if self._disconnected:
                    self._disconnected = False
                    self._emit_status("ws_reconnected")
                tick = self._ticker_to_tick(symbol, ticker)
                if tick:
                    self._store_and_emit(symbol, tick)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if not self._disconnected:
                    self._disconnected = True
                    self._emit_status("ws_disconnected", error=str(e))
                await asyncio.sleep(delay)
                delay = min(self.reconnect_max_delay, delay * self.reconnect_multiplier)

    async def _watch_channel(self, symbol: str, kind: str, method: str, *args: Any) -> None:
        ccxt_symbol = self._to_ccxt_symbol(symbol)
        delay = 1.0
        last_nonce: Optional[int] = None
        while self._running:
            try:
                fn = getattr(self._exchange, method)
                payload = await fn(ccxt_symbol, *args)
                delay = 1.0
                if kind == "order_book" and isinstance(payload, dict):
                    nonce = payload.get("nonce")
                    if nonce is not None:
                        nonce = int(nonce)
                        if last_nonce is not None and nonce <= last_nonce:
                            self._emit_status("order_book_resync_required", symbol=symbol)
                        last_nonce = nonce
                self._emit_market(symbol, kind, payload)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._emit_status("market_channel_degraded", symbol=symbol, channel=kind, error=str(e))
                await asyncio.sleep(delay)
                delay = min(self.reconnect_max_delay, delay * self.reconnect_multiplier)

    async def _watch_trades(self, symbol: str) -> None:
        await self._watch_channel(symbol, "trades", "watch_trades")

    async def _watch_order_book(self, symbol: str) -> None:
        await self._watch_channel(
            symbol, "order_book", "watch_order_book", self.order_book_limit
        )

    async def _watch_candles(self, symbol: str) -> None:
        timeframe = str((self.config.get("trading") or {}).get("timeframe", "4h"))
        await self._watch_channel(symbol, "candle", "watch_ohlcv", timeframe)
