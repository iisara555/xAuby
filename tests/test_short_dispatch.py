"""Engine dispatch routing for SHORT signals.

open_short() maps intent OPEN/SHORT onto action "BUY"; the tick dispatcher must
route it to execute_open_short, NOT the LONG execute_buy path. close_short()
maps to action "SELL" and must cover an open short.
"""
from __future__ import annotations

import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from xauby.engine.trading import LiteTradingEngine
from xauby.strategies.signal import close_short, open_short
from tests.mocks import MockExchangeGateway, MockDatabaseRepository, MockNotificationService


def _candles_df(n: int = 150, price: float = 100.0) -> pd.DataFrame:
    ts = list(range(1_700_000_000, 1_700_000_000 + n * 14400, 14400))
    return pd.DataFrame(
        {
            "timestamp": ts,
            "open": [price] * n,
            "high": [price] * n,
            "low": [price] * n,
            "close": [price] * n,
            "volume": [1.0] * n,
        }
    )


class TestShortDispatch(unittest.TestCase):
    def _engine(self, sym="BTCUSDT", state=None):
        engine = LiteTradingEngine(
            config_path="bot_config.yaml",
            client=MockExchangeGateway(),
            db=MockDatabaseRepository(),
            notification_service=MockNotificationService(),
        )
        engine.simulate_only = False
        # Run the pair in sim execution mode so the dispatch is exercised without
        # live client calls (set_leverage/margin) on the mock.
        spec = engine._pair_registry.get(sym)
        engine._pair_registry.update_spec(replace(spec, execution_mode="sim"))
        engine._active_tick_symbol = sym
        if state is not None:
            engine.db.trade_state = state
        sc = engine._sc(sym)
        sc.set_tick({
            "last": 100.0, "bid": 99.9, "ask": 100.1, "percent_change_24h": 0.0,
            "timestamp": time.time(), "monotonic_ts": time.monotonic(),
        })
        return engine, sym

    def _run_tick(self, engine, sym, signal):
        runner = SimpleNamespace(run=lambda ctx: signal)
        strat = SimpleNamespace(name="cdc_action_zone", version="1.0.0")
        with patch.object(engine, "sync_candles"), \
                patch.object(engine, "load_candles_df", return_value=_candles_df()), \
                patch.object(engine, "_get_runner_for_symbol", return_value=runner), \
                patch.object(engine, "_get_strategy_for_symbol", return_value=strat), \
                patch.object(engine, "execute_buy") as m_buy, \
                patch.object(engine, "execute_open_short") as m_open_short, \
                patch.object(engine, "execute_sell") as m_sell, \
                patch.object(engine, "execute_close_short") as m_close_short:
            engine._tick_body()
        return SimpleNamespace(buy=m_buy, open_short=m_open_short, sell=m_sell, close_short=m_close_short)

    def test_open_short_routes_to_execute_open_short(self):
        engine, sym = self._engine()
        calls = self._run_tick(engine, sym, open_short("fresh red", volatility=1.0))
        calls.open_short.assert_called_once()
        calls.buy.assert_not_called()
        calls.sell.assert_not_called()

    def test_close_short_covers_open_short_position(self):
        short_state = {
            "state": "bought", "position_side": "SHORT", "entry_price": 100.0,
            "stop_loss": 0.0, "take_profit": 0.0, "highest_price_seen": 100.0,
            "quantity": 1.0, "opened_at": "2026-06-21T00:00:00",
        }
        engine, sym = self._engine(state=short_state)
        calls = self._run_tick(engine, sym, close_short("green cover", volatility=1.0))
        calls.close_short.assert_called_once()
        calls.sell.assert_not_called()
        calls.buy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
