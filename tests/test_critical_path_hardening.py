"""Pre-deploy hardening tests for the live-trading critical path.

Covers the most dangerous edge cases surfaced in the QA review:
  * equity_peak.json must survive a crash mid-save (atomic write) — otherwise the
    drawdown high-water mark is lost and the circuit breaker silently resets.
  * startup reconciliation must not crash (or reset a real position) when the
    exchange balance call returns None.
  * execute_buy must refuse a non-positive price.
  * a zero stop-loss distance must abort the BUY (no divide-by-zero / no order).
  * the drawdown guard must actually block a BUY below the high-water mark.

These follow the lightweight mixin-subclass pattern used by
``tests/test_order_allocation_guard.py`` and ``tests/test_entry_market_fallback.py``,
so they need neither ``bot_config.yaml`` nor the real ``coin_whitelist.json``.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from xauby.engine.orders import OrderMixin
from xauby.engine.reconcile import ReconcileMixin
from xauby.engine.risk import RiskMixin
from xauby.utils.atomic_io import atomic_json_write


# ---------------------------------------------------------------------------
# atomic_json_write durability — foundation for the equity_peak fix
# ---------------------------------------------------------------------------
class TestAtomicWriteDurability(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "equity_peak.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_no_torn_write_when_dump_fails(self):
        atomic_json_write(self.path, {"peak": 100.0})
        # A failure mid-serialize must leave the original file fully intact.
        with mock.patch("xauby.utils.atomic_io.json.dump", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                atomic_json_write(self.path, {"peak": 200.0})
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"peak": 100.0})
        # And no temp file should be left behind.
        leftovers = [n for n in os.listdir(self.dir) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])


# ---------------------------------------------------------------------------
# Equity-peak persistence + drawdown guard (RiskMixin)
# ---------------------------------------------------------------------------
class _RiskEngine(RiskMixin):
    def __init__(self):
        self.config = {
            "risk": {
                "drawdown_guard": {
                    "enabled": True,
                    "max_drawdown_pct": 20.0,
                    # keep the capital-flow rebase out of the way for these tests
                    "auto_rebase_capital_flows": False,
                }
            }
        }
        self.simulate_only = True  # -> equity-peak scope resolves to "sim"
        self._equity_peak_cache_by_scope = {}
        self._equity_peak_cache = 0.0


class _RiskTestBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._old_home = os.environ.get("XAUBY_HOME")
        os.environ["XAUBY_HOME"] = self.dir  # isolate runtime_root() from real core/
        self.engine = _RiskEngine()

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("XAUBY_HOME", None)
        else:
            os.environ["XAUBY_HOME"] = self._old_home
        shutil.rmtree(self.dir, ignore_errors=True)

    def _peak_file(self):
        from xauby.runtime.paths import equity_peak_path
        return equity_peak_path()


class TestEquityPeakAtomic(_RiskTestBase):
    def test_save_equity_peak_writes_atomically(self):
        self.engine._save_equity_peak(150.0, "BTCUSDT")
        with open(self._peak_file(), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data.get("peak"), 150.0)
        # No stray temp file left in the runtime dir.
        d = os.path.dirname(self._peak_file())
        self.assertEqual([n for n in os.listdir(d) if n.endswith(".tmp")], [])

    def test_existing_peak_preserved_when_save_fails(self):
        self.engine._save_equity_peak(100.0, "BTCUSDT")  # good baseline on disk
        with mock.patch("xauby.utils.atomic_io.json.dump", side_effect=RuntimeError("disk full")):
            # _save_equity_peak swallows the error internally; it must not crash
            # and must not truncate the existing high-water mark.
            self.engine._save_equity_peak(200.0, "BTCUSDT")
        with open(self._peak_file(), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data.get("peak"), 100.0)


class TestDrawdownGuard(_RiskTestBase):
    def test_blocks_buy_below_high_water_mark(self):
        self.engine.update_equity_peak(1000.0, "BTCUSDT")
        allowed, reason = self.engine.check_drawdown_guard(700.0, "BTCUSDT")  # -30%
        self.assertFalse(allowed)
        self.assertIn("Drawdown guard", reason)

    def test_allows_buy_within_drawdown(self):
        self.engine.update_equity_peak(1000.0, "BTCUSDT")
        allowed, _ = self.engine.check_drawdown_guard(950.0, "BTCUSDT")  # -5% < 20%
        self.assertTrue(allowed)


# ---------------------------------------------------------------------------
# Startup reconciliation null-safety (ReconcileMixin)
# ---------------------------------------------------------------------------
class _ReconcileDB:
    def __init__(self, state):
        self._state = state
        self.saved = []

    def get_trade_state(self, symbol):
        return dict(self._state)

    def save_trade_state(self, *args, **kwargs):
        self.saved.append((args, kwargs))


class _NoneBalancesClient:
    """Simulates an exchange/API hiccup: balances come back as None."""

    def get_balances(self):
        return None

    def get_symbol_filters(self, symbol):
        return {"minQty": 0.0, "stepSize": 0.0001}

    def get_ticker(self, symbol):
        return {"last": 100.0}


class _HaltRecorder:
    def __init__(self):
        self.halted = []

    def set_trading_halted(self, flag, reason):
        self.halted.append((flag, reason))


class _ReconcileEngine(ReconcileMixin):
    def __init__(self, state):
        self.config = {"exchange": {"market_type": "spot"}}
        self.client = _NoneBalancesClient()
        self.db = _ReconcileDB(state)
        self._halts = _HaltRecorder()
        self.alerts = []

    def _get_base_asset(self, symbol):
        return symbol[:-4] if symbol.endswith("USDT") else symbol

    def _sc(self, symbol):
        return self._halts

    def _cancel_orphan_orders(self, **kwargs):
        pass

    def _strategy_name_for_symbol(self, symbol=None):
        return "cdc_action_zone"

    def _ensure_sl_protected(self, state, symbol=None):
        return state

    def send_telegram_alert(self, *args, **kwargs):
        self.alerts.append(args[0] if args else "")


class TestReconcileNoneBalances(unittest.TestCase):
    def test_bought_state_not_reset_when_balances_none(self):
        engine = _ReconcileEngine({"symbol": "BTCUSDT", "state": "bought", "quantity": 1.0})
        engine._reconcile_single_symbol("BTCUSDT")  # must not raise
        # A None balance must NOT cause the live position to be reset to idle.
        self.assertEqual(engine.db.saved, [])

    def test_detect_spot_orphan_does_not_halt_when_balances_none(self):
        engine = _ReconcileEngine({"symbol": "BTCUSDT", "state": "idle", "quantity": 0.0})
        engine._detect_spot_orphan("BTCUSDT", {"symbol": "BTCUSDT", "state": "idle"})
        # Cannot assess an orphan without balances -> must not false-halt trading.
        self.assertEqual(engine._halts.halted, [])


# ---------------------------------------------------------------------------
# execute_buy entry guards (OrderMixin)
# ---------------------------------------------------------------------------
class _IdleDB:
    def __init__(self):
        self.saved = None

    def get_trade_state(self, symbol):
        return {"symbol": symbol, "state": "idle", "quantity": 0.0}

    def save_trade_state(self, *args, **kwargs):
        self.saved = (args, kwargs)


class _BuyClient:
    def __init__(self):
        self.order_called = False

    def get_balances(self):
        return {"USDT": {"available": 1000.0, "reserved": 0.0}}

    def place_order(self, *args, **kwargs):
        self.order_called = True
        return {"orderId": "x", "status": "FILLED", "executedQty": 0.0, "cummulativeQuoteQty": 0.0}


class _BuyEngine(OrderMixin):
    def __init__(self):
        self.config = {
            "portfolio": {
                "position_sizing": {
                    "risk_pct": 0.02,
                    "max_position_per_trade_pct": 50.0,
                    "min_order_amount": 10.0,
                },
                "symbols": {
                    "BTCUSDT": {
                        "allocation_pct": 100.0,
                        "position_sizing": {
                            "risk_pct": 0.02,
                            "max_position_per_trade_pct": 50.0,
                            "min_order_amount": 10.0,
                        },
                    }
                },
            },
            "strategy": {
                "active": "cdc_action_zone",
                "config": {"cdc_action_zone": {"sl_atr_mult": 2.0}},
            },
        }
        self.client = _BuyClient()
        self.db = _IdleDB()
        self.simulate_only = False
        self.read_only = False
        self.last_log_message = ""
        self.events = []
        self.alerts = []

    def _sym(self):
        return "BTCUSDT"

    def _use_sim_broker(self, symbol):
        return False

    def _get_base_asset(self, symbol):
        return symbol[:-4] if symbol.endswith("USDT") else symbol

    def _quote_asset(self):
        return "USDT"

    def _strategy_name_for_symbol(self, symbol=None):
        return "cdc_action_zone"

    def get_equity(self, ticker_price=None, symbol=None):
        return 1000.0

    def check_daily_protections(self, equity, symbol=None):
        return True, ""

    def check_drawdown_guard(self, equity):
        return True, ""

    def _emit_event(self, event_type, **payload):
        self.events.append((event_type, payload))

    def send_telegram_alert(self, *args, **kwargs):
        self.alerts.append(args[0] if args else "")


class TestExecuteBuyGuards(unittest.TestCase):
    def test_nonpositive_price_aborts_without_order(self):
        for px in (0.0, -5.0):
            engine = _BuyEngine()
            ok = engine.execute_buy(ticker_price=px, atr=1.0, symbol="BTCUSDT")
            self.assertFalse(ok)
            self.assertFalse(engine.client.order_called)
            self.assertIsNone(engine.db.saved)

    def test_zero_sl_distance_aborts_without_order(self):
        engine = _BuyEngine()
        # atr=0 with no signal SL -> sl_distance = atr * sl_atr_mult = 0 -> abort.
        ok = engine.execute_buy(ticker_price=100.0, atr=0.0, symbol="BTCUSDT")
        self.assertFalse(ok)
        self.assertFalse(engine.client.order_called)
        self.assertIsNone(engine.db.saved)


if __name__ == "__main__":
    unittest.main()
