from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone

from xauby.api.ccxt_client import CCXTExchangeClient
from xauby.database.db import LiteDB, SCHEMA_VERSION
from xauby.engine.brokers.sim_broker import SimBroker
from xauby.runtime.config_error import ConfigError
from xauby.runtime.derivatives_config import derivatives_settings
from xauby.runtime.exchange_switch import run_exchange_preflight
from xauby.strategies.signal import close_short, open_short


SWAP_CFG = {
    "exchange": {
        "provider": "ccxt", "ccxt_id": "okx", "quote_asset": "USDT",
        "market_type": "swap", "settle_asset": "USDT",
        "margin_mode": "isolated", "position_mode": "one_way",
    },
    "derivatives": {"default_leverage": 1, "max_leverage": 3},
}


class FakeSwap:
    def __init__(self):
        self.has = {"fetchPositionsHistory": True}
        self.markets = {
            "BTC/USDT:USDT": {
                "id": "BTC-USDT-SWAP", "symbol": "BTC/USDT:USDT",
                "base": "BTC", "quote": "USDT", "settle": "USDT",
                "swap": True, "contract": True, "active": True,
                "contractSize": 0.01, "precision": {"amount": 1, "price": 1},
                "limits": {"amount": {"min": 1}, "cost": {"min": 1}},
            },
            "XAU/USDT:USDT": {
                "id": "XAU-USDT-SWAP", "symbol": "XAU/USDT:USDT",
                "base": "XAU", "quote": "USDT", "settle": "USDT",
                "swap": True, "contract": True, "active": True,
                "contractSize": 0.001, "precision": {"amount": 1, "price": 1},
                "limits": {"amount": {"min": 1}, "cost": {"min": 1}},
            },
        }
        self.markets_by_id = {"BTCUSDT": [self.markets["BTC/USDT:USDT"]]}
        self.created = None
        self.history_args = None

    def load_markets(self): return self.markets
    def fetch_ticker(self, symbol): return {"symbol": symbol, "last": 50000}
    def fetch_ohlcv(self, symbol, timeframe="1m", limit=2): return [[1, 1, 1, 1, 1, 1]]
    def fetch_balance(self): return {"free": {"USDT": 1000}, "used": {}, "total": {"USDT": 1000}}
    def fetch_open_orders(self, symbol=None): return []
    def privateGetTradeOrdersAlgoPending(self, params):
        return {"code": "0", "data": [], "msg": ""}
    def fetch_positions(self, symbols=None): return []
    def fetch_positions_history(self, symbols, since, limit, params):
        self.history_args = (symbols, since, limit, params)
        return [{
            "id": "3708019708102549504",
            "symbol": "XAU/USDT:USDT",
            "side": "short",
            "contracts": 33,
            "entryPrice": 4115.3,
            "lastPrice": 4000.5,
            "realizedPnl": 3.8292371028625467,
            "timestamp": 1783475415777,
            "lastUpdateTimestamp": 1784206521546,
            "info": {
                "posId": "3708019708102549504",
                "instId": "XAU-USDT-SWAP",
                "direction": "short",
                "closeTotalPos": "33",
                "openAvgPx": "4115.3",
                "closeAvgPx": "4000.5",
                "realizedPnl": "3.8292371028625467",
                "pnl": "3.7884",
                "fee": "-0.1339107",
                "fundingFee": "0.1747478028625467",
                "type": "2",
                "cTime": "1783475415777",
                "uTime": "1784206521546",
                "mgnMode": "isolated",
                "lever": "1",
            },
        }]
    def fetch_trades(self, symbol, limit=2): return []
    def fetch_order_book(self, symbol, limit=5): return {"bids": [[1, 1]], "asks": [[2, 1]]}
    def fetch_funding_rate(self, symbol): return {"symbol": symbol, "fundingRate": 0.0001}
    def iso8601(self, timestamp):
        return datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).isoformat()
    def create_order(self, symbol, typ, side, amount, price, params):
        self.created = (symbol, typ, side, amount, params)
        return {"id": "1", "symbol": symbol, "type": typ, "side": side,
                "amount": amount, "filled": amount, "status": "closed"}


class TestDerivativesRuntime(unittest.TestCase):
    def test_semantic_short_signals(self):
        self.assertEqual((open_short("x").intent, open_short("x").position_side), ("OPEN", "SHORT"))
        self.assertEqual((close_short("x").intent, close_short("x").position_side), ("CLOSE", "SHORT"))

    def test_config_caps_leverage(self):
        self.assertEqual(derivatives_settings(SWAP_CFG)["max_leverage"], 3)
        bad = {**SWAP_CFG, "derivatives": {"default_leverage": 1, "max_leverage": 10}}
        # Named rather than bare Exception: a blind assertRaises also passes on
        # an AttributeError or a typo in the call, so it can go green while the
        # cap it is meant to prove has stopped working.
        with self.assertRaises(ConfigError):
            derivatives_settings(bad)

    def test_okx_swap_symbol_contract_and_reduce_only(self):
        exchange = FakeSwap()
        client = CCXTExchangeClient(config=SWAP_CFG, exchange_instance=exchange)
        self.assertEqual(client.market_identity("BTCUSDT").exchange_symbol, "BTC/USDT:USDT")
        client.place_order("BTCUSDT", "BUY", "MARKET", 0.02,
                           reduce_only=True, position_side="SHORT", amount_in_base=True)
        self.assertEqual(exchange.created[0], "BTC/USDT:USDT")
        self.assertEqual(exchange.created[3], 2.0)
        self.assertTrue(exchange.created[4]["reduceOnly"])
        self.assertEqual(exchange.created[4]["tdMode"], "isolated")

    def test_short_sim_margin_and_pnl(self):
        with tempfile.TemporaryDirectory() as tmp:
            broker = SimBroker(os.path.join(tmp, "sim.json"), initial_balance=1000, default_fee_pct=0)
            opened = broker.execute_open("BTCUSDT", "SHORT", 1, 100, 100, leverage=2)
            self.assertTrue(opened.success)
            self.assertEqual(broker.get_usdt_balance(), 950)
            closed = broker.execute_close("BTCUSDT", "SHORT", 1, 90, 100, 100)
            self.assertTrue(closed.success)
            self.assertEqual(broker.get_usdt_balance(), 1010)

    def test_okx_position_history_uses_authoritative_realized_pnl(self):
        exchange = FakeSwap()
        client = CCXTExchangeClient(config=SWAP_CFG, exchange_instance=exchange)

        rows = client.get_position_history("XAUUSDT", since=1783470000000, limit=20)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(
            row["exchange_close_id"],
            "okx:3708019708102549504:1784206521546",
        )
        self.assertEqual(row["position_side"], "SHORT")
        self.assertAlmostEqual(row["quantity"], 0.033)
        self.assertAlmostEqual(row["entry_price"], 4115.3)
        self.assertAlmostEqual(row["exit_price"], 4000.5)
        self.assertAlmostEqual(row["realized_pnl"], 3.8292371028625467)
        self.assertAlmostEqual(row["fee_cost"], 0.1339107)
        self.assertAlmostEqual(row["funding_fee"], 0.1747478028625467)
        self.assertEqual(exchange.history_args[0], ["XAU/USDT:USDT"])
        self.assertEqual(exchange.history_args[3]["marginMode"], "isolated")

    def test_schema_v9_position_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.db")
            db = LiteDB(path)
            db.save_trade_state("BTCUSDT", "bought", entry_price=100, quantity=1,
                                position_side="SHORT", leverage=2, margin_mode="isolated")
            pos = db.get_trade_state("BTCUSDT")
            self.assertEqual(pos.position_side, "SHORT")
            self.assertEqual(pos.leverage, 2)
            conn = sqlite3.connect(path)
            try:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            finally:
                conn.close()

    def test_preflight_blocks_old_open_position(self):
        old = FakeSwap()
        old.fetch_positions = lambda symbols=None: [{
            "symbol": "BTC/USDT:USDT", "contracts": 1, "side": "short",
            "entryPrice": 100, "leverage": 1, "marginMode": "isolated",
        }]
        old_gateway = CCXTExchangeClient(config=SWAP_CFG, exchange_instance=old)
        result = run_exchange_preflight(
            SWAP_CFG, ["BTCUSDT"], current_client=old_gateway,
            client_factory=lambda *args: CCXTExchangeClient(config=SWAP_CFG, exchange_instance=FakeSwap()),
            websocket_factory=lambda *args, **kwargs: None,
        )
        self.assertFalse(result.ok)
        self.assertFalse(result.checks["old_exchange_flat"])


if __name__ == "__main__":
    unittest.main()
