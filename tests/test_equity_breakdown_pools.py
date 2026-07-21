"""get_symbol_equity_breakdown must not mix real and virtual capital pools.

Regression: in a mixed sim+live portfolio a sim pair used to report the REAL
exchange portfolio total (get_portfolio_equity_total) next to its VIRTUAL
SimBroker cash, so the two pairs' equity cards drew from different pools and
never reconciled. A sim pair must total its own virtual pool.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xauby.engine.loop import LoopMixin


class _Spec:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol


class _Registry:
    def __init__(self, symbols):
        self._specs = [_Spec(s) for s in symbols]

    def active(self):
        return self._specs


class _DB:
    def __init__(self, states):
        self._states = states

    def get_trade_state(self, sym):
        return self._states.get(sym, {"state": "none"})


class _EngineStub:
    """Minimal surface for the two equity methods under test."""

    def __init__(self, *, sim_symbols, live_symbols, sim_cash, real_total,
                 states, prices):
        self._pair_registry = _Registry(sim_symbols + live_symbols)
        self._sim_symbols = set(sim_symbols)
        self.db = _DB(states)
        self._sim_cash = sim_cash
        self._real_total = real_total
        self._prices = prices

    # --- surface used by the real methods -------------------------------- #
    def _use_sim_broker(self, sym):
        return sym in self._sim_symbols

    def get_simulated_balance(self):
        return self._sim_cash

    def get_portfolio_equity_total(self):
        return self._real_total

    def _price_for_symbol(self, sym):
        return self._prices.get(sym, 0.0)

    def _get_base_asset(self, sym):
        return sym.replace("USDT", "")

    def _quote_asset(self):
        return "USDT"

    def _symbol_fee_pct(self, sym):
        return 0.0005

    def _balance_totals_map(self):
        # Live pair path: real exchange USDT + base holdings.
        return {"USDT": self._real_total}

    # bind the real implementations
    _sim_portfolio_equity_total = LoopMixin._sim_portfolio_equity_total
    get_symbol_equity_breakdown = LoopMixin.get_symbol_equity_breakdown


class TestEquityBreakdownPools(unittest.TestCase):
    def test_sim_pair_totals_its_own_virtual_pool(self):
        # BTC is sim (virtual 1000); XAU is live (real 50000). Both flat.
        eng = _EngineStub(
            sim_symbols=["BTCUSDT"],
            live_symbols=["XAUUSDT"],
            sim_cash=1000.0,
            real_total=50000.0,
            states={"BTCUSDT": {"state": "none"}, "XAUUSDT": {"state": "none"}},
            prices={"BTCUSDT": 60000.0, "XAUUSDT": 2400.0},
        )
        btc = eng.get_symbol_equity_breakdown("BTCUSDT")
        xau = eng.get_symbol_equity_breakdown("XAUUSDT")

        # The sim card is internally consistent: total == its virtual cash,
        # NOT the 50000 real portfolio (the pre-fix bug).
        self.assertEqual(btc["portfolio_total_usdt"], 1000.0)
        self.assertEqual(btc["usdt_balance_usdt"], 1000.0)
        # The live card reflects the real pool.
        self.assertEqual(xau["portfolio_total_usdt"], 50000.0)
        # Two different pools -> different totals (expected, not a bug).
        self.assertNotEqual(btc["portfolio_total_usdt"], xau["portfolio_total_usdt"])

    def test_sim_pool_includes_open_sim_position_value(self):
        eng = _EngineStub(
            sim_symbols=["BTCUSDT"],
            live_symbols=[],
            sim_cash=1000.0,
            real_total=99999.0,  # must be ignored for a sim pair
            states={"BTCUSDT": {
                "state": "bought", "position_side": "LONG",
                "entry_price": 60000.0, "quantity": 0.01,
            }},
            prices={"BTCUSDT": 60000.0},
        )
        btc = eng.get_symbol_equity_breakdown("BTCUSDT")
        # virtual cash 1000 + position value (0.01 * 60000 = 600) = 1600.
        self.assertEqual(btc["portfolio_total_usdt"], 1600.0)
        self.assertNotEqual(btc["portfolio_total_usdt"], 99999.0)

    def test_live_swap_position_exposure_uses_position_notional(self):
        # Regression: a swap position holds no spot base asset, so exposure
        # used to collapse to bare unrealized PnL (spot-era base-holdings math)
        # and the workspace allocation bar sat at its 2% display floor.
        eng = _EngineStub(
            sim_symbols=[],
            live_symbols=["XAUUSDT"],
            sim_cash=0.0,
            real_total=179.0,
            states={"XAUUSDT": {
                "state": "bought", "position_side": "LONG",
                "entry_price": 4000.0, "quantity": 0.015,
            }},
            prices={"XAUUSDT": 4100.0},
        )
        xau = eng.get_symbol_equity_breakdown("XAUUSDT")
        notional = 0.015 * 4100.0  # 61.5
        gross = (4100.0 - 4000.0) * 0.015
        fees = (4000.0 + 4100.0) * 0.015 * 0.0005
        self.assertEqual(
            xau["symbol_exposure_usdt"], round(notional + gross - fees, 2)
        )
        # Spot holdings stay zero for a swap pair; exposure must not.
        self.assertEqual(xau["base_value_usdt"], 0.0)

    def test_sim_short_exposure_uses_position_notional(self):
        eng = _EngineStub(
            sim_symbols=["BTCUSDT"],
            live_symbols=[],
            sim_cash=1000.0,
            real_total=0.0,
            states={"BTCUSDT": {
                "state": "bought", "position_side": "SHORT",
                "entry_price": 60000.0, "quantity": 0.01,
            }},
            prices={"BTCUSDT": 59000.0},
        )
        eng._sim_broker = types.SimpleNamespace(
            get_ledger=lambda sym: types.SimpleNamespace(
                margin_reserved=600.0, unrealized_pnl=10.0
            )
        )
        btc = eng.get_symbol_equity_breakdown("BTCUSDT")
        notional = 0.01 * 59000.0  # 590
        gross = (60000.0 - 59000.0) * 0.01  # short gains as price falls
        fees = (60000.0 + 59000.0) * 0.01 * 0.0005
        self.assertEqual(
            btc["symbol_exposure_usdt"], round(notional + gross - fees, 2)
        )

    def test_all_sim_pairs_share_one_total(self):
        eng = _EngineStub(
            sim_symbols=["BTCUSDT", "XAUUSDT"],
            live_symbols=[],
            sim_cash=1000.0,
            real_total=50000.0,
            states={"BTCUSDT": {"state": "none"}, "XAUUSDT": {"state": "none"}},
            prices={"BTCUSDT": 60000.0, "XAUUSDT": 2400.0},
        )
        btc = eng.get_symbol_equity_breakdown("BTCUSDT")
        xau = eng.get_symbol_equity_breakdown("XAUUSDT")
        self.assertEqual(btc["portfolio_total_usdt"], xau["portfolio_total_usdt"])
        self.assertEqual(btc["portfolio_total_usdt"], 1000.0)


if __name__ == "__main__":
    unittest.main()
