import logging
import time
import hashlib
import hmac
import os
import uuid
import requests
import threading
from requests.adapters import HTTPAdapter
from urllib.parse import urlencode
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List, Optional

from xauby.api.interface import IExchangeGateway
from xauby.api.utils import find_symbol_entry, parse_exchange_filters
from xauby.api.errors import ExchangeAPIError
from xauby.domain.models import Candle, Order, Portfolio

logger = logging.getLogger("lite_api")

INTERVAL_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m",
    "1h": "1h", "4h": "4h", "1d": "1d",
    "1D": "1d", "4H": "4h", "15M": "15m",
}

class ClockSync:
    """Tracks offset between local clock and Binance.th server clock."""
    def __init__(self, max_offset: float = 30.0):
        self.max_offset = max_offset
        self._offset = 0.0
        self._last_sync = 0.0
        self._sync_interval = 300.0
        self._locked = threading.Lock()
        self._alerted = False

    @property
    def offset(self) -> float:
        with self._locked:
            return self._offset

    def sync(self, base_url: str, session: Optional[requests.Session] = None) -> float:
        try:
            if session:
                r = session.get(f"{base_url}/api/v1/time", timeout=10)
            else:
                r = requests.get(f"{base_url}/api/v1/time", timeout=10)
            r.raise_for_status()
            payload = r.json()
            if isinstance(payload, dict):
                server_ts_ms = int(payload.get("serverTime", 0))
            else:
                server_ts_ms = int(payload)
            server_ts = server_ts_ms / 1000.0
            local_ts = time.time()

            offset = server_ts - local_ts
            with self._locked:
                self._offset = offset
                self._last_sync = local_ts

            if abs(offset) > self.max_offset and not self._alerted:
                logger.warning(f"ClockSync: LARGE OFFSET detected ({offset:+.1f}s) — requests may be rejected")
                self._alerted = True
            elif abs(offset) <= self.max_offset:
                self._alerted = False

            logger.debug(f"ClockSync: offset={offset:+.3f}s")
            return offset
        except Exception as e:
            logger.error(f"ClockSync: sync failed: {e}")
            with self._locked:
                # Retain the previous offset, but retry syncing in 30 seconds
                self._last_sync = time.time() - (self._sync_interval - 30.0)
                current_offset = self._offset
            return current_offset

    def is_synced(self) -> bool:
        return abs(self._offset) <= self.max_offset

    def should_resync(self) -> bool:
        return (time.time() - self._last_sync) >= self._sync_interval


def _decimal_places_from_step(step: float) -> int:
    if step is None or step <= 0:
        return 8
    d = Decimal(str(step))
    if d.is_zero():
        return 8
    exp = d.as_tuple().exponent
    if exp >= 0:
        return 0
    return min(16, -exp)

def _format_decimal(value: float, precision: int = 8) -> str:
    s = f"{value:.{precision}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"

def _quantize_down(value: float, decimals: int) -> float:
    if decimals < 0:
        decimals = 0
    q = Decimal("1") if decimals == 0 else Decimal("1").scaleb(-decimals)
    return float(Decimal(str(float(value))).quantize(q, rounding=ROUND_DOWN))

def round_step(value: float, step: Any) -> float:
    if step is None:
        return float(value)
    try:
        s = Decimal(str(step))
    except Exception:
        return float(value)
    if s <= 0:
        return float(value)
    try:
        d = Decimal(str(value))
    except Exception:
        return float(value)
    return float((d // s) * s)


class BinanceAPIError(ExchangeAPIError):
    def __init__(self, code: int, msg: str, raw: Any = None):
        super().__init__(code, msg, raw=raw)
        self.args = (f"BinanceAPIError({code}): {msg}",)


class LiteBinanceClient(IExchangeGateway):
    BASE_URL = "https://api.binance.th"
    TIMEOUT = 15

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = (base_url or os.environ.get("BINANCE_BASE_URL") or self.BASE_URL).rstrip("/")
        self.session = requests.Session()
        api_cfg = (config or {}).get("api") or {}
        timeout_seconds = float(api_cfg.get("timeout_seconds", self.TIMEOUT))
        connect_timeout = float(api_cfg.get("connect_timeout_seconds", timeout_seconds))
        read_timeout = float(api_cfg.get("read_timeout_seconds", timeout_seconds))
        self.timeout = (connect_timeout, read_timeout)
        self.max_attempts = max(1, int(api_cfg.get("max_attempts", 3)))
        self.get_retry_backoff_seconds = max(
            0.0, float(api_cfg.get("get_retry_backoff_seconds", 0.5))
        )
        self.recv_window_ms = max(1000, int(api_cfg.get("recv_window_ms", 5000)))
        pool_size = max(1, int(api_cfg.get("pool_maxsize", 10)))
        adapter = HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.last_latency = 0.0
        self.last_request: Dict[str, Any] = {}
        self._clock = ClockSync(max_offset=30.0)
        t0 = time.time()
        self._clock.sync(self.base_url, session=self.session)
        self.last_latency = time.time() - t0
        
        self._exchange_info_cache: Dict[str, Dict[str, Any]] = {}
        self._exchange_info_lock = threading.Lock()

    def check_clock_sync(self) -> bool:
        if self._clock.should_resync():
            t0 = time.time()
            self._clock.sync(self.base_url, session=self.session)
            self.last_latency = time.time() - t0
        return self._clock.is_synced()

    def _server_now_ms(self) -> int:
        return int((time.time() + self._clock.offset) * 1000)

    def supports_exchange_stop_loss(self) -> bool:
        """Binance.th spot places exchange-side ``STOP_LOSS_LIMIT`` natively."""
        return True

    @staticmethod
    def make_client_id(prefix: str = "xb") -> str:
        safe = "".join(c for c in prefix if c.isalnum() or c in "-_")[:12]
        return f"xauby-{safe}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"

    def _sign(self, params: Dict[str, Any]) -> str:
        query_string = urlencode(params, doseq=True)
        return hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _resign_params(self, req_params: Dict[str, Any]) -> None:
        req_params["timestamp"] = self._server_now_ms()
        req_params.pop("signature", None)
        req_params["signature"] = self._sign(req_params)

    def _recover_order_by_client_id(self, symbol: str, client_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self.get_order_by_client_id(symbol, client_id)
        except BinanceAPIError as e:
            if e.code in (-2013, -2026):
                return None
            raise
        except Exception:
            return None

    def _request(self, method: str, path: str, signed: bool = False, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{path}"
        req_params = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {"Accept": "application/json"}
        is_order_post = method.upper() == "POST" and "/order" in path
        client_order_id = req_params.get("newClientOrderId") if is_order_post else None
        symbol_for_recovery = req_params.get("symbol") if is_order_post else None

        if signed:
            self.check_clock_sync()
            req_params["timestamp"] = self._server_now_ms()
            req_params.setdefault("recvWindow", self.recv_window_ms)
            req_params["signature"] = self._sign(req_params)
            headers["X-MBX-APIKEY"] = self.api_key
        elif self.api_key and method.upper() != "GET":
            headers["X-MBX-APIKEY"] = self.api_key

        max_attempts = self.max_attempts
        attempt = 0
        while True:
            attempt += 1
            try:
                t0 = time.time()
                if method.upper() == "GET":
                    r = self.session.get(url, params=req_params, headers=headers, timeout=self.timeout)
                elif method.upper() == "POST":
                    r = self.session.post(url, params=req_params, headers=headers, timeout=self.timeout)
                elif method.upper() == "DELETE":
                    r = self.session.delete(url, params=req_params, headers=headers, timeout=self.timeout)
                else:
                    raise ValueError(f"Unsupported method: {method}")
                self.last_latency = time.time() - t0
                self.last_request = {
                    "method": method.upper(),
                    "path": path,
                    "latency_ms": int(self.last_latency * 1000),
                    "status_code": r.status_code,
                    "attempt": attempt,
                    "signed": bool(signed),
                }

                if r.status_code == 429:
                    retry_after = r.headers.get("Retry-After")
                    sleep_sec = float(retry_after) if retry_after else 2.0
                    logger.warning(f"Rate limited (429). Retry-After: {sleep_sec}s. Sleeping...")
                    time.sleep(sleep_sec)
                    if method.upper() == "GET" and attempt < max_attempts:
                        continue
                    raise BinanceAPIError(429, "Rate limit exceeded (HTTP 429)", raw={"status_code": 429})

                try:
                    data = r.json()
                except Exception as json_err:
                    r.raise_for_status()
                    raise json_err

                if isinstance(data, dict) and "code" in data and isinstance(data.get("code"), int) and data["code"] < 0:
                    code = data["code"]
                    if code == -1021 and signed and attempt < max_attempts:
                        logger.warning("Clock skew detected (-1021). Resyncing and retrying.")
                        self._clock.sync(self.base_url, session=self.session)
                        req_params["recvWindow"] = max(int(req_params.get("recvWindow", 5000)), 10000)
                        self._resign_params(req_params)
                        time.sleep(0.3)
                        continue
                    raise BinanceAPIError(code, data.get("msg", "Unknown error"), raw=data)

                break
            except BinanceAPIError as e:
                if e.code == -1021 and signed and attempt < max_attempts:
                    logger.warning("Clock skew detected (-1021). Resyncing and retrying.")
                    self._clock.sync(self.base_url, session=self.session)
                    req_params["recvWindow"] = max(int(req_params.get("recvWindow", 5000)), 10000)
                    self._resign_params(req_params)
                    time.sleep(0.3)
                    continue
                raise
            except (requests.Timeout, requests.ConnectionError) as e:
                if is_order_post and client_order_id and symbol_for_recovery:
                    recovered = self._recover_order_by_client_id(symbol_for_recovery, client_order_id)
                    if recovered:
                        logger.warning(
                            f"Recovered order via clientOrderId {client_order_id} after network error: {e}"
                        )
                        return recovered
                if method.upper() == "GET" and attempt < max_attempts:
                    backoff = self.get_retry_backoff_seconds * (2 ** (attempt - 1))
                    logger.warning(
                        f"GET request to {path} failed: {e}. Retrying {attempt}/{max_attempts} in {backoff}s..."
                    )
                    time.sleep(backoff)
                    if signed:
                        self._resign_params(req_params)
                    continue
                raise BinanceAPIError(-1, f"Request failed: {e}") from e
            except Exception as e:
                if is_order_post and client_order_id and symbol_for_recovery:
                    recovered = self._recover_order_by_client_id(symbol_for_recovery, client_order_id)
                    if recovered:
                        logger.warning(
                            f"Recovered order via clientOrderId {client_order_id} after error: {e}"
                        )
                        return recovered
                if method.upper() == "GET" and attempt < max_attempts:
                    backoff = self.get_retry_backoff_seconds * (2 ** (attempt - 1))
                    logger.warning(
                        f"GET request to {path} failed: {e}. Retrying {attempt}/{max_attempts} in {backoff}s..."
                    )
                    time.sleep(backoff)
                    if signed:
                        self._resign_params(req_params)
                    continue
                raise BinanceAPIError(-1, f"Request failed: {e}") from e

        return data

    def get_server_time(self) -> int:
        data = self._request("GET", "/api/v1/time")
        return int(data.get("serverTime", 0))

    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """Fetch 24hr ticker for a symbol."""
        sym = symbol.upper().replace("_", "")
        data = self._request("GET", "/api/v1/ticker/24hr", params={"symbol": sym})
        return {
            "symbol": symbol,
            "last": float(data.get("lastPrice", 0.0) or data.get("price", 0.0)),
            "bid": float(data.get("bidPrice", 0.0)),
            "ask": float(data.get("askPrice", 0.0)),
            "volume": float(data.get("volume", 0.0)),
            "high": float(data.get("highPrice", 0.0)),
            "low": float(data.get("lowPrice", 0.0)),
            "percent_change_24h": float(data.get("priceChangePercent", 0.0)),
            "raw": data
        }

    def get_candles(self, symbol: str, timeframe: str, limit: int = 250) -> List[List[Any]]:
        """Fetch candlestick (klines) data."""
        sym = symbol.upper().replace("_", "")
        interval = INTERVAL_MAP.get(timeframe, timeframe)
        return self._request("GET", "/api/v1/klines", params={
            "symbol": sym,
            "interval": interval,
            "limit": limit
        })

    def get_exchange_info(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        params = {}
        if symbol:
            params["symbol"] = symbol.upper().replace("_", "")
        return self._request("GET", "/api/v1/exchangeInfo", params=params)

    def _find_symbol_entry(self, symbols: List[Dict[str, Any]], sym: str) -> Optional[Dict[str, Any]]:
        """Resolve the exact symbol entry; exchangeInfo may return all pairs even when symbol is requested."""
        return find_symbol_entry(symbols, sym)

    def get_symbol_filters(self, symbol: str) -> Dict[str, Any]:
        """Public, cached lot/price/notional filters (gateway contract)."""
        return self._get_symbol_filters(symbol)

    def get_balances(self) -> Dict[str, Dict[str, float]]:
        """Fetch account snapshot balances."""
        data = self._request("GET", "/api/v1/account", signed=True)
        result: Dict[str, Dict[str, float]] = {}
        for entry in data.get("balances", []):
            asset = entry.get("asset", "").upper()
            if not asset:
                continue
            available = float(entry.get("free", 0.0))
            reserved = float(entry.get("locked", 0.0))
            if available > 0 or reserved > 0:
                result[asset] = {"available": available, "reserved": reserved}
        return result

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {}
        if symbol:
            params["symbol"] = symbol.upper().replace("_", "")
        return self._request("GET", "/api/v1/openOrders", signed=True, params=params)

    def get_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        sym = symbol.upper().replace("_", "")
        return self._request("GET", "/api/v1/order", signed=True, params={
            "symbol": sym,
            "orderId": str(order_id)
        })

    def get_order_by_client_id(self, symbol: str, client_id: str) -> Dict[str, Any]:
        sym = symbol.upper().replace("_", "")
        return self._request("GET", "/api/v1/order", signed=True, params={
            "symbol": sym,
            "origClientOrderId": str(client_id),
        })

    def cancel_order(self, symbol: str, order_id: str) -> None:
        sym = symbol.upper().replace("_", "")
        self._request("DELETE", "/api/v1/order", signed=True, params={
            "symbol": sym,
            "orderId": str(order_id)
        })

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,      # USDT for BUY, base coin for SELL
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        client_id: Optional[str] = None,
        post_only: bool = False,
        **kwargs
    ) -> Order:
        sym = symbol.upper().replace("_", "")
        side_upper = side.upper()
        ot_upper = order_type.upper()
        params = {
            "symbol": sym,
            "side": side_upper,
            "type": ot_upper,
        }
        
        f = self._get_symbol_filters(sym)
        
        if side_upper == "BUY":
            if ot_upper == "MARKET":
                qp = int(f.get("quoteAssetPrecision") or 8)
                qp = min(16, max(0, qp))
                quote_qty = _quantize_down(float(amount), qp)
                params["quoteOrderQty"] = _format_decimal(quote_qty, precision=qp)
            else:  # LIMIT
                if not price or price <= 0:
                    raise ValueError("Price is required for LIMIT BUY order")
                tick = float(f.get("tickSize") or 0.0)
                rounded_price = round_step(float(price), tick) if tick > 0 else float(price)
                price_prec = _decimal_places_from_step(tick) if tick > 0 else int(f.get("quoteAssetPrecision") or 8)
                price_str = _format_decimal(rounded_price, precision=price_prec)
                
                base_qty = float(amount) / rounded_price
                step = float(f.get("stepSize") or 0.0)
                rounded_qty = round_step(base_qty, step) if step > 0 else base_qty
                qty_prec = _decimal_places_from_step(step) if step > 0 else int(f.get("baseAssetPrecision") or 8)
                
                params["price"] = price_str
                params["quantity"] = _format_decimal(rounded_qty, precision=qty_prec)
                if post_only or ot_upper == "LIMIT_MAKER":
                    params["type"] = "LIMIT_MAKER"
                else:
                    params["type"] = "LIMIT"
                    params["timeInForce"] = "GTC"
        else: # SELL
            step = float(f.get("stepSize") or 0.0)
            rounded_qty = round_step(float(amount), step) if step > 0 else float(amount)
            qty_prec = _decimal_places_from_step(step) if step > 0 else int(f.get("baseAssetPrecision") or 8)
            params["quantity"] = _format_decimal(rounded_qty, precision=qty_prec)
            
            if ot_upper == "MARKET":
                params["type"] = "MARKET"
            else: # LIMIT / STOP_LOSS_LIMIT
                if not price or price <= 0:
                    raise ValueError("Price is required for LIMIT SELL order")
                tick = float(f.get("tickSize") or 0.0)
                rounded_price = round_step(float(price), tick) if tick > 0 else float(price)
                price_prec = _decimal_places_from_step(tick) if tick > 0 else int(f.get("quoteAssetPrecision") or 8)
                
                params["price"] = _format_decimal(rounded_price, precision=price_prec)
                if post_only or ot_upper == "LIMIT_MAKER":
                    params["type"] = "LIMIT_MAKER"
                elif ot_upper == "STOP_LOSS_LIMIT":
                    params["type"] = "STOP_LOSS_LIMIT"
                    params["timeInForce"] = "GTC"
                else:
                    params["type"] = "LIMIT"
                    params["timeInForce"] = "GTC"
                    
        if stop_price:
            params["stopPrice"] = self._format_price_string(symbol, stop_price)
            
        if client_id:
            params["newClientOrderId"] = str(client_id)[:36]
            
        res = self._request("POST", "/api/v1/order", signed=True, params=params)
        
        order_id = str(res.get("orderId") or res.get("id") or "")
        client_id_res = str(res.get("clientOrderId") or client_id or "")
        symbol_res = str(res.get("symbol") or symbol)
        side_res = str(res.get("side") or side)
        type_res = str(res.get("type") or order_type)
        price_res = float(res.get("price") or price or 0.0)
        amount_res = float(res.get("origQty") or res.get("executedQty") or amount)
        status_res = str(res.get("status") or "NEW")

        return Order(
            order_id=order_id,
            client_id=client_id_res,
            symbol=symbol_res,
            side=side_res,
            order_type=type_res,
            price=price_res,
            amount=amount_res,
            status=status_res,
            raw_payload=res
        )

    def _get_symbol_filters(self, symbol: str) -> Dict[str, Any]:
        sym = symbol.upper().replace("_", "")
        with self._exchange_info_lock:
            if sym in self._exchange_info_cache:
                return self._exchange_info_cache[sym]
        
        try:
            data = self.get_exchange_info(symbol=sym)
            symbols = data.get("symbols", [])
            entry = self._find_symbol_entry(symbols, sym) if symbols else None
            if entry:
                filters = self._parse_exchange_filters(entry)
                with self._exchange_info_lock:
                    self._exchange_info_cache[sym] = filters
                return filters
        except Exception as e:
            logger.warning(f"Failed to fetch exchange info for {sym}: {e}, using default filters")
        
        return {
            "stepSize": 0.000001,
            "tickSize": 0.000001,
            "stepSizeStr": "0.000001",
            "tickSizeStr": "0.000001",
            "minQty": 0.0,
            "minNotional": 10.0,
            "baseAssetPrecision": 8,
            "quoteAssetPrecision": 8,
        }

    def _parse_exchange_filters(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        return parse_exchange_filters(entry)

    def _format_qty_string(self, symbol: str, qty: float) -> str:
        f = self._get_symbol_filters(symbol)
        step = float(f.get("stepSize") or 0.0)
        rounded = round_step(float(qty), step) if step > 0 else float(qty)
        prec = _decimal_places_from_step(step) if step > 0 else int(f.get("baseAssetPrecision") or 8)
        return _format_decimal(rounded, precision=min(16, max(0, prec)))

    def _format_price_string(self, symbol: str, price: float) -> str:
        f = self._get_symbol_filters(symbol)
        tick = float(f.get("tickSize") or 0.0)
        rounded = round_step(float(price), tick) if tick > 0 else float(price)
        prec = _decimal_places_from_step(tick) if tick > 0 else int(f.get("quoteAssetPrecision") or 8)
        return _format_decimal(rounded, precision=min(16, max(0, prec)))

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass

    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        return self.get_ticker(symbol)

    def fetch_candles(self, symbol: str, timeframe: str, limit: int = 500) -> List[Candle]:
        raw = self.get_candles(symbol, timeframe, limit=limit)
        candles = []
        for k in raw:
            candles.append(Candle(
                timestamp=int(k[0]) // 1000,
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[5]),
            ))
        return candles

    def fetch_balances(self) -> Portfolio:
        raw = self.get_balances()
        balances = {}
        for asset, details in raw.items():
            avail = float(details.get("available") or 0.0)
            res_val = float(details.get("reserved") or 0.0)
            balances[asset] = avail + res_val
        return Portfolio(balances=balances, raw_payload=raw)
