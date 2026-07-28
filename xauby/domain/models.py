from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict, Any, Optional


class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TradeIntent(str, Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    HOLD = "HOLD"


@dataclass(frozen=True)
class MarketIdentity:
    exchange: str
    market_type: str
    settle_asset: str
    canonical_symbol: str
    exchange_symbol: str

@dataclass(frozen=True)
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __getitem__(self, key):
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self):
        return ("timestamp", "open", "high", "low", "close", "volume")

    def __contains__(self, key):
        return hasattr(self, key)


@dataclass(frozen=True)
class Order:
    order_id: str
    client_id: str
    symbol: str
    side: str
    order_type: str
    price: float
    amount: float
    status: str
    raw_payload: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __getitem__(self, key):
        if self.raw_payload and key in self.raw_payload:
            return self.raw_payload[key]

        legacy_map = {
            "orderId": self.order_id,
            "id": self.order_id,
            "clientId": self.client_id,
            "executedQty": self.amount,
            "filled": self.amount,
        }
        if key in legacy_map:
            return legacy_map[key]
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self):
        base_keys = {"order_id", "client_id", "symbol", "side", "order_type", "price", "amount", "status", "orderId", "id", "clientId", "executedQty", "filled"}
        if self.raw_payload:
            base_keys.update(self.raw_payload.keys())
        return list(base_keys)

    def __contains__(self, key):
        if self.raw_payload and key in self.raw_payload:
            return True
        legacy_keys = {"orderId", "id", "clientId", "executedQty", "filled"}
        return key in legacy_keys or hasattr(self, key)


@dataclass(frozen=True)
class Position:
    symbol: str
    state: str
    entry_price: float
    stop_loss: float
    take_profit: float
    highest_price_seen: float
    quantity: float
    # Lowest observed ticker while this position is open.  Together with
    # highest_price_seen this makes MAE/MFE measurable for both LONG and SHORT.
    lowest_price_seen: float = 0.0
    # None means a newly-created state whose DB layer may start complete
    # tracking. False is retained for positions adopted/migrated mid-trade,
    # whose pre-upgrade excursion history is unknowable.
    excursion_tracking_complete: Optional[bool] = None
    opened_at: Optional[str] = None
    last_transition_at: Optional[str] = None
    stop_loss_order_id: Optional[str] = None
    position_side: str = PositionSide.LONG.value
    leverage: float = 1.0
    margin_mode: str = "spot"
    liquidation_price: float = 0.0
    funding_paid: float = 0.0
    management_mode: str = "strategy"
    exchange_position_id: Optional[str] = None
    # One-shot partial take-profit already banked for this position (persisted
    # so an engine restart cannot re-fire the partial exit).
    partial_tp_taken: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def __getitem__(self, key):
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self):
        return (
            "symbol", "state", "entry_price", "stop_loss", "take_profit",
            "highest_price_seen", "quantity", "lowest_price_seen",
            "excursion_tracking_complete", "opened_at", "last_transition_at",
            "stop_loss_order_id", "position_side", "leverage", "margin_mode",
            "liquidation_price", "funding_paid", "management_mode",
            "exchange_position_id", "partial_tp_taken",
        )

    def __contains__(self, key):
        return hasattr(self, key)


@dataclass(frozen=True)
class Portfolio:
    balances: Dict[str, float]
    raw_payload: Optional[Dict[str, Any]] = None

    def get_balance(self, asset: str) -> float:
        return self.balances.get(asset.upper(), 0.0)

    def __getitem__(self, key):
        if self.raw_payload and key in self.raw_payload:
            return self.raw_payload[key]
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self):
        base_keys = {"balances"}
        if self.raw_payload:
            base_keys.update(self.raw_payload.keys())
        return list(base_keys)

    def __contains__(self, key):
        if self.raw_payload and key in self.raw_payload:
            return True
        return hasattr(self, key)
