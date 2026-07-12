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
from xauby.strategies.signal import close_short, open_short, sell
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
    def _engine(self, sym="XAUUSDT", state=None, execution_mode="sim"):
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
        engine._pair_registry.update_spec(replace(spec, execution_mode=execution_mode))
        engine._active_tick_symbol = sym
        if state is not None:
            engine.db.trade_state = state
        sc = engine._sc(sym)
        sc.set_tick({
            "last": 100.0, "bid": 99.9, "ask": 100.1, "percent_change_24h": 0.0,
            "timestamp": time.time(), "monotonic_ts": time.monotonic(),
        })
        return engine, sym

    def _run_tick(
        self,
        engine,
        sym,
        signal,
        *,
        close_to_idle=False,
        close_result=True,
        refreshed_state_after_close=None,
        extra_patches=(),
    ):
        runner = SimpleNamespace(run=lambda ctx: signal)
        strat = SimpleNamespace(name="cdc_action_zone", version="1.0.0")
        patches = [
            patch.object(engine, "sync_candles"),
            patch.object(engine, "load_candles_df", return_value=_candles_df()),
            patch.object(engine, "_get_runner_for_symbol", return_value=runner),
            patch.object(engine, "_get_strategy_for_symbol", return_value=strat),
            patch.object(engine, "execute_buy"),
            patch.object(engine, "execute_open_short"),
            patch.object(engine, "execute_sell"),
            patch.object(engine, "execute_close_short"),
            *extra_patches,
        ]
        entered = [p.start() for p in patches]
        try:
            m_buy, m_open_short, m_sell, m_close_short = entered[4:8]
            if close_to_idle:
                def _mark_idle(*_args, **_kwargs):
                    engine.db.trade_state = refreshed_state_after_close or {
                        "state": "idle", "entry_price": 0.0, "stop_loss": 0.0,
                        "take_profit": 0.0, "highest_price_seen": 0.0, "quantity": 0.0,
                    }
                    return True
                m_sell.side_effect = _mark_idle
                m_close_short.side_effect = _mark_idle
            else:
                m_sell.return_value = close_result
                m_close_short.return_value = close_result
            engine._tick_body()
        finally:
            for p in reversed(patches):
                p.stop()
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

    def test_long_close_then_reverse_opens_short_after_successful_close(self):
        long_state = {
            "state": "bought", "position_side": "LONG", "entry_price": 100.0,
            "stop_loss": 0.0, "take_profit": 0.0, "highest_price_seen": 100.0,
            "quantity": 1.0, "opened_at": "2026-06-21T00:00:00",
        }
        engine, sym = self._engine(state=long_state)
        signal = sell(
            "red close",
            volatility=1.0,
            metadata={
                "reverse_to_position_side": "SHORT",
                "reverse_reason": "Fresh short entry",
            },
        )
        calls = self._run_tick(engine, sym, signal, close_to_idle=True)
        calls.sell.assert_called_once()
        calls.open_short.assert_called_once()
        calls.buy.assert_not_called()

    def test_failed_close_does_not_reverse_open(self):
        long_state = {
            "state": "bought", "position_side": "LONG", "entry_price": 100.0,
            "stop_loss": 0.0, "take_profit": 0.0, "highest_price_seen": 100.0,
            "quantity": 1.0, "opened_at": "2026-06-21T00:00:00",
        }
        engine, sym = self._engine(state=long_state)
        signal = sell(
            "red close",
            volatility=1.0,
            metadata={"reverse_to_position_side": "SHORT"},
        )
        calls = self._run_tick(engine, sym, signal, close_result=False)
        calls.sell.assert_called_once()
        calls.open_short.assert_not_called()

    def test_reverse_open_short_bypasses_loss_cooldown_after_close(self):
        long_state = {
            "state": "bought", "position_side": "LONG", "entry_price": 100.0,
            "stop_loss": 0.0, "take_profit": 0.0, "highest_price_seen": 100.0,
            "quantity": 1.0, "opened_at": "2026-06-21T00:00:00",
        }
        engine, sym = self._engine(state=long_state)
        signal = sell(
            "red close",
            volatility=1.0,
            metadata={"reverse_to_position_side": "SHORT"},
        )
        calls = self._run_tick(
            engine,
            sym,
            signal,
            close_to_idle=True,
            extra_patches=[
                patch.object(
                    engine,
                    "_is_buy_blocked_by_cooldown",
                    return_value=(True, "cooldown active"),
                )
            ],
        )
        calls.sell.assert_called_once()
        calls.open_short.assert_called_once()

    def test_reverse_open_short_is_blocked_by_local_residual_after_close(self):
        long_state = {
            "state": "bought", "position_side": "LONG", "entry_price": 100.0,
            "stop_loss": 0.0, "take_profit": 0.0, "highest_price_seen": 100.0,
            "quantity": 1.0, "opened_at": "2026-06-21T00:00:00",
        }
        engine, sym = self._engine(state=long_state)
        signal = sell(
            "red close",
            volatility=1.0,
            metadata={"reverse_to_position_side": "SHORT"},
        )
        calls = self._run_tick(
            engine,
            sym,
            signal,
            close_to_idle=True,
            refreshed_state_after_close={
                "state": "idle",
                "entry_price": 0.0,
                "stop_loss": 0.0,
                "take_profit": 0.0,
                "highest_price_seen": 0.0,
                "quantity": 0.000001,
            },
        )
        calls.sell.assert_called_once()
        calls.open_short.assert_not_called()

    def test_reverse_open_short_is_blocked_by_live_exchange_position_after_close(self):
        long_state = {
            "state": "bought", "position_side": "LONG", "entry_price": 100.0,
            "stop_loss": 0.0, "take_profit": 0.0, "highest_price_seen": 100.0,
            "quantity": 1.0, "opened_at": "2026-06-21T00:00:00",
        }
        engine, sym = self._engine(state=long_state, execution_mode="live")
        signal = sell(
            "red close",
            volatility=1.0,
            metadata={"reverse_to_position_side": "SHORT"},
        )
        calls = self._run_tick(
            engine,
            sym,
            signal,
            close_to_idle=True,
            extra_patches=[
                patch.object(
                    engine.client,
                    "get_positions",
                    return_value=[{"symbol": sym, "position_side": "LONG", "quantity": 0.1}],
                )
            ],
        )
        calls.sell.assert_called_once()
        calls.open_short.assert_not_called()

    def test_reverse_open_short_is_blocked_by_telegram_pause_after_close(self):
        long_state = {
            "state": "bought", "position_side": "LONG", "entry_price": 100.0,
            "stop_loss": 0.0, "take_profit": 0.0, "highest_price_seen": 100.0,
            "quantity": 1.0, "opened_at": "2026-06-21T00:00:00",
        }
        engine, sym = self._engine(state=long_state)
        signal = sell(
            "red close",
            volatility=1.0,
            metadata={"reverse_to_position_side": "SHORT"},
        )
        calls = self._run_tick(
            engine,
            sym,
            signal,
            close_to_idle=True,
            extra_patches=[
                patch(
                    "xauby.runtime.telegram_control.trading_pause_reason",
                    return_value=(True, "manual"),
                )
            ],
        )
        calls.sell.assert_called_once()
        calls.open_short.assert_not_called()

    def test_short_close_then_reverse_opens_long_after_successful_close(self):
        short_state = {
            "state": "bought", "position_side": "SHORT", "entry_price": 100.0,
            "stop_loss": 0.0, "take_profit": 0.0, "highest_price_seen": 100.0,
            "quantity": 1.0, "opened_at": "2026-06-21T00:00:00",
        }
        engine, sym = self._engine(state=short_state)
        signal = close_short(
            "green cover",
            volatility=1.0,
            metadata={
                "reverse_to_position_side": "LONG",
                "reverse_reason": "Fresh long entry",
            },
        )
        calls = self._run_tick(engine, sym, signal, close_to_idle=True)
        calls.close_short.assert_called_once()
        calls.buy.assert_called_once()
        calls.open_short.assert_not_called()


if __name__ == "__main__":
    unittest.main()
