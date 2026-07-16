"""Shared mocks for xAuby test suite.

These were extracted from test_trading_engine.py so sibling test modules
can import them without relying on pytest's non-package sibling-import
behaviour.
"""

from typing import Dict, Any, List, Optional

from xauby.domain.models import Candle, Order, Position, Portfolio
from xauby.api.interface import IExchangeGateway
from xauby.storage.interface import IDatabaseRepository
from xauby.notifications.interface import INotificationService, AlertLevel
from xauby.runtime.pair_registry import PairSpec
from xauby.engine.symbol_context import SymbolContext


class MockExchangeGateway(IExchangeGateway):
    def __init__(self):
        self.ticker_called = False
        self.candles_called = False
        self.order_called = False
        self.balances_called = False
        self.cancel_called = False
        # Reflect the configured OKX USDT-perpetual adapter so the engine's
        # derivatives/live-SHORT validation passes in tests.
        self.capabilities = {
            "swap": True,
            "positions": True,
            "reduce_only": True,
            "supports_stop_loss_limit": False,
        }

    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        self.ticker_called = True
        return {
            "symbol": symbol,
            "last": 2000.0,
            "bid": 1999.0,
            "ask": 2001.0,
            "volume": 5000.0,
            "high": 2010.0,
            "low": 1990.0,
            "percent_change_24h": 0.5,
        }

    def fetch_candles(self, symbol: str, timeframe: str, limit: int = 500) -> List[Candle]:
        self.candles_called = True
        return [
            Candle(1700000000 + i * 60, 2000.0, 2010.0, 1990.0, 2005.0, 10.0)
            for i in range(10)
        ]

    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        client_id: Optional[str] = None,
        **kwargs
    ) -> Order:
        self.order_called = True
        return Order(
            order_id="mock-order-id",
            client_id=client_id or "mock-client-id",
            symbol=symbol,
            side=side,
            order_type=order_type,
            price=price or 2000.0,
            amount=amount,
            status="FILLED",
        )

    def cancel_order(self, symbol: str, order_id: str) -> None:
        self.cancel_called = True

    def fetch_balances(self) -> Portfolio:
        self.balances_called = True
        return Portfolio(balances={"USDT": 1000.0, "XAUT": 2.0})

    def get_positions(self, symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        return []


def install_test_pair(
    engine: Any,
    symbol: str,
    *,
    strategy_name: str = "xauby_actionzone",
    execution_mode: str = "sim",
    allowed_sides: tuple[str, ...] = ("long", "short"),
    short_live_enabled: bool = True,
) -> None:
    sym = symbol.upper().replace("_", "")
    spec = PairSpec(
        symbol=sym,
        strategy_name=strategy_name,
        execution_mode=execution_mode,
        allowed_sides=allowed_sides,
        short_live_enabled=short_live_enabled,
    )
    engine._pair_registry.update_spec(spec)
    engine._strategy_names_by_symbol[sym] = strategy_name
    engine.contexts[sym] = SymbolContext.from_spec(spec)


class MockDatabaseRepository(IDatabaseRepository):
    def __init__(self):
        self.saved_candles = []
        self.trade_state = None
        self.closed_trades = []
        self.get_state_called = False
        self.save_state_called = False

    def save_candles(self, symbol: str, timeframe: str, candles: List[Candle]) -> None:
        self.saved_candles.extend(candles)

    def get_candles(self, symbol: str, timeframe: str, limit: int = 250) -> List[Candle]:
        return [
            Candle(1700000000 + i * 60, 2000.0, 2010.0, 1990.0, 2005.0, 10.0)
            for i in range(10)
        ]

    def delete_old_candles(self, symbol: str, timeframe: str, before_timestamp: int) -> None:
        pass

    def get_trade_state(self, symbol: str) -> Position:
        self.get_state_called = True
        if self.trade_state:
            return self.trade_state
        return Position(
            symbol=symbol,
            state="idle",
            entry_price=0.0,
            stop_loss=0.0,
            take_profit=0.0,
            highest_price_seen=0.0,
            quantity=0.0,
        )

    def save_trade_state(
        self,
        symbol_or_position: Any = None,
        state: Optional[str] = None,
        entry_price: float = 0.0,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        highest_price_seen: float = 0.0,
        quantity: float = 0.0,
        opened_at: Optional[str] = None,
        last_transition_at: Optional[str] = None,
        stop_loss_order_id: Optional[str] = None,
        position_side: str = "LONG",
        leverage: float = 1.0,
        margin_mode: str = "spot",
        liquidation_price: float = 0.0,
        funding_paid: float = 0.0,
        management_mode: str = "strategy",
        exchange_position_id: Optional[str] = None,
        partial_tp_taken: bool = False,
        *,
        symbol: Optional[str] = None,
    ) -> None:
        self.save_state_called = True
        if isinstance(symbol_or_position, Position):
            self.trade_state = symbol_or_position
            return
        if symbol_or_position is not None:
            symbol = str(symbol_or_position)
        if symbol is None:
            raise ValueError("save_trade_state requires a symbol or Position")
        self.trade_state = Position(
            symbol=symbol,
            state=state or "idle",
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            highest_price_seen=highest_price_seen,
            quantity=quantity,
            opened_at=opened_at,
            last_transition_at=last_transition_at,
            stop_loss_order_id=stop_loss_order_id,
            position_side=str(position_side or "LONG").upper(),
            leverage=leverage,
            margin_mode=margin_mode,
            liquidation_price=liquidation_price,
            funding_paid=funding_paid,
            management_mode=management_mode,
            exchange_position_id=exchange_position_id,
            partial_tp_taken=partial_tp_taken,
        )

    def save_closed_trade(self, trade: Dict[str, Any] = None, **kwargs) -> None:
        self.closed_trades.append(dict(trade or kwargs))

    def get_closed_trades(self, symbol: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        return self.closed_trades[:limit]

    def get_latest_gold_regime(self, symbol: str = "XAUTUSDT") -> Optional[Dict[str, Any]]:
        return {
            "symbol": symbol,
            "timestamp": 1700000000,
            "regime": "NORMAL",
            "trend": "UP",
            "volatility": "LOW",
            "macro_bias": "BULLISH",
            "confidence": 0.9,
            "details": {},
        }

    def get_regime_at(self, ts_iso: str, symbol: str = "XAUTUSDT") -> str:
        return "NORMAL"

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
        return []

    def list_event_runs(
        self,
        limit: int = 20,
        symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return []

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
        pass

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
        pass

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
    ) -> bool:
        self.closed_trades.append({
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "entry_cost": entry_cost,
            "gross_exit": gross_exit,
            "entry_fee": entry_fee,
            "exit_fee": exit_fee,
            "total_fees": total_fees,
            "net_pnl": net_pnl,
            "net_pnl_pct": net_pnl_pct,
            "trigger": trigger,
            "opened_at": opened_at,
            "closed_at": closed_at,
            "entry_regime": entry_regime,
            "exit_regime": exit_regime,
            "strategy_name": strategy_name,
            "execution_mode": execution_mode,
            "exchange_close_id": exchange_close_id,
            "exchange_position_id": exchange_position_id,
            "pnl_source": pnl_source,
            "pnl_confirmed": pnl_confirmed,
            "funding_fee": funding_fee,
        })
        self.trade_state = Position(
            symbol=symbol,
            state="idle",
            entry_price=0.0,
            stop_loss=0.0,
            take_profit=0.0,
            highest_price_seen=0.0,
            quantity=0.0,
        )
        return True


class MockNotificationService(INotificationService):
    def __init__(self):
        self.alerts = []

    def send_alert(self, message: str, level=AlertLevel.TRADE, reply_markup=None) -> None:
        self.alerts.append(message)
