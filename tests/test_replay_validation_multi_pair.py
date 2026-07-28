"""End-to-end replay pairing for a multi-pair engine run.

One engine cycle used to reuse a tick id for every symbol and validation then
indexed ticks by that id alone.  The XAU tick silently replaced the BTC tick;
validation also replayed every symbol through whichever ``--strategy`` was
selected.  A report could therefore say PASS while evaluating the wrong market.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import yaml

from xauby.observability.replay_validation import validate_run
from xauby.observability.event_coverage import audit_event_coverage
from xauby.strategies.signal import hold


class _Store:
    def __init__(self, events):
        self.events = events

    def iter_run(self, run_id, limit=10_000):
        return list(self.events)[:limit]


class _DB:
    def get_candles(self, symbol, timeframe, limit=300):
        base = 100.0 if symbol == "BTCUSDT" else 4_000.0
        return [
            {
                "timestamp": 1_700_000_000 + i * 14_400,
                "open": base,
                "high": base + 1,
                "low": base - 1,
                "close": base,
                "volume": 1.0,
            }
            for i in range(120)
        ]


class _Strategy:
    name = "test_strategy"
    version = "1.0.0"

    def __init__(self, seen):
        self.config = {"timeframe": "4h"}
        self.seen = seen

    def analyze(self, ctx):
        self.seen.append((ctx.symbol, ctx.current_price, ctx.position_side))
        return hold("same decision")


class TestMultiPairReplayPairing(unittest.TestCase):
    def test_event_integrity_requires_same_symbol_not_only_same_tick_id(self):
        events = [
            {"run_id": "r", "seq": 1, "event_type": "tick",
             "symbol": "XAUUSDT", "tick_id": "shared", "payload": {"price": 4000}},
            {"run_id": "r", "seq": 2, "event_type": "signal_evaluated",
             "symbol": "BTCUSDT", "tick_id": "shared", "payload": {"action": "HOLD"}},
        ]
        report = audit_event_coverage(events, run_id="r")
        self.assertFalse(report.tick_pairs_ok)
        self.assertIn("signal_without_tick:1", report.integrity_issues)

    def test_event_integrity_requires_order_lifecycle_on_same_symbol(self):
        events = [
            {"run_id": "r", "seq": 1, "event_type": "order_submitted",
             "symbol": "XAUUSDT", "payload": {"side": "BUY"}},
            {"run_id": "r", "seq": 2, "event_type": "order_filled",
             "symbol": "BTCUSDT", "payload": {"side": "BUY"}},
            {"run_id": "r", "seq": 3, "event_type": "position_opened",
             "symbol": "XAUUSDT", "payload": {"position_side": "LONG"}},
        ]
        report = audit_event_coverage(events, run_id="r")
        self.assertFalse(report.integrity_ok)
        self.assertIn("order_filled_without_submit:BUY@seq=2", report.integrity_issues)
        self.assertIn("position_opened_without_precursor:seq=3", report.integrity_issues)

    def test_legacy_same_tick_stop_and_reverse_is_a_valid_open_precursor(self):
        events = [
            {"run_id": "r", "seq": 1, "event_type": "position_closed",
             "symbol": "XAUUSDT", "tick_id": "reverse-1",
             "payload": {"position_side": "LONG"}},
            {"run_id": "r", "seq": 2, "event_type": "position_opened",
             "symbol": "XAUUSDT", "tick_id": "reverse-1",
             "payload": {"position_side": "SHORT"}},
        ]

        report = audit_event_coverage(events, run_id="r")

        self.assertTrue(report.integrity_ok, report.integrity_issues)

    def test_unrelated_close_does_not_hide_an_open_without_order_evidence(self):
        events = [
            {"run_id": "r", "seq": 1, "event_type": "position_closed",
             "symbol": "XAUUSDT", "tick_id": "another-tick",
             "payload": {"position_side": "LONG"}},
            {"run_id": "r", "seq": 2, "event_type": "position_opened",
             "symbol": "XAUUSDT", "tick_id": "open-tick",
             "payload": {"position_side": "SHORT"}},
        ]

        report = audit_event_coverage(events, run_id="r")

        self.assertFalse(report.integrity_ok)
        self.assertIn("position_opened_without_precursor:seq=2", report.integrity_issues)

    def test_filters_symbol_pairs_tick_by_symbol_and_restores_short(self):
        run = "run-1"
        shared = "legacy-shared-tick-id"
        ts = "2026-07-28T00:00:00+00:00"
        events = [
            {"run_id": run, "seq": 1, "ts": ts, "event_type": "engine_started",
             "symbol": "BTCUSDT", "payload": {}},
            {"run_id": run, "seq": 2, "ts": ts, "event_type": "position_restored",
             "symbol": "BTCUSDT", "payload": {"position_side": "SHORT", "stop_loss": 106.0}},
            # A different pair's state event must not overwrite BTC's SHORT.
            {"run_id": run, "seq": 3, "ts": ts, "event_type": "position_restored",
             "symbol": "XAUUSDT", "payload": {"position_side": "LONG", "stop_loss": 3900.0}},
            {"run_id": run, "seq": 4, "ts": ts, "event_type": "tick",
             "symbol": "BTCUSDT", "tick_id": shared, "payload": {"price": 100.0}},
            {"run_id": run, "seq": 5, "ts": ts, "event_type": "signal_evaluated",
             "symbol": "BTCUSDT", "tick_id": shared,
             "payload": {"action": "HOLD", "intent": "HOLD", "reason": "same decision"}},
            {"run_id": run, "seq": 6, "ts": ts, "event_type": "tick",
             "symbol": "XAUUSDT", "tick_id": shared, "payload": {"price": 4_000.0}},
            {"run_id": run, "seq": 7, "ts": ts, "event_type": "signal_evaluated",
             "symbol": "XAUUSDT", "tick_id": shared,
             "payload": {"action": "HOLD", "intent": "HOLD", "reason": "same decision"}},
        ]
        seen = []
        strategy = _Strategy(seen)
        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as cfg:
            yaml.safe_dump({
                "strategy": {"active": "test_strategy", "use_closed_candles": False},
                "strategies": {"test_strategy": {"timeframe": "4h"}},
                "trading": {"timeframe": "4h"},
            }, cfg)
            cfg.flush()
            with patch(
                "xauby.observability.replay_validation.load_strategy",
                return_value=strategy,
            ):
                report = validate_run(
                    _Store(events), _DB(), run,
                    symbol="BTCUSDT", config_path=cfg.name,
                    strategy_name="test_strategy", min_bars=1,
                )

        self.assertEqual(report.total_signals, 1)
        self.assertEqual(report.matched, 1)
        self.assertEqual(report.mismatched, 0)
        self.assertEqual(report.skipped, 0)
        self.assertEqual(report.short_signals_checked, 1)
        self.assertTrue(report.to_dict()["short_side_checked"])
        self.assertEqual(seen, [("BTCUSDT", 100.0, "SHORT")])

    def test_resolves_strategy_and_overrides_from_symbol_whitelist(self):
        run = "run-whitelist"
        ts = "2026-07-28T00:00:00+00:00"
        events = [
            {"run_id": run, "seq": 1, "ts": ts, "event_type": "tick",
             "symbol": "BTCUSDT", "tick_id": "btc-tick", "payload": {"price": 100.0}},
            {"run_id": run, "seq": 2, "ts": ts, "event_type": "signal_evaluated",
             "symbol": "BTCUSDT", "tick_id": "btc-tick",
             "payload": {"action": "HOLD", "intent": "HOLD", "reason": "same decision"}},
        ]
        seen = []
        strategy = _Strategy(seen)
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "bot_config.yaml")
            with open(config_path, "w", encoding="utf-8") as cfg:
                yaml.safe_dump({
                    "architecture": {"whitelist_strict": True},
                    "strategy": {
                        "active": "xauby_actionzone",
                        "use_closed_candles": False,
                        "config": {
                            "test_strategy": {
                                "primary_timeframe": "1h",
                                "confirm_timeframe": "1d",
                            }
                        },
                    },
                    "trading": {"timeframe": "4h"},
                    "portfolio": {"quote_asset": "USDT"},
                }, cfg)
            with open(os.path.join(tmp, "coin_whitelist.json"), "w", encoding="utf-8") as whitelist:
                json.dump({
                    "quote_asset": "USDT",
                    "assets": [{
                        "symbol": "BTC",
                        "strategy": "test_strategy",
                        "strategy_params": {"primary_timeframe": "4h", "marker": "btc"},
                    }],
                }, whitelist)

            loaded_configs = []

            def _load(name, config):
                self.assertEqual(name, "test_strategy")
                loaded_configs.append(dict(config))
                strategy.config = dict(config)
                return strategy

            with patch(
                "xauby.observability.replay_validation.load_strategy",
                side_effect=_load,
            ):
                report = validate_run(
                    _Store(events), _DB(), run,
                    symbol="BTCUSDT", config_path=config_path, min_bars=1,
                )

        self.assertEqual(report.strategy_name, "test_strategy")
        self.assertEqual(report.matched, 1)
        self.assertEqual(loaded_configs[0]["marker"], "btc")
        self.assertEqual(loaded_configs[0]["primary_timeframe"], "4h")


if __name__ == "__main__":
    unittest.main()
