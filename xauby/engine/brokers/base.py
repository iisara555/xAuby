"""Execution broker abstractions for sim and live order paths."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Tuple


@dataclass
class FillResult:
    success: bool
    qty: float = 0.0
    price: float = 0.0
    fees: float = 0.0
    order_type: str = "PAPER"
    order_id: str = ""
    error: str = ""


@dataclass
class SimPositionLedger:
    entry_price: float = 0.0
    quantity: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    position_side: str = "LONG"
    margin_reserved: float = 0.0
    leverage: float = 1.0
    funding_paid: float = 0.0
    last_funding_ts: float = 0.0


class ExecutionBroker(Protocol):
    mode: str

    def execute_buy(
        self,
        symbol: str,
        qty: float,
        price: float,
        notional: float,
    ) -> FillResult: ...

    def execute_sell(
        self,
        symbol: str,
        qty: float,
        price: float,
        entry_price: float,
        entry_cost: float,
    ) -> FillResult: ...

    def get_usdt_balance(self) -> float: ...

    def debit_usdt(self, amount: float) -> bool: ...

    def credit_usdt(self, amount: float) -> None: ...

    def get_ledger(self, symbol: str) -> SimPositionLedger: ...

    def place_stop_loss(
        self,
        symbol: str,
        qty: float,
        stop_price: float,
    ) -> Optional[Tuple[str, float]]: ...

    def execute_open(
        self, symbol: str, position_side: str, qty: float, price: float,
        notional: float, leverage: float = 1.0,
    ) -> FillResult: ...

    def execute_close(
        self, symbol: str, position_side: str, qty: float, price: float,
        entry_price: float, entry_cost: float, funding_paid: float = 0.0,
    ) -> FillResult: ...
