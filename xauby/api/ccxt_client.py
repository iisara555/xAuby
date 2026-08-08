import logging
import os
import time
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

from xauby.api.client import INTERVAL_MAP
from xauby.api.errors import ExchangeAPIError
from xauby.api.interface import IExchangeGateway
from xauby.domain.models import Candle, MarketIdentity, Order, Portfolio
from xauby.runtime.derivatives_config import derivatives_settings

logger = logging.getLogger("lite_api")


class CCXTAPIError(ExchangeAPIError):
    def __init__(self, code: int | str, msg: str, raw: Any = None):
        super().__init__(code, msg, raw=raw)
        self.args = (f"CCXTAPIError({code}): {msg}",)


class CCXTExchangeClient(IExchangeGateway):
    """CCXT REST adapter with the Binance-shaped methods the engine uses today."""

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        base_url: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        exchange_instance: Optional[Any] = None,
    ):
        self.config = config or {}
        exchange_cfg = self.config.get("exchange") or {}
        self.exchange_id = str(
            exchange_cfg.get("ccxt_id") or exchange_cfg.get("id") or exchange_cfg.get("name") or "binance"
        ).lower()
        self.quote_asset = str(
            exchange_cfg.get("quote_asset")
            or (self.config.get("portfolio") or {}).get("quote_asset")
            or "USDT"
        ).upper()
        self.last_latency = 0.0
        self.last_request: Dict[str, Any] = {}
        self._markets_loaded = False
        self._okx_algo_order_ids: set[str] = set()
        self.derivatives = derivatives_settings(self.config)

        if exchange_instance is not None:
            self.exchange = exchange_instance
        else:
            try:
                import ccxt  # type: ignore
            except Exception as e:
                raise CCXTAPIError(
                    "ccxt_missing",
                    "ccxt is not installed. Install project requirements before using exchange.provider=ccxt.",
                ) from e

            exchange_class = getattr(ccxt, self.exchange_id, None)
            if exchange_class is None:
                raise CCXTAPIError("unknown_exchange", f"Unknown CCXT exchange id: {self.exchange_id}")
            prefix = self.exchange_id.upper()
            passphrase = (
                os.environ.get(f"{prefix}_API_PASSPHRASE")
                or os.environ.get(f"{prefix}_PASSWORD")
                or os.environ.get(f"{prefix}_API_PASSWORD")
            )
            params = dict(exchange_cfg.get("params") or {})
            options = dict(params.get("options") or {})
            options.setdefault("defaultType", self.derivatives["market_type"])
            params["options"] = options
            params.update(
                {
                    "apiKey": api_key or params.get("apiKey", ""),
                    "secret": api_secret or params.get("secret", ""),
                    "enableRateLimit": bool(exchange_cfg.get("enable_rate_limit", True)),
                }
            )
            if passphrase:
                params.setdefault("password", passphrase)
            self.exchange = exchange_class(params)
            if base_url:
                self._apply_base_url_override(base_url)
            if bool(exchange_cfg.get("sandbox", False)) and hasattr(self.exchange, "set_sandbox_mode"):
                self.exchange.set_sandbox_mode(True)

        # Optional outbound resilience (token bucket + circuit breaker). Gated by
        # architecture.api_circuit_breaker_enabled; None when off (no-op path).
        from xauby.api.resilience import always_allow_methods, build_guard

        self._resilience = build_guard(self.config)
        # Order-mutating calls bypass the breaker gate — see ResilienceGuard.run.
        self._resilience_always_allow = always_allow_methods(self.config)

        capabilities = exchange_cfg.get("capabilities") or {}
        # Explicit config wins; otherwise probe the venue's real ccxt `has` map so
        # migrating to a new exchange auto-detects exchange-side stop support
        # instead of silently defaulting to local-only SL monitoring.
        explicit_sl = capabilities.get("stop_loss_limit")
        supports_sl = (
            bool(explicit_sl) if explicit_sl is not None else self._probe_stop_loss_support()
        )
        self.capabilities = {
            "supports_stop_loss_limit": supports_sl,
            "positions": self.derivatives["market_type"] == "swap",
            "position_history": bool(
                (getattr(self.exchange, "has", None) or {}).get("fetchPositionsHistory")
            ),
            "reduce_only": self.derivatives["market_type"] == "swap",
            "swap": self.derivatives["market_type"] == "swap",
            "market_data": ("ticker", "candle", "trades", "order_book"),
        }

    def _probe_stop_loss_support(self) -> bool:
        """Detect exchange-side stop-order support from ccxt's ``has`` map.

        Consulted only when ``exchange.capabilities.stop_loss_limit`` is unset in
        config, so a new venue is auto-classified rather than assumed unsupported.
        """
        has = getattr(self.exchange, "has", None) or {}
        return bool(
            has.get("createStopLimitOrder")
            or has.get("createStopOrder")
            or has.get("createStopMarketOrder")
        )

    def supports_exchange_stop_loss(self) -> bool:
        """Whether this adapter can place exchange-side ``STOP_LOSS_LIMIT`` orders."""
        return bool(self.capabilities.get("supports_stop_loss_limit"))

    def _apply_base_url_override(self, base_url: str) -> None:
        """Override API hosts without destroying CCXT endpoint maps."""
        urls = getattr(self.exchange, "urls", None)
        if not isinstance(urls, dict):
            return
        override = str(base_url).rstrip("/")
        api_urls = urls.get("api")
        if isinstance(api_urls, dict):
            urls["api"] = {key: override for key in api_urls}
        else:
            urls["api"] = override

    @staticmethod
    def make_client_id(prefix: str = "xb") -> str:
        safe = "".join(c for c in prefix if c.isalnum() or c in "-_")[:12]
        return f"xauby-{safe}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"

    def _format_client_order_id(self, client_id: str) -> str:
        raw = str(client_id or "")
        if self.exchange_id == "okx":
            # OKX `clOrdId` only accepts case-sensitive alphanumerics, max 32.
            safe = "".join(c for c in raw if c.isalnum())[:32]
            return safe or self.make_client_id("xb").replace("-", "")[:32]
        return raw[:36]

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        fn = getattr(self.exchange, method)
        t0 = time.time()
        try:
            if self._resilience is not None:
                result = self._resilience.run(
                    fn, *args, label=method,
                    critical=method in self._resilience_always_allow,
                    **kwargs,
                )
            else:
                result = fn(*args, **kwargs)
            self.last_request = {"method": method, "args": args, "kwargs": kwargs}
            return result
        except CCXTAPIError:
            raise
        except Exception as e:
            code = getattr(e, "code", None) or e.__class__.__name__
            raise CCXTAPIError(code, str(e), raw={"method": method}) from e
        finally:
            self.last_latency = time.time() - t0

    def _load_markets(self) -> Dict[str, Any]:
        markets = getattr(self.exchange, "markets", None)
        if not markets or not self._markets_loaded:
            if hasattr(self.exchange, "load_markets"):
                markets = self._call("load_markets")
            else:
                markets = getattr(self.exchange, "markets", {}) or {}
            self._markets_loaded = True
        return markets or {}

    def _to_ccxt_symbol(self, symbol: str) -> str:
        markets = self._load_markets()
        raw = str(symbol).strip().upper().replace("_", "")
        if "/" in str(symbol):
            candidate = str(symbol).strip().upper()
            if not markets or candidate in markets:
                return candidate
        if raw in markets:
            return raw
        markets_by_id = getattr(self.exchange, "markets_by_id", None) or {}
        by_id = markets_by_id.get(raw)
        if isinstance(by_id, list) and by_id:
            return str(by_id[0].get("symbol") or raw)
        if isinstance(by_id, dict):
            return str(by_id.get("symbol") or raw)
        matches = []
        for market_symbol, market in markets.items():
            market_id = str((market or {}).get("id") or "").upper().replace("_", "")
            if market_id == raw:
                matches.append((market_symbol, market or {}))
        if self.derivatives["market_type"] == "swap":
            for market_symbol, market in matches:
                if market.get("swap") and str(market.get("settle") or "").upper() == self.derivatives["settle_asset"]:
                    return market_symbol
            base = raw[: -len(self.quote_asset)] if raw.endswith(self.quote_asset) else raw
            candidate = f"{base}/{self.quote_asset}:{self.derivatives['settle_asset']}"
            if candidate in markets:
                return candidate
        if matches:
            return matches[0][0]
        if raw.endswith(self.quote_asset):
            base = raw[: -len(self.quote_asset)]
            return f"{base}/{self.quote_asset}"
        return raw

    @staticmethod
    def _from_ccxt_symbol(symbol: str, fallback: str = "") -> str:
        raw = str(symbol or fallback).split(":", 1)[0]
        value = raw.upper().replace("/", "").replace("_", "")
        return value

    @staticmethod
    def _normalize_status(status: Any) -> str:
        value = str(status or "").upper()
        mapping = {
            "OPEN": "NEW",
            "CLOSED": "FILLED",
            "CANCELED": "CANCELED",
            "CANCELLED": "CANCELED",
        }
        return mapping.get(value, value or "NEW")

    @staticmethod
    def _precision_digits(value: Any, default: int = 8) -> int:
        if value is None:
            return default
        try:
            dec = Decimal(str(value))
        except Exception:
            return default
        if dec <= 0:
            return default
        if dec >= 1 and dec == dec.to_integral_value():
            return int(dec)
        exponent = dec.normalize().as_tuple().exponent
        return min(16, max(0, -int(exponent)))

    @classmethod
    def _precision_step(cls, value: Any, default: str = "0.00000001") -> str:
        if value is None:
            return default
        try:
            dec = Decimal(str(value))
        except Exception:
            return default
        if dec <= 0:
            return default
        if dec >= 1 and dec == dec.to_integral_value():
            places = min(16, max(0, int(dec)))
            return format(Decimal(1).scaleb(-places), "f")
        return format(dec, "f").rstrip("0").rstrip(".") or default

    def _normalize_order(self, order: Dict[str, Any], fallback_symbol: str = "") -> Dict[str, Any]:
        symbol = self._from_ccxt_symbol(order.get("symbol"), fallback_symbol)
        order_id = str(order.get("id") or order.get("orderId") or "")
        client_id = str(
            order.get("clientOrderId")
            or order.get("clOrdId")
            or (order.get("info") or {}).get("clientOrderId")
            or (order.get("info") or {}).get("clientOrderID")
            or (order.get("info") or {}).get("clOrdId")
            or ""
        )
        filled = float(order.get("filled") or order.get("executedQty") or 0.0)
        amount = float(order.get("amount") or order.get("origQty") or filled or 0.0)
        normalized = {
            "orderId": order_id,
            "id": order_id,
            "clientOrderId": client_id,
            "symbol": symbol,
            "side": str(order.get("side") or "").upper(),
            "type": str(order.get("type") or "").upper(),
            "price": float(order.get("price") or 0.0),
            "origQty": amount,
            "amount": amount,
            "executedQty": filled,
            "filled": filled,
            "status": self._normalize_status(order.get("status")),
            "raw": order,
        }
        return normalized

    @staticmethod
    def _is_missing_order_error(exc: ExchangeAPIError) -> bool:
        code = str(getattr(exc, "code", "") or "").strip().lower()
        message = str(exc).lower()
        return code in {"-2013", "-2026", "ordernotfound", "51603", "51400"} or any(
            marker in message
            for marker in (
                '"code":"51603"',
                '"scode":"51400"',
                "order does not exist",
                "filled, canceled or does not exist",
            )
        )

    def _okx_algo_request_params(self, symbol: str, order_id: Optional[str] = None) -> Dict[str, Any]:
        ccxt_symbol = self._to_ccxt_symbol(symbol)
        market = self._load_markets().get(ccxt_symbol, {}) or {}
        params: Dict[str, Any] = {
            "instId": str(market.get("id") or ccxt_symbol),
        }
        if order_id is not None:
            params["algoId"] = str(order_id)
        return params

    def _normalize_okx_algo_order(
        self,
        row: Dict[str, Any],
        fallback_symbol: str,
    ) -> Dict[str, Any]:
        markets = self._load_markets()
        ccxt_symbol = self._to_ccxt_symbol(fallback_symbol) if fallback_symbol else ""
        market = markets.get(ccxt_symbol, {}) or {}
        if not market:
            inst_id = str(row.get("instId") or "")
            for candidate_symbol, candidate_market in markets.items():
                if str((candidate_market or {}).get("id") or "") == inst_id:
                    ccxt_symbol = candidate_symbol
                    market = candidate_market or {}
                    break
        contract_size = float(market.get("contractSize") or 1.0)
        amount = float(row.get("sz") or 0.0) * contract_size
        filled = float(row.get("actualSz") or 0.0) * contract_size
        average = float(row.get("actualPx") or 0.0)
        raw_state = str(row.get("state") or "").lower()
        status = {
            "live": "NEW",
            "pause": "NEW",
            "partially_effective": "PARTIALLY_FILLED",
            "canceled": "CANCELED",
            "order_failed": "REJECTED",
        }.get(raw_state, "NEW")
        order_id = str(row.get("algoId") or "")
        trigger_price = float(
            row.get("triggerPx") or row.get("slTriggerPx") or row.get("tpTriggerPx") or 0.0
        )
        order_price = float(
            row.get("orderPx") or row.get("slOrdPx") or row.get("tpOrdPx") or average or 0.0
        )
        return {
            "orderId": order_id,
            "id": order_id,
            "clientOrderId": str(row.get("algoClOrdId") or ""),
            "symbol": self._from_ccxt_symbol(ccxt_symbol, fallback_symbol),
            "side": str(row.get("side") or "").upper(),
            "type": "STOP_LOSS_LIMIT",
            "price": order_price,
            "stopPrice": trigger_price,
            "origQty": amount,
            "amount": amount,
            "executedQty": filled,
            "filled": filled,
            "cummulativeQuoteQty": filled * average,
            "status": status,
            "raw": row,
        }

    def _get_okx_algo_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        payload = self._call(
            "privateGetTradeOrderAlgo",
            self._okx_algo_request_params(symbol, order_id),
        ) or {}
        rows = payload.get("data") or []
        if len(rows) != 1:
            raise CCXTAPIError(
                "OrderNotFound",
                f"OKX algo order {order_id} does not exist",
                raw=payload,
            )
        row = rows[0]
        self._okx_algo_order_ids.add(str(order_id))

        # Once an algo has triggered, OKX may expose the resulting regular
        # order id. Prefer that order's exact fill state over guessing from the
        # algo lifecycle state.
        regular_order_id = str(row.get("ordId") or "")
        if regular_order_id and str(row.get("state") or "").lower() in {
            "effective",
            "partially_effective",
        }:
            try:
                ccxt_symbol = self._to_ccxt_symbol(symbol)
                regular = self._call("fetch_order", regular_order_id, ccxt_symbol)
                return self._normalize_order(regular, fallback_symbol=symbol)
            except ExchangeAPIError:
                pass
        return self._normalize_okx_algo_order(row, fallback_symbol=symbol)

    def _cancel_okx_algo_order(self, symbol: str, order_id: str) -> None:
        params = self._okx_algo_request_params(symbol, order_id)
        payload = self._call("privatePostTradeCancelAlgos", [params]) or {}
        rows = payload.get("data") or []
        if str(payload.get("code") or "") != "0" or len(rows) != 1 or str(rows[0].get("sCode") or "") != "0":
            row = rows[0] if rows else {}
            raise CCXTAPIError(
                row.get("sCode") or payload.get("code") or "cancel_algo_failed",
                row.get("sMsg") or payload.get("msg") or f"Failed to cancel OKX algo order {order_id}",
                raw=payload,
            )
        self._okx_algo_order_ids.discard(str(order_id))

    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        ccxt_symbol = self._to_ccxt_symbol(symbol)
        data = self._call("fetch_ticker", ccxt_symbol)
        return {
            "symbol": self._from_ccxt_symbol(data.get("symbol"), symbol),
            "last": float(data.get("last") or data.get("close") or 0.0),
            "bid": float(data.get("bid") or 0.0),
            "ask": float(data.get("ask") or 0.0),
            "volume": float(data.get("baseVolume") or data.get("quoteVolume") or 0.0),
            "high": float(data.get("high") or 0.0),
            "low": float(data.get("low") or 0.0),
            "percent_change_24h": float(data.get("percentage") or 0.0),
            "raw": data,
        }

    def get_candles(self, symbol: str, timeframe: str, limit: int = 250) -> List[List[Any]]:
        ccxt_symbol = self._to_ccxt_symbol(symbol)
        interval = INTERVAL_MAP.get(timeframe, timeframe)
        return self._call("fetch_ohlcv", ccxt_symbol, timeframe=interval, limit=limit)

    def market_identity(self, symbol: str) -> MarketIdentity:
        native = self._to_ccxt_symbol(symbol)
        return MarketIdentity(
            exchange=self.exchange_id,
            market_type=self.derivatives["market_type"],
            settle_asset=self.derivatives["settle_asset"],
            canonical_symbol=self._from_ccxt_symbol(symbol),
            exchange_symbol=native,
        )

    def get_recent_trades(self, symbol: str, limit: int = 100) -> List[Dict[str, Any]]:
        return list(self._call("fetch_trades", self._to_ccxt_symbol(symbol), limit=limit) or [])

    def get_order_book(self, symbol: str, limit: int = 50) -> Dict[str, Any]:
        return dict(self._call("fetch_order_book", self._to_ccxt_symbol(symbol), limit) or {})

    def get_positions(self, symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        if self.derivatives["market_type"] != "swap":
            return []
        native = [self._to_ccxt_symbol(s) for s in symbols] if symbols else None
        rows = self._call("fetch_positions", native) if native else self._call("fetch_positions")
        result = []
        for p in rows or []:
            contracts = float(p.get("contracts") or 0.0)
            side = str(p.get("side") or "").upper()
            if contracts <= 0 or side not in ("LONG", "SHORT"):
                continue
            native_symbol = str(p.get("symbol") or "")
            market = self._load_markets().get(native_symbol, {}) or {}
            contract_size = float(market.get("contractSize") or 1.0)
            result.append({
                "symbol": self._from_ccxt_symbol(p.get("symbol")),
                "exchange_position_id": str(
                    p.get("id") or ((p.get("info") or {}).get("posId")) or ""
                ) or None,
                "position_side": side,
                "quantity": contracts * contract_size,
                "contracts": contracts,
                "contract_size": contract_size,
                "entry_price": float(p.get("entryPrice") or 0.0),
                "mark_price": float(p.get("markPrice") or 0.0),
                "liquidation_price": float(p.get("liquidationPrice") or 0.0),
                "leverage": float(p.get("leverage") or 1.0),
                "unrealized_pnl": float(p.get("unrealizedPnl") or 0.0),
                "margin_mode": str(p.get("marginMode") or self.derivatives["margin_mode"]),
                "raw": p,
            })
        return result

    def get_position_history(
        self,
        symbol: str,
        since: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Return normalized, exchange-authoritative derivative close history.

        OKX position history exposes cumulative realized PnL for a completed
        position. Its ``posId`` can be reused across consecutive one-way
        positions, so ``exchange_close_id`` also includes the update timestamp.
        """
        if self.derivatives["market_type"] != "swap":
            return []
        if not self.capabilities.get("position_history"):
            raise CCXTAPIError(
                "position_history_unsupported",
                f"{self.exchange_id} does not expose fetchPositionsHistory",
            )

        native = self._to_ccxt_symbol(symbol)
        params = {"instType": "SWAP"}
        margin_mode = str(self.derivatives.get("margin_mode") or "").lower()
        if margin_mode in {"cross", "isolated"}:
            params["marginMode"] = margin_mode
        rows = self._call(
            "fetch_positions_history",
            [native],
            since,
            max(1, min(int(limit), 100)),
            params,
        )
        market = self._load_markets().get(native, {}) or {}
        contract_size = float(market.get("contractSize") or 1.0)
        result: List[Dict[str, Any]] = []
        for position in rows or []:
            raw = position.get("info") or {}
            position_id = str(position.get("id") or raw.get("posId") or "")
            closed_ms = int(
                position.get("lastUpdateTimestamp") or raw.get("uTime") or 0
            )
            opened_ms = int(position.get("timestamp") or raw.get("cTime") or 0)
            raw_side = str(raw.get("direction") or "").upper()
            parsed_side = str(position.get("side") or "").upper()
            side = raw_side if raw_side in {"LONG", "SHORT"} else parsed_side
            if side not in {"LONG", "SHORT"}:
                continue
            contracts = float(raw.get("closeTotalPos") or position.get("contracts") or 0.0)
            fee_signed = float(raw.get("fee") or 0.0)
            funding_fee = float(raw.get("fundingFee") or 0.0)
            realized_pnl = float(
                position.get("realizedPnl")
                if position.get("realizedPnl") is not None
                else raw.get("realizedPnl") or 0.0
            )
            exchange_close_id = (
                f"{self.exchange_id}:{position_id}:{closed_ms}"
                if position_id and closed_ms
                else f"{self.exchange_id}:{self._from_ccxt_symbol(native)}:{closed_ms}"
            )
            result.append(
                {
                    "exchange_close_id": exchange_close_id,
                    "exchange_position_id": position_id or None,
                    "exchange": self.exchange_id,
                    "symbol": self._from_ccxt_symbol(
                        position.get("symbol"), raw.get("instId") or symbol
                    ),
                    "position_side": side,
                    "quantity": contracts * contract_size,
                    "contracts": contracts,
                    "contract_size": contract_size,
                    "entry_price": float(
                        position.get("entryPrice") or raw.get("openAvgPx") or 0.0
                    ),
                    "exit_price": float(
                        position.get("lastPrice") or raw.get("closeAvgPx") or 0.0
                    ),
                    "realized_pnl": realized_pnl,
                    "gross_pnl": float(raw.get("pnl") or 0.0),
                    "fee": fee_signed,
                    "fee_cost": -fee_signed,
                    "funding_fee": funding_fee,
                    "liquidation_penalty": float(raw.get("liqPenalty") or 0.0),
                    "close_type": str(raw.get("type") or ""),
                    "opened_timestamp": opened_ms,
                    "closed_timestamp": closed_ms,
                    "opened_at": self.exchange.iso8601(opened_ms) if opened_ms else None,
                    "closed_at": self.exchange.iso8601(closed_ms) if closed_ms else None,
                    "margin_mode": str(raw.get("mgnMode") or ""),
                    "leverage": float(raw.get("lever") or 1.0),
                    "raw": raw,
                }
            )
        return result

    def get_funding_rate(self, symbol: str) -> Dict[str, Any]:
        if self.derivatives["market_type"] != "swap":
            return {"symbol": self._from_ccxt_symbol(symbol), "funding_rate": 0.0}
        row = self._call("fetch_funding_rate", self._to_ccxt_symbol(symbol))
        return {
            "symbol": self._from_ccxt_symbol(row.get("symbol"), symbol),
            "funding_rate": float(row.get("fundingRate") or 0.0),
            "next_funding_timestamp": row.get("fundingTimestamp") or row.get("nextFundingTimestamp"),
            "raw": row,
        }

    def set_leverage(self, symbol: str, leverage: float) -> Any:
        value = float(leverage)
        if not (1.0 <= value <= self.derivatives["max_leverage"]):
            raise ValueError(f"leverage must be between 1 and {self.derivatives['max_leverage']}")
        return self._call(
            "set_leverage", value, self._to_ccxt_symbol(symbol),
            {"marginMode": self.derivatives["margin_mode"], "mgnMode": self.derivatives["margin_mode"]},
        )

    def set_margin_mode(self, symbol: str, margin_mode: Optional[str] = None) -> Any:
        mode = str(margin_mode or self.derivatives["margin_mode"]).lower()
        if mode != "isolated":
            raise ValueError("v1 perpetual support only permits isolated margin")
        params = {"lever": str(int(float(self.derivatives.get("default_leverage") or 1)))}
        return self._call("set_margin_mode", mode, self._to_ccxt_symbol(symbol), params)

    def get_exchange_info(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        markets = self._load_markets()
        target = self._from_ccxt_symbol(symbol or "") if symbol else ""
        symbols = []
        for market_symbol, market in markets.items():
            if self.derivatives["market_type"] == "swap" and not bool((market or {}).get("swap")):
                continue
            if self.derivatives["market_type"] == "spot" and bool((market or {}).get("contract")):
                continue
            normalized = self._from_ccxt_symbol(market_symbol, market.get("id") if isinstance(market, dict) else "")
            if target and normalized != target:
                continue
            if not isinstance(market, dict):
                market = {}
            precision = market.get("precision") or {}
            limits = market.get("limits") or {}
            amount_limits = limits.get("amount") or {}
            cost_limits = limits.get("cost") or {}
            amount_precision = precision.get("amount")
            price_precision = precision.get("price")
            contract_size = (
                Decimal(str(market.get("contractSize") or 1.0))
                if self.derivatives["market_type"] == "swap"
                else Decimal("1")
            )
            amount_min = Decimal(str(amount_limits.get("min") or "0")) * contract_size
            amount_step = Decimal(
                self._precision_step(amount_precision, "0.00000001")
            ) * contract_size
            symbols.append(
                {
                    "symbol": normalized,
                    "status": "TRADING" if market.get("active", True) else "BREAK",
                    "baseAsset": str(market.get("base") or "").upper(),
                    "quoteAsset": str(market.get("quote") or "").upper(),
                    "baseAssetPrecision": self._precision_digits(amount_step, 8),
                    "quoteAssetPrecision": self._precision_digits(price_precision, 8),
                    "filters": [
                        {
                            "filterType": "LOT_SIZE",
                            "minQty": format(amount_min, "f"),
                            "stepSize": format(amount_step, "f"),
                        },
                        {
                            "filterType": "PRICE_FILTER",
                            "tickSize": self._precision_step(price_precision, "0.00000001"),
                        },
                        {
                            "filterType": "MIN_NOTIONAL",
                            "minNotional": str(cost_limits.get("min") or "0"),
                        },
                    ],
                }
            )
        return {"symbols": symbols}

    def get_balances(self) -> Dict[str, Dict[str, float]]:
        data = self._call("fetch_balance")
        result: Dict[str, Dict[str, float]] = {}
        free = data.get("free") or {}
        used = data.get("used") or {}
        total = data.get("total") or {}
        assets = set(free) | set(used) | set(total)
        for asset in assets:
            asset_key = str(asset).upper()
            available = float(free.get(asset) or 0.0)
            reserved = float(used.get(asset) or 0.0)
            total_value = float(total.get(asset) or available + reserved)
            if available > 0 or reserved > 0 or total_value > 0:
                result[asset_key] = {"available": available, "reserved": reserved}
        return result

    def get_trading_fee(self, symbol: str) -> Dict[str, float]:
        """Return account-tier maker/taker rates for a symbol.

        The exchange adapter owns symbol translation; callers remain exchange
        neutral and can feature-detect this optional capability.
        """
        ccxt_symbol = self._to_ccxt_symbol(symbol)
        data = self._call("fetch_trading_fee", ccxt_symbol) or {}
        return {
            "maker": float(data.get("maker") or 0.0),
            "taker": float(data.get("taker") or 0.0),
        }

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        ccxt_symbol = self._to_ccxt_symbol(symbol) if symbol else None
        if ccxt_symbol:
            orders = self._call("fetch_open_orders", ccxt_symbol)
        else:
            orders = self._call("fetch_open_orders")
        normalized = [self._normalize_order(o, fallback_symbol=symbol or "") for o in orders or []]
        if self.exchange_id == "okx" and self.derivatives["market_type"] == "swap":
            params: Dict[str, Any] = {"instType": "SWAP", "ordType": "trigger"}
            if symbol:
                params.update(self._okx_algo_request_params(symbol))
            payload = self._call("privateGetTradeOrdersAlgoPending", params) or {}
            for row in payload.get("data") or []:
                algo = self._normalize_okx_algo_order(row, fallback_symbol=symbol or "")
                self._okx_algo_order_ids.add(str(algo.get("orderId") or ""))
                normalized.append(algo)
        deduped: Dict[str, Dict[str, Any]] = {}
        for order in normalized:
            key = str(order.get("orderId") or order.get("clientOrderId") or "")
            deduped[key or f"anonymous-{len(deduped)}"] = order
        return list(deduped.values())

    def get_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        if (
            self.exchange_id == "okx"
            and self.derivatives["market_type"] == "swap"
            and str(order_id) in self._okx_algo_order_ids
        ):
            return self._get_okx_algo_order(symbol, str(order_id))
        ccxt_symbol = self._to_ccxt_symbol(symbol)
        try:
            order = self._call("fetch_order", str(order_id), ccxt_symbol)
            return self._normalize_order(order, fallback_symbol=symbol)
        except ExchangeAPIError as exc:
            if (
                self.exchange_id == "okx"
                and self.derivatives["market_type"] == "swap"
                and self._is_missing_order_error(exc)
            ):
                return self._get_okx_algo_order(symbol, str(order_id))
            raise

    def cancel_order(self, symbol: str, order_id: str) -> None:
        if (
            self.exchange_id == "okx"
            and self.derivatives["market_type"] == "swap"
            and str(order_id) in self._okx_algo_order_ids
        ):
            self._cancel_okx_algo_order(symbol, str(order_id))
            return
        ccxt_symbol = self._to_ccxt_symbol(symbol)
        try:
            self._call("cancel_order", str(order_id), ccxt_symbol)
        except ExchangeAPIError as exc:
            if (
                self.exchange_id == "okx"
                and self.derivatives["market_type"] == "swap"
                and self._is_missing_order_error(exc)
            ):
                self._cancel_okx_algo_order(symbol, str(order_id))
                return
            raise

    def _base_amount_for_order(
        self,
        ccxt_symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: Optional[float],
    ) -> float:
        if side.upper() != "BUY":
            return float(amount)
        if order_type.upper() == "MARKET":
            ticker = self.get_ticker(ccxt_symbol)
            last = float(ticker.get("last") or 0.0)
            if last <= 0:
                raise CCXTAPIError("invalid_price", f"Cannot convert quote amount for market buy: {ccxt_symbol}")
            return float(amount) / last
        if not price or price <= 0:
            raise ValueError("Price is required for LIMIT BUY order")
        return float(amount) / float(price)

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        client_id: Optional[str] = None,
        post_only: bool = False,
        **kwargs: Any,
    ) -> Order:
        ccxt_symbol = self._to_ccxt_symbol(symbol)
        side_lc = side.lower()
        order_type_uc = order_type.upper()
        if order_type_uc == "STOP_LOSS_LIMIT" and not self.capabilities.get("supports_stop_loss_limit"):
            raise CCXTAPIError(
                "unsupported_order_type",
                "STOP_LOSS_LIMIT is not enabled for this CCXT adapter. Use local SL monitoring or enable an exchange-specific adapter.",
            )
        type_lc = "limit" if order_type_uc in ("LIMIT_MAKER", "STOP_LOSS_LIMIT") else order_type.lower()
        if self.derivatives["market_type"] == "swap":
            market = self._load_markets().get(ccxt_symbol, {}) or {}
            contract_size = float(market.get("contractSize") or 1.0)
            amount_in_base = bool(kwargs.get("amount_in_base", True))
            base_amount = float(amount) / contract_size if amount_in_base else float(amount)
        else:
            base_amount = self._base_amount_for_order(ccxt_symbol, side, order_type, amount, price)
        params = dict(kwargs.get("params") or {})
        if self.derivatives["market_type"] == "swap":
            params.setdefault("tdMode", self.derivatives["margin_mode"])
            params.setdefault("marginMode", self.derivatives["margin_mode"])
            if "reduce_only" in kwargs:
                params.setdefault("reduceOnly", bool(kwargs["reduce_only"]))
            position_side = str(kwargs.get("position_side") or "").lower()
            if position_side and self.derivatives["position_mode"] != "one_way":
                params.setdefault("posSide", position_side)
        if client_id:
            formatted_client_id = self._format_client_order_id(client_id)
            params.setdefault("clientOrderId", formatted_client_id)
            params.setdefault("newClientOrderId", formatted_client_id)
        if post_only or order_type_uc == "LIMIT_MAKER":
            params.setdefault("postOnly", True)
        if stop_price is not None:
            params.setdefault("stopPrice", stop_price)

        order_price = float(price) if price is not None and type_lc != "market" else None
        data = self._call("create_order", ccxt_symbol, type_lc, side_lc, base_amount, order_price, params)
        normalized = self._normalize_order(data, fallback_symbol=symbol)
        if (
            order_type_uc == "STOP_LOSS_LIMIT"
            and self.exchange_id == "okx"
            and self.derivatives["market_type"] == "swap"
            and normalized["orderId"]
        ):
            self._okx_algo_order_ids.add(str(normalized["orderId"]))
        return Order(
            order_id=normalized["orderId"],
            client_id=normalized["clientOrderId"] or client_id or "",
            symbol=normalized["symbol"],
            side=normalized["side"],
            order_type=normalized["type"] or order_type_uc,
            price=float(normalized["price"] or price or 0.0),
            amount=float(normalized["origQty"] or base_amount),
            status=normalized["status"],
            raw_payload=normalized,
        )

    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        return self.get_ticker(symbol)

    def fetch_candles(self, symbol: str, timeframe: str, limit: int = 500) -> List[Candle]:
        raw = self.get_candles(symbol, timeframe, limit=limit)
        return [
            Candle(
                timestamp=int(k[0]) // 1000,
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[5]),
            )
            for k in raw
        ]

    def fetch_balances(self) -> Portfolio:
        raw = self.get_balances()
        balances = {
            asset: float(details.get("available") or 0.0) + float(details.get("reserved") or 0.0)
            for asset, details in raw.items()
        }
        return Portfolio(balances=balances, raw_payload=raw)

    def check_clock_sync(self) -> bool:
        return True

    def close(self) -> None:
        close_fn = getattr(self.exchange, "close", None)
        if callable(close_fn):
            try:
                close_fn()
            except Exception:
                logger.debug("CCXT exchange close failed", exc_info=True)
