import copy
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from scripts.audit_release_readiness import _runtime_symbols, audit_runtime, audit_static


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestReleaseReadiness(unittest.TestCase):
    def test_runtime_symbols_expand_aggregate_engine_started_metadata(self):
        events = [
            {"event_type": "engine_started", "symbol": "BTCUSDT, XAUUSDT"},
            {"event_type": "tick", "symbol": "BTCUSDT"},
        ]
        self.assertEqual(_runtime_symbols(events), ["BTCUSDT", "XAUUSDT"])
        self.assertEqual(_runtime_symbols(events, explicit="xau_usdt"), ["XAUUSDT"])

    def test_runtime_audit_validates_each_pair_without_explicit_symbol(self):
        class _Connection:
            def execute(self, query):
                return SimpleNamespace(fetchone=lambda: (13,))

            def close(self):
                pass

        class _DB:
            def _get_connection(self):
                return _Connection()

        reports = {
            "BTCUSDT": SimpleNamespace(
                matched=2, mismatched=0, skipped=0,
                short_signals_checked=1, ok=True,
            ),
            "XAUUSDT": SimpleNamespace(
                matched=3, mismatched=0, skipped=0,
                short_signals_checked=0, ok=True,
            ),
        }

        with patch("xauby.database.db.LiteDB", return_value=_DB()), \
             patch("xauby.observability.store.EventStore"), \
             patch(
                 "xauby.observability.incidents.load_run_timeline",
                 return_value=[{
                     "event_type": "engine_started",
                     "symbol": "BTCUSDT, XAUUSDT",
                 }],
             ), \
             patch(
                 "xauby.observability.replay_validation.validate_run",
                 side_effect=lambda store, db, run_id, *, symbol, config_path: reports[symbol],
             ):
            checks = audit_runtime(
                config_path="tenant.yaml",
                db_path="runtime.db",
                run_id="run-1",
            )

        by_name = {check.name: check for check in checks}
        self.assertTrue(by_name["runtime_replay:BTCUSDT"].ok)
        self.assertTrue(by_name["runtime_replay:XAUUSDT"].ok)
        self.assertEqual(
            set(by_name),
            {"runtime_schema", "runtime_replay:BTCUSDT", "runtime_replay:XAUUSDT"},
        )

    def test_repo_phase_0_and_1_static_gates_pass(self):
        checks = audit_static(
            os.path.join(ROOT, "bot_config.yaml"),
            os.path.join(ROOT, "coin_whitelist.json"),
        )
        failed = [f"{check.name}: {check.detail}" for check in checks if not check.ok]
        self.assertEqual(failed, [])

    def test_durable_events_cannot_be_disabled_silently(self):
        with open(os.path.join(ROOT, "bot_config.yaml"), "r", encoding="utf-8") as handle:
            cfg = yaml.safe_load(handle)
        cfg = copy.deepcopy(cfg)
        cfg["observability"]["durable_high_frequency_events"] = False
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bot_config.yaml")
            with open(path, "w", encoding="utf-8") as handle:
                yaml.safe_dump(cfg, handle)
            checks = audit_static(path, os.path.join(ROOT, "coin_whitelist.json"))
        durable = next(check for check in checks if check.name == "durable_events")
        self.assertFalse(durable.ok)

    def test_audits_cannot_be_configured_to_fail_silently(self):
        with open(os.path.join(ROOT, "bot_config.yaml"), "r", encoding="utf-8") as handle:
            cfg = yaml.safe_load(handle)
        cfg = copy.deepcopy(cfg)
        cfg["self_audit"]["send_telegram"] = False
        cfg["monthly_report"]["send_telegram"] = False
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bot_config.yaml")
            with open(path, "w", encoding="utf-8") as handle:
                yaml.safe_dump(cfg, handle)
            checks = audit_static(path, os.path.join(ROOT, "coin_whitelist.json"))
        by_name = {check.name: check for check in checks}
        self.assertFalse(by_name["self_audit_scheduled"].ok)
        self.assertFalse(by_name["monthly_track_record"].ok)


if __name__ == "__main__":
    unittest.main()
