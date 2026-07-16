import threading
import time
import unittest
from types import SimpleNamespace

from xauby.engine.loop import LoopMixin


class _PairRegistry:
    def __init__(self, specs):
        self._specs = list(specs)

    def active(self):
        return list(self._specs)

    def get(self, symbol):
        sym = symbol.upper().replace("_", "")
        return next((s for s in self._specs if s.symbol == sym), None)


class _DB:
    def __init__(self):
        self.saved = []
        self.save_thread_ids = []

    def get_candles(self, symbol, timeframe, limit=1):
        return []

    def save_candles(self, symbol, timeframe, candles):
        self.save_thread_ids.append(threading.get_ident())
        self.saved.append((symbol, timeframe, list(candles)))


class _SlowClient:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()
        self.release = threading.Event()

    def get_candles(self, symbol, timeframe, limit=250):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.max_active >= 2:
                self.release.set()
        try:
            self.release.wait(timeout=0.5)
            open_ts_ms = int((time.time() - 7200) * 1000)
            return [[open_ts_ms, 1.0, 2.0, 0.5, 1.5, 10.0]]
        finally:
            with self.lock:
                self.active -= 1


class _Engine(LoopMixin):
    def __init__(self):
        self.config = {
            "data": {
                "candle_sync_max_interval_seconds": 900,
                "candle_sync_max_workers": 3,
                # This test pins the strategy timeframes only; the dashboard
                # timeframe fan-out has its own expectations below.
                "dashboard_timeframes": [],
            }
        }
        self.db = _DB()
        self.client = _SlowClient()
        self._candle_sync_last = {}
        self._latency_metrics = {}
        self._specs = [
            SimpleNamespace(symbol="AAAUSDT", enabled=True),
            SimpleNamespace(symbol="BBBUSDT", enabled=True),
        ]
        self._pair_registry = _PairRegistry(self._specs)
        self.contexts = {
            "AAAUSDT": SimpleNamespace(primary_timeframe="1h", timeframe_regime="4h"),
            "BBBUSDT": SimpleNamespace(primary_timeframe="1h", timeframe_regime=None),
        }

    def _sc(self, symbol=None):
        return self.contexts[symbol.upper().replace("_", "")]

    @staticmethod
    def _timeframe_ms(timeframe):
        return {
            "1h": 60 * 60 * 1000,
            "4h": 4 * 60 * 60 * 1000,
            "1d": 24 * 60 * 60 * 1000,
        }[timeframe]


class TestCandleSyncConcurrency(unittest.TestCase):
    def test_fetches_candles_concurrently_and_saves_serially(self):
        engine = _Engine()
        caller_thread = threading.get_ident()

        engine.sync_candles()

        self.assertGreaterEqual(engine.client.max_active, 2)
        self.assertEqual(len(engine.db.saved), 3)
        self.assertEqual(set(engine.db.save_thread_ids), {caller_thread})
        self.assertEqual(engine._latency_metrics["candle_sync_fetches"], 3)
        self.assertEqual(engine._latency_metrics["candle_sync_skipped"], 0)
        self.assertEqual(engine._latency_metrics["candle_sync_failed"], 0)
        self.assertEqual(engine._latency_metrics["candle_sync_workers"], 3)

    def test_dashboard_timeframes_fan_out_by_default(self):
        engine = _Engine()
        del engine.config["data"]["dashboard_timeframes"]

        engine.sync_candles()

        # AAAUSDT: 1h (primary) + 4h (regime) + 1d (dashboard);
        # BBBUSDT: 1h (primary) + 4h + 1d (dashboard) = 6 unique jobs.
        self.assertEqual(engine._latency_metrics["candle_sync_fetches"], 6)
        self.assertEqual(
            sorted({(sym, tf) for sym, tf, _ in engine.db.saved}),
            [("AAAUSDT", "1d"), ("AAAUSDT", "1h"), ("AAAUSDT", "4h"),
             ("BBBUSDT", "1d"), ("BBBUSDT", "1h"), ("BBBUSDT", "4h")],
        )


if __name__ == "__main__":
    unittest.main()
