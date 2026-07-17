"""Live exchange broker — thin wrapper delegating to engine client methods."""
from __future__ import annotations

import time
from typing import Any, Callable, Optional, Tuple

from xauby.engine.brokers.base import FillResult, SimPositionLedger

_ORDER_DONE_STATUSES = ("FILLED", "CANCELED", "REJECTED", "EXPIRED")


def _order_attr(order: Any, name: str, default: Any = None) -> Any:
    if isinstance(order, dict):
        aliases = {
            "order_id": ("orderId", "id"),
            "order_type": ("type", "orderType"),
            "raw_payload": ("raw",),
        }
        if name in order:
            return order.get(name, default)
        for key in aliases.get(name, ()):
            if key in order:
                return order.get(key, default)
        return default
    return getattr(order, name, default)


def _avg_fill_price(order: Any, filled_qty: float, fallback: float) -> float:
    raw = _order_attr(order, "raw_payload", None) or {}
    if isinstance(order, dict):
        raw = {**order, **raw}
    try:
        quote = float(
            raw.get("cummulativeQuoteQty", 0.0)
            or raw.get("cumulativeQuoteQty", 0.0)
            or raw.get("cost", 0.0)
            or 0.0
        )
        if quote > 0 and filled_qty > 0:
            return quote / filled_qty
    except (TypeError, ValueError):
        pass
    return float(_order_attr(order, "price", fallback) or fallback)


def _filled_base_quantity(
    order: Any,
    requested_base_qty: float,
    filled_order_qty: float,
) -> float:
    """Convert a native order fill back to the requested base-asset unit.

    CCXT derivative orders are submitted in contracts after the adapter
    converts ``amount_in_base=True``. Consequently ``executedQty``/``filled``
    are native contracts, while engine state and PnL use base quantity. Derive
    the fill fraction from the submitted native amount so this also handles
    partial fills without hard-coding a market's contract size.
    """
    requested = max(0.0, float(requested_base_qty or 0.0))
    filled = max(0.0, float(filled_order_qty or 0.0))
    if requested <= 0 or filled <= 0:
        return 0.0
    try:
        submitted_order_qty = float(_order_attr(order, "amount", 0.0) or 0.0)
    except (TypeError, ValueError):
        submitted_order_qty = 0.0
    if submitted_order_qty > 0:
        fill_fraction = min(1.0, filled / submitted_order_qty)
        return requested * fill_fraction
    # Gateways without an original amount are assumed to report base units.
    return min(requested, filled)


class LiveBroker:
    mode = "live"

    def __init__(
        self,
        client: Any,
        place_sl_fn: Callable[..., Optional[Tuple[str, float]]],
        quote_asset: str = "USDT",
        order_timeout_seconds: float = 10.0,
        order_poll_initial_seconds: float = 0.5,
        order_poll_max_seconds: float = 2.0,
    ):
        self.client = client
        self._place_sl = place_sl_fn
        self.quote_asset = str(quote_asset or "USDT").upper()
        self._order_timeout_seconds = max(0.0, float(order_timeout_seconds or 0.0))
        self._order_poll_initial_seconds = max(0.01, float(order_poll_initial_seconds or 0.5))
        self._order_poll_max_seconds = max(
            self._order_poll_initial_seconds, float(order_poll_max_seconds or 2.0)
        )

    def get_quote_balance(self) -> float:
        """Available cash balance in the exchange quote asset."""
        b = self.client.get_balances()
        return float(b.get(self.quote_asset, {}).get("available", 0.0))

    # Back-compat alias (the quote asset was historically assumed to be USDT).
    def get_usdt_balance(self) -> float:
        return self.get_quote_balance()

    def debit_usdt(self, amount: float) -> bool:
        return self.get_quote_balance() >= amount

    def credit_usdt(self, amount: float) -> None:
        return None

    def get_ledger(self, symbol: str) -> SimPositionLedger:
        return SimPositionLedger()

    def execute_buy(
        self,
        symbol: str,
        qty: float,
        price: float,
        notional: float,
    ) -> FillResult:
        return FillResult(success=False, error="LiveBroker.execute_buy delegates to OrderMixin live path")

    def execute_sell(
        self,
        symbol: str,
        qty: float,
        price: float,
        entry_price: float,
        entry_cost: float,
    ) -> FillResult:
        return FillResult(success=False, error="LiveBroker.execute_sell delegates to OrderMixin live path")

    def place_stop_loss(
        self,
        symbol: str,
        qty: float,
        stop_price: float,
    ) -> Optional[Tuple[str, float]]:
        return self._place_sl(qty, stop_price, symbol=symbol)

    def _confirm_fill(self, symbol: str, order: Any) -> Tuple[str, float]:
        """Poll the exchange until a swap MARKET order reaches a terminal status.

        MARKET orders are expected to fill immediately, but the create-order
        response can lag the matching engine. Treating that response as a
        confirmed fill let a stop-and-reverse flip local state before the
        exchange had actually closed/opened the position, causing a
        local/exchange position-side mismatch. Poll ``get_order`` instead of
        trusting the initial response.
        """
        status = str(_order_attr(order, "status", "") or "").upper()
        raw = _order_attr(order, "raw_payload", None) or {}
        if isinstance(order, dict):
            raw = {**order, **raw}
        filled = float(raw.get("executedQty", 0.0) or raw.get("filled", 0.0) or 0.0)
        if status == "FILLED" and filled <= 0:
            # Adapters without granular fill data (or the test gateway) only
            # populate Order.amount on a terminal status — treat that as fully
            # filled rather than misreading "no raw payload" as "not filled".
            filled = float(_order_attr(order, "amount", 0.0) or 0.0)
        elif status not in {"FILLED", "CANCELED"}:
            filled = 0.0
        order_id = _order_attr(order, "order_id", "")
        if status in _ORDER_DONE_STATUSES or not order_id:
            return status, filled

        elapsed = 0.0
        poll_interval = self._order_poll_initial_seconds
        while elapsed < self._order_timeout_seconds:
            time.sleep(min(poll_interval, self._order_timeout_seconds - elapsed))
            elapsed += poll_interval
            poll_interval = min(self._order_poll_max_seconds, poll_interval * 1.5)
            try:
                details = self.client.get_order(symbol, order_id)
                status = str(details.get("status", "") or "").upper()
                filled = float(details.get("executedQty", 0.0) or details.get("filled", 0.0) or 0.0)
                if status in _ORDER_DONE_STATUSES:
                    break
            except Exception:
                continue
        return status, filled

    def execute_open(self, symbol, position_side, qty, price, notional, leverage=1.0):
        side = "SELL" if str(position_side).upper() == "SHORT" else "BUY"
        order = self.client.place_order(
            symbol, side, "MARKET", qty, position_side=position_side,
            reduce_only=False, amount_in_base=True,
        )
        status, filled_order_qty = self._confirm_fill(symbol, order)
        filled_base_qty = _filled_base_quantity(order, qty, filled_order_qty)
        if status != "FILLED" or filled_base_qty <= 0:
            return FillResult(
                success=False,
                order_id=_order_attr(order, "order_id", ""),
                error=f"open order not confirmed filled (status={status or 'unknown'})",
            )
        return FillResult(True, qty=filled_base_qty, price=_avg_fill_price(order, filled_base_qty, price),
                          order_type=_order_attr(order, "order_type", "MARKET"),
                          order_id=_order_attr(order, "order_id", ""))

    def execute_close(self, symbol, position_side, qty, price, entry_price, entry_cost, funding_paid=0.0):
        side = "BUY" if str(position_side).upper() == "SHORT" else "SELL"
        order = self.client.place_order(
            symbol, side, "MARKET", qty, position_side=position_side,
            reduce_only=True, amount_in_base=True,
        )
        status, filled_order_qty = self._confirm_fill(symbol, order)
        filled_base_qty = _filled_base_quantity(order, qty, filled_order_qty)
        if filled_base_qty <= 0 or status not in {"FILLED", "CANCELED"}:
            return FillResult(
                success=False,
                order_id=_order_attr(order, "order_id", ""),
                error=f"close order not confirmed filled (status={status or 'unknown'})",
            )
        return FillResult(True, qty=filled_base_qty, price=_avg_fill_price(order, filled_base_qty, price),
                          order_type=_order_attr(order, "order_type", "MARKET"),
                          order_id=_order_attr(order, "order_id", ""))
