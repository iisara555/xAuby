from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional

from xauby.domain.models import Candle, Order, Portfolio
from xauby.api.utils import (
    DEFAULT_SYMBOL_FILTERS,
    find_symbol_entry,
    make_client_id,
    parse_exchange_filters,
)


class IExchangeGateway(ABC):
    """Abstract gateway interface for REST/WS exchange operations.

    Two method families exist:

    * ``fetch_*`` — the typed, exchange-neutral surface (returns domain models).
    * ``get_*`` — the raw-dict surface the live engine, reconciler, and order
      mixins depend on. A gateway that the engine drives in live/sim must
      provide, with these exact signatures and the normalized Binance-style
      shapes (both bundled adapters do):

        - ``get_ticker(symbol) -> dict``
        - ``get_candles(symbol, timeframe, limit=250) -> list[list]``
        - ``get_exchange_info(symbol=None) -> {"symbols": [...]}``
        - ``get_balances() -> {asset: {"available", "reserved"}}``
        - ``get_open_orders(symbol=None) -> list[dict]``
        - ``get_order(symbol, order_id) -> dict``
        - ``get_positions(symbols=None) -> list[dict]`` for derivatives
        - ``get_position_history(symbol, since=None, limit=100) -> list[dict]``
          when the derivatives venue exposes exchange-authoritative close data
        - ``get_recent_trades`` / ``get_order_book`` for full market-data parity
        - ``set_leverage`` / ``set_margin_mode`` for derivatives
        - ``check_clock_sync() -> bool`` (optional; feature-detected)
        - ``close() -> None`` (optional)

    They are intentionally not declared as abstract methods so lightweight test
    doubles that only need the ``fetch_*`` surface, plus the engine's
    ``hasattr``-based feature detection, keep working. ``get_symbol_filters``
    and ``make_client_id`` are provided concretely below because they are pure
    derivations any adapter can share.
    """

    # --- typed neutral surface -------------------------------------------------
    @abstractmethod
    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """Fetch current market ticker."""

    @abstractmethod
    def fetch_candles(self, symbol: str, timeframe: str, limit: int = 500) -> List[Candle]:
        """Fetch historical candle charts."""

    @abstractmethod
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
        """Place a limit, market, or stop-loss order."""

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> None:
        """Cancel an open order."""

    @abstractmethod
    def fetch_balances(self) -> Portfolio:
        """Fetch current account balances."""

    # --- shared concrete derivations -------------------------------------------
    @staticmethod
    def make_client_id(prefix: str = "xb") -> str:
        return make_client_id(prefix)

    def get_symbol_filters(self, symbol: str) -> Dict[str, Any]:
        """Resolve lot/price/notional filters for ``symbol``.

        Default implementation parses the gateway's own (normalized)
        ``get_exchange_info`` output, so any adapter that returns the standard
        exchangeInfo shape gets working filters for free.
        """
        try:
            data = self.get_exchange_info(symbol)
            entry = find_symbol_entry(data.get("symbols", []), symbol)
            if entry:
                return parse_exchange_filters(entry)
        except Exception:
            pass
        return dict(DEFAULT_SYMBOL_FILTERS)
