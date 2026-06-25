"""Live exchange broker — thin wrapper delegating to engine client methods."""
from __future__ import annotations

from typing import Any, Callable, Optional, Tuple

from xauby.engine.brokers.base import FillResult, SimPositionLedger


class LiveBroker:
    mode = "live"

    def __init__(
        self,
        client: Any,
        place_sl_fn: Callable[..., Optional[Tuple[str, float]]],
        quote_asset: str = "USDT",
    ):
        self.client = client
        self._place_sl = place_sl_fn
        self.quote_asset = str(quote_asset or "USDT").upper()

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

    def execute_open(self, symbol, position_side, qty, price, notional, leverage=1.0):
        side = "SELL" if str(position_side).upper() == "SHORT" else "BUY"
        order = self.client.place_order(
            symbol, side, "MARKET", qty, position_side=position_side,
            reduce_only=False, amount_in_base=True,
        )
        return FillResult(True, qty=float(qty), price=float(order.price or price),
                          order_type=order.order_type, order_id=order.order_id)

    def execute_close(self, symbol, position_side, qty, price, entry_price, entry_cost, funding_paid=0.0):
        side = "BUY" if str(position_side).upper() == "SHORT" else "SELL"
        order = self.client.place_order(
            symbol, side, "MARKET", qty, position_side=position_side,
            reduce_only=True, amount_in_base=True,
        )
        return FillResult(True, qty=float(qty), price=float(order.price or price),
                          order_type=order.order_type, order_id=order.order_id)
