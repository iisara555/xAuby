"""Paper trading broker — no exchange interaction."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict
from typing import Any, Callable, Dict, Optional, Tuple

from xauby.engine.brokers.base import FillResult, SimPositionLedger
from xauby.utils.atomic_io import atomic_json_write

logger = logging.getLogger("lite_bot.sim_broker")


class SimBroker:
    mode = "sim"

    def __init__(
        self,
        balance_file: str,
        initial_balance: float = 42.0,
        default_fee_pct: float = 0.001,
        fee_resolver: Optional[Callable[[str], float]] = None,
        funding_rate_8h: float = 0.0001,
    ):
        self.balance_file = balance_file
        self.initial_balance = float(initial_balance)
        self.default_fee_pct = float(default_fee_pct)
        self._fee_resolver = fee_resolver
        self.funding_rate_8h = float(funding_rate_8h)
        self._lock = threading.Lock()
        self._ledgers: Dict[str, SimPositionLedger] = {}
        self._load_ledgers()

    def _fee_pct(self, symbol: str) -> float:
        if self._fee_resolver:
            try:
                return float(self._fee_resolver(symbol))
            except Exception:
                pass
        return self.default_fee_pct

    def _read_state_unlocked(self) -> Dict[str, Any]:
        if not os.path.exists(self.balance_file):
            return {"USDT": self.initial_balance, "ledgers": {}}
        try:
            with open(self.balance_file, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            if not isinstance(data, dict):
                data = {}
            data.setdefault("USDT", self.initial_balance)
            data.setdefault("ledgers", {})
            return data
        except Exception:
            return {"USDT": self.initial_balance, "ledgers": {}}

    def _write_state_unlocked(self, data: Dict[str, Any]) -> None:
        atomic_json_write(self.balance_file, data, indent=2)

    def _load_ledgers(self) -> None:
        with self._lock:
            raw = self._read_state_unlocked().get("ledgers") or {}
            if not isinstance(raw, dict):
                return
            for sym, item in raw.items():
                if not isinstance(item, dict):
                    continue
                try:
                    self._ledgers[str(sym).upper().replace("_", "")] = SimPositionLedger(
                        entry_price=float(item.get("entry_price", 0.0)),
                        quantity=float(item.get("quantity", 0.0)),
                        unrealized_pnl=float(item.get("unrealized_pnl", 0.0)),
                        realized_pnl=float(item.get("realized_pnl", 0.0)),
                        position_side=str(item.get("position_side") or "LONG").upper(),
                        margin_reserved=float(item.get("margin_reserved", 0.0)),
                        leverage=float(item.get("leverage", 1.0) or 1.0),
                        funding_paid=float(item.get("funding_paid", 0.0)),
                        last_funding_ts=float(item.get("last_funding_ts", 0.0)),
                    )
                except (TypeError, ValueError):
                    continue

    def _persist_ledgers_unlocked(self) -> None:
        data = self._read_state_unlocked()
        data["ledgers"] = {sym: asdict(ledger) for sym, ledger in self._ledgers.items()}
        self._write_state_unlocked(data)

    def _read_balance_unlocked(self) -> float:
        data = self._read_state_unlocked()
        try:
            return float(data.get("USDT", self.initial_balance))
        except (TypeError, ValueError):
            return self.initial_balance

    def get_usdt_balance(self) -> float:
        with self._lock:
            return self._read_balance_unlocked()

    def save_usdt_balance(self, amount: float) -> None:
        with self._lock:
            data = self._read_state_unlocked()
            data["USDT"] = float(amount)
            self._write_state_unlocked(data)

    def debit_usdt(self, amount: float) -> bool:
        with self._lock:
            bal = self._read_balance_unlocked()
            if bal < amount:
                return False
            data = self._read_state_unlocked()
            data["USDT"] = float(bal - amount)
            self._write_state_unlocked(data)
            return True

    def credit_usdt(self, amount: float) -> None:
        with self._lock:
            bal = self._read_balance_unlocked()
            data = self._read_state_unlocked()
            data["USDT"] = float(bal + amount)
            self._write_state_unlocked(data)

    def get_ledger(self, symbol: str) -> SimPositionLedger:
        sym = symbol.upper().replace("_", "")
        return self._ledgers.get(sym, SimPositionLedger())

    def update_unrealized(self, symbol: str, mark_price: float) -> None:
        sym = symbol.upper().replace("_", "")
        with self._lock:
            ledger = self._ledgers.get(sym)
            if not ledger or ledger.quantity <= 0:
                return
            direction = -1.0 if ledger.position_side == "SHORT" else 1.0
            ledger.unrealized_pnl = direction * (mark_price - ledger.entry_price) * ledger.quantity
            if ledger.position_side == "SHORT" and ledger.last_funding_ts > 0:
                periods = int((time.time() - ledger.last_funding_ts) // (8 * 3600))
                if periods > 0:
                    ledger.funding_paid += (
                        ledger.entry_price * ledger.quantity * self.funding_rate_8h * periods
                    )
                    ledger.last_funding_ts += periods * 8 * 3600
            self._persist_ledgers_unlocked()

    def execute_buy(
        self,
        symbol: str,
        qty: float,
        price: float,
        notional: float,
    ) -> FillResult:
        sym = symbol.upper().replace("_", "")
        fee_pct = self._fee_pct(sym)
        fees = notional * fee_pct
        total = notional + fees
        # Debit + ledger update in one critical section / one file write so a
        # crash in between cannot leave money deducted without a position.
        with self._lock:
            bal = self._read_balance_unlocked()
            if bal < total:
                return FillResult(success=False, error=f"Insufficient sim balance ({total:.2f} USDT)")
            existing = self._ledgers.get(sym)
            if existing and existing.quantity > 0:
                logger.warning(
                    "[%s] execute_buy called while position already open (qty=%.6f). Aborting.",
                    sym, existing.quantity,
                )
                return FillResult(success=False, error=f"Position already open for {sym}")
            self._ledgers[sym] = SimPositionLedger(entry_price=price, quantity=qty)
            data = self._read_state_unlocked()
            data["USDT"] = float(bal - total)
            data["ledgers"] = {s: asdict(ledger) for s, ledger in self._ledgers.items()}
            self._write_state_unlocked(data)
        return FillResult(
            success=True,
            qty=qty,
            price=price,
            fees=fees,
            order_type="PAPER",
            order_id=f"sim-buy-{sym}",
        )

    def execute_sell(
        self,
        symbol: str,
        qty: float,
        price: float,
        entry_price: float,
        entry_cost: float,
    ) -> FillResult:
        sym = symbol.upper().replace("_", "")
        gross = qty * price
        fee_pct = self._fee_pct(sym)
        fees = gross * fee_pct
        net_exit = gross - fees
        net_pnl = net_exit - entry_cost
        with self._lock:
            ledger = self._ledgers.get(sym, SimPositionLedger())
            ledger.realized_pnl += net_pnl
            ledger.quantity = 0.0
            ledger.unrealized_pnl = 0.0
            self._ledgers[sym] = ledger
            data = self._read_state_unlocked()
            data["USDT"] = float(self._read_balance_unlocked() + net_exit)
            data["ledgers"] = {s: asdict(l) for s, l in self._ledgers.items()}
            self._write_state_unlocked(data)
        return FillResult(
            success=True,
            qty=qty,
            price=price,
            fees=fees,
            order_type="PAPER",
            order_id=f"sim-sell-{sym}",
        )

    def place_stop_loss(
        self,
        symbol: str,
        qty: float,
        stop_price: float,
    ) -> Optional[Tuple[str, float]]:
        return None

    def execute_open(
        self,
        symbol: str,
        position_side: str,
        qty: float,
        price: float,
        notional: float,
        leverage: float = 1.0,
    ) -> FillResult:
        side = str(position_side).upper()
        if side == "LONG" and float(leverage) == 1.0:
            return self.execute_buy(symbol, qty, price, notional)
        if side not in ("LONG", "SHORT"):
            return FillResult(success=False, error=f"Invalid position side {position_side!r}")
        lev = max(1.0, float(leverage))
        margin = float(notional) / lev
        fees = float(notional) * self._fee_pct(symbol)
        sym = symbol.upper().replace("_", "")
        with self._lock:
            bal = self._read_balance_unlocked()
            if bal < margin + fees:
                return FillResult(success=False, error=f"Insufficient sim margin ({margin + fees:.2f})")
            existing = self._ledgers.get(sym)
            if existing and existing.quantity > 0:
                return FillResult(success=False, error=f"Position already open for {sym}")
            self._ledgers[sym] = SimPositionLedger(
                entry_price=price, quantity=qty, position_side=side,
                margin_reserved=margin, leverage=lev,
                last_funding_ts=time.time(),
            )
            data = self._read_state_unlocked()
            data["USDT"] = float(bal - margin - fees)
            data["ledgers"] = {s: asdict(v) for s, v in self._ledgers.items()}
            self._write_state_unlocked(data)
        return FillResult(True, qty=qty, price=price, fees=fees,
                          order_type="PAPER", order_id=f"sim-open-{side.lower()}-{sym}")

    def execute_close(
        self,
        symbol: str,
        position_side: str,
        qty: float,
        price: float,
        entry_price: float,
        entry_cost: float,
        funding_paid: float = 0.0,
    ) -> FillResult:
        side = str(position_side).upper()
        if side == "LONG":
            return self.execute_sell(symbol, qty, price, entry_price, entry_cost)
        sym = symbol.upper().replace("_", "")
        with self._lock:
            ledger = self._ledgers.get(sym, SimPositionLedger())
            if ledger.quantity <= 0 or ledger.position_side != "SHORT":
                return FillResult(False, error=f"No SHORT position for {sym}")
            notional_exit = qty * price
            fees = notional_exit * self._fee_pct(sym)
            funding_total = float(funding_paid or ledger.funding_paid)
            pnl = (entry_price - price) * qty - fees - funding_total
            credit = ledger.margin_reserved + pnl
            ledger.realized_pnl += pnl
            ledger.quantity = 0.0
            ledger.unrealized_pnl = 0.0
            ledger.funding_paid = funding_total
            self._ledgers[sym] = ledger
            data = self._read_state_unlocked()
            data["USDT"] = float(self._read_balance_unlocked() + credit)
            data["ledgers"] = {s: asdict(v) for s, v in self._ledgers.items()}
            self._write_state_unlocked(data)
        return FillResult(True, qty=qty, price=price, fees=fees,
                          order_type="PAPER", order_id=f"sim-close-short-{sym}")
