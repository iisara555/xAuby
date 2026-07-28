from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from xauby.domain.models import Candle, Position

class IDatabaseRepository(ABC):
    """Abstract interface for database operations (caching, positions, trades)."""

    @abstractmethod
    def save_candles(self, symbol: str, timeframe: str, candles: List[Candle]) -> None:
        """Cache list of Candle objects to database."""

    @abstractmethod
    def get_candles(self, symbol: str, timeframe: str, limit: int = 250) -> List[Candle]:
        """Fetch list of cached Candle objects."""

    @abstractmethod
    def delete_old_candles(self, symbol: str, timeframe: str, before_timestamp: int) -> None:
        """Prune old database records."""

    @abstractmethod
    def get_trade_state(self, symbol: str) -> Position:
        """Fetch active trading position state."""

    @abstractmethod
    def save_trade_state(self, position: Position) -> None:
        """Persist current position state."""

    def update_position_extrema(self, symbol: str, price: float) -> bool:
        """Persist a new high/low for an open position, if either changed."""
        return False

    @abstractmethod
    def save_closed_trade(self, trade: Dict[str, Any]) -> None:
        """Save a finalized trade record."""

    @abstractmethod
    def get_closed_trades(self, symbol: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch closed trade history."""

    @abstractmethod
    def get_latest_gold_regime(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch the most recent gold regime record for a symbol."""

    @abstractmethod
    def get_regime_at(self, ts_iso: str, symbol: str) -> str:
        """Fetch the regime status at a specific ISO timestamp for a symbol."""

    @abstractmethod
    def query_events(
        self,
        symbol: Optional[str] = None,
        event_type: Optional[str] = None,
        run_id: Optional[str] = None,
        since_ts: Optional[str] = None,
        until_ts: Optional[str] = None,
        limit: int = 100,
        order: str = "desc",
    ) -> List[Dict[str, Any]]:
        """Query logged events based on filters."""

    @abstractmethod
    def list_event_runs(
        self,
        limit: int = 20,
        symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List distinct engine execution runs."""

    @abstractmethod
    def save_gold_regime(
        self,
        symbol: str,
        timestamp: int,
        regime: str,
        trend: str,
        volatility: str,
        macro_bias: str,
        confidence: float,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Save a new gold regime record for a symbol."""

    @abstractmethod
    def insert_event(
        self,
        run_id: str,
        seq: int,
        ts: str,
        event_type: str,
        symbol: str,
        mode: str = "paper",
        payload: Optional[str] = None,
        tick_id: Optional[str] = None,
    ) -> None:
        """Log a new system event."""

    @abstractmethod
    def close_position_atomic(
        self,
        symbol: str,
        *,
        side: str,
        amount: float,
        entry_price: float,
        exit_price: float,
        entry_cost: float,
        gross_exit: float,
        entry_fee: float = 0.0,
        exit_fee: float = 0.0,
        total_fees: float = 0.0,
        net_pnl: float = 0.0,
        net_pnl_pct: float = 0.0,
        trigger: Optional[str] = None,
        opened_at: Optional[str] = None,
        closed_at: Optional[str] = None,
        entry_regime: Optional[str] = None,
        exit_regime: Optional[str] = None,
        strategy_name: Optional[str] = None,
        execution_mode: Optional[str] = None,
        exchange_close_id: Optional[str] = None,
        exchange_position_id: Optional[str] = None,
        pnl_source: str = "engine",
        pnl_confirmed: bool = True,
        funding_fee: float = 0.0,
        mae_pct: Optional[float] = None,
        mfe_pct: Optional[float] = None,
    ) -> bool:
        """Atomically record a closed trade and reset position state to idle."""
