"""Tests for the static strategy sandbox scan and strict-mode enforcement (item 5)."""

import importlib.util
import json
import math
import os
import sys
import tempfile
import textwrap
import unittest

from xauby.strategies.sandbox import StrategyRunner, StrategySandboxError
from xauby.strategies.sandbox_scan import scan_source, scan_strategy
from xauby.strategies.base import Strategy
from xauby.strategies.context import MarketContext
from xauby.strategies.signal import hold

import pandas as pd


CLEAN_SRC = textwrap.dedent(
    """
    from __future__ import annotations
    import math
    import numpy as np
    from xauby.strategies.signal import hold

    class CleanStrategy:
        name = "clean"
        def analyze(self, ctx):
            return hold("ok")
    """
)

BAD_SRC = textwrap.dedent(
    """
    import os
    import requests
    from xauby.engine.trading import LiteTradingEngine

    class EvilStrategy:
        name = "evil"
        def analyze(self, ctx):
            import subprocess
            return eval("1+1")
    """
)


class TestScanSource(unittest.TestCase):
    def test_clean_source_has_no_violations(self):
        self.assertEqual(scan_source(CLEAN_SRC), [])

    def test_detects_forbidden_and_engine_imports(self):
        kinds = {(v.kind, v.name) for v in scan_source(BAD_SRC)}
        self.assertIn(("import", "os"), kinds)
        self.assertIn(("import", "requests"), kinds)
        self.assertIn(("import", "xauby.engine.trading"), kinds)
        # function-level import is caught too
        self.assertIn(("import", "subprocess"), kinds)
        # eval builtin
        self.assertIn(("call", "eval"), kinds)

    def test_allows_strategies_subpackage(self):
        src = "from xauby.strategies.indicators import base\n"
        self.assertEqual(scan_source(src), [])

    def test_allows_relative_import(self):
        src = "from . import indicators\nfrom .foo import bar\n"
        self.assertEqual(scan_source(src), [])

    def test_detects_globals_escape(self):
        src = "def f(x):\n    return x.__globals__['os']\n"
        v = scan_source(src)
        self.assertTrue(any(item.name == "__globals__" for item in v))

    def test_syntax_error_reported(self):
        v = scan_source("def f(:\n")
        self.assertTrue(v and v[0].kind == "syntax")


def _load_obj_from_source(src: str, modname: str):
    tmp = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False)
    tmp.write(src)
    tmp.close()
    spec = importlib.util.spec_from_file_location(modname, tmp.name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = module
    spec.loader.exec_module(module)
    return module, tmp.name


class TestRunnerStrictMode(unittest.TestCase):
    def setUp(self):
        self._tmpfiles = []

    def tearDown(self):
        for f in self._tmpfiles:
            try:
                os.unlink(f)
            except OSError:
                pass

    def _make(self, src, modname, cls_name):
        module, path = _load_obj_from_source(src, modname)
        self._tmpfiles.append(path)
        return getattr(module, cls_name)()

    def test_strict_rejects_bad_strategy(self):
        bad = self._make(BAD_SRC, "xauby_test_evil_mod", "EvilStrategy")
        with self.assertRaises(StrategySandboxError):
            StrategyRunner(bad, check_imports=True, strict=True)

    def test_non_strict_warns_but_loads(self):
        bad = self._make(BAD_SRC, "xauby_test_evil_mod2", "EvilStrategy")
        runner = StrategyRunner(bad, check_imports=True, strict=False)
        self.assertIsNotNone(runner)

    def test_clean_strategy_passes_strict(self):
        clean = self._make(CLEAN_SRC, "xauby_test_clean_mod", "CleanStrategy")
        runner = StrategyRunner(clean, check_imports=True, strict=True)
        self.assertIsNotNone(runner)

    def test_runner_sanitizes_nonfinite_signal_payload(self):
        class NonFiniteStrategy(Strategy):
            name = "nonfinite"

            def analyze(self, ctx):
                return hold(
                    "flat data",
                    confidence=float("nan"),
                    volatility=float("inf"),
                    indicators={"rsi": float("nan"), "nested": [float("-inf")]},
                    checklist=[{"label": "RSI", "value": float("nan"), "ok": False}],
                )

        signal = StrategyRunner(NonFiniteStrategy(), check_imports=False).run(
            MarketContext(
                symbol="TESTUSDT",
                timeframe_primary="1h",
                df_primary=pd.DataFrame(),
                current_price=0.0,
            )
        )

        json.dumps(signal.indicators, allow_nan=False)
        json.dumps(signal.checklist, allow_nan=False)
        self.assertTrue(math.isfinite(signal.confidence))
        self.assertTrue(math.isfinite(signal.volatility))
        self.assertEqual(signal.indicators["rsi"], 0.0)

    def test_runner_replaces_invalid_result_type_with_hold(self):
        class InvalidResultStrategy(Strategy):
            name = "invalid_result"

            def analyze(self, ctx):
                return {"action": "BUY"}

        signal = StrategyRunner(
            InvalidResultStrategy(), check_imports=False
        ).run(
            MarketContext(
                symbol="TESTUSDT",
                timeframe_primary="1h",
                df_primary=pd.DataFrame(),
                current_price=0.0,
            )
        )

        self.assertEqual(signal.action, "HOLD")
        self.assertIn("invalid type", signal.reason)
        self.assertEqual(signal.strategy_name, "invalid_result")


class TestBundledStrategiesPassScan(unittest.TestCase):
    def test_all_bundled_strategies_clean(self):
        from xauby.strategies import available_strategies, load_strategy

        offenders = {}
        for name in available_strategies():
            try:
                strat = load_strategy(name, {})
            except Exception:
                continue
            violations = scan_strategy(strat)
            if violations:
                offenders[name] = [str(v) for v in violations]
        self.assertEqual(offenders, {})


if __name__ == "__main__":
    unittest.main()
