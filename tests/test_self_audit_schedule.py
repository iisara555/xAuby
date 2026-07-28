"""The self-audit has to actually run. Roadmap P1.6.

`audit_track_record` shipped and was tested, but nothing in the runtime ever
called it — the only caller was a unit test. A reconciliation check that never
executes against real data is indistinguishable from not having one, so these
tests are about the wiring rather than the arithmetic.
"""
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xauby.notifications.scheduler import ReportScheduler  # noqa: E402


def _engine():
    engine = MagicMock()
    engine.db = MagicMock()
    return engine


def _scheduler(**audit_cfg):
    cfg = {"weekly_review": {"enabled": False},
           "daily_digest": {"enabled": False},
           "self_audit": {"enabled": True, "hour_utc": 11, **audit_cfg}}
    return ReportScheduler(cfg)


AT_ELEVEN = datetime(2026, 7, 27, 11, 30, tzinfo=timezone.utc)
AT_NOON = datetime(2026, 7, 27, 12, 30, tzinfo=timezone.utc)
NEXT_DAY = datetime(2026, 7, 28, 11, 5, tzinfo=timezone.utc)


class TestScheduling(unittest.TestCase):
    def test_it_runs_at_its_hour(self):
        engine = _engine()
        with patch("xauby.track_record.validator.audit_track_record",
                   return_value=(True, [], {"matching_trades": 3,
                                            "symbols": ["XAUUSDT"]})) as audit:
            _scheduler().check_and_run(engine, AT_ELEVEN)
        audit.assert_called_once_with(engine.db)

    def test_it_does_not_run_at_other_hours(self):
        with patch("xauby.track_record.validator.audit_track_record") as audit:
            _scheduler().check_and_run(_engine(), AT_NOON)
        audit.assert_not_called()

    def test_it_runs_once_per_day_not_once_per_tick(self):
        scheduler = _scheduler()
        engine = _engine()
        with patch("xauby.track_record.validator.audit_track_record",
                   return_value=(True, [], {})) as audit:
            scheduler.check_and_run(engine, AT_ELEVEN)
            scheduler.check_and_run(engine, AT_ELEVEN)
            scheduler.check_and_run(engine, AT_ELEVEN)
            self.assertEqual(audit.call_count, 1)
            scheduler.check_and_run(engine, NEXT_DAY)
            self.assertEqual(audit.call_count, 2)

    def test_disabled_means_disabled(self):
        cfg = {"weekly_review": {"enabled": False}, "daily_digest": {"enabled": False},
               "self_audit": {"enabled": False, "hour_utc": 11}}
        with patch("xauby.track_record.validator.audit_track_record") as audit:
            ReportScheduler(cfg).check_and_run(_engine(), AT_ELEVEN)
        audit.assert_not_called()

    def test_absent_config_does_not_run_it(self):
        with patch("xauby.track_record.validator.audit_track_record") as audit:
            ReportScheduler({}).check_and_run(_engine(), AT_ELEVEN)
        audit.assert_not_called()


class TestAlerting(unittest.TestCase):
    def test_a_clean_audit_stays_quiet(self):
        engine = _engine()
        with patch("xauby.track_record.validator.audit_track_record",
                   return_value=(True, [], {"matching_trades": 2, "symbols": []})):
            _scheduler().check_and_run(engine, AT_ELEVEN)
        engine.send_telegram_alert.assert_not_called()

    def test_discrepancies_reach_the_operator(self):
        engine = _engine()
        found = [f"[XAUUSDT] trade {i}: exit price mismatch" for i in range(3)]
        with patch("xauby.track_record.validator.audit_track_record",
                   return_value=(False, found, {})):
            _scheduler().check_and_run(engine, AT_ELEVEN)
        engine.send_telegram_alert.assert_called_once()
        message = engine.send_telegram_alert.call_args[0][0]
        self.assertIn("3 discrepancies", message)
        self.assertIn("[XAUUSDT]", message)

    def test_a_flood_is_truncated_rather_than_sent_whole(self):
        engine = _engine()
        found = [f"[XAUUSDT] trade {i}: mismatch" for i in range(40)]
        with patch("xauby.track_record.validator.audit_track_record",
                   return_value=(False, found, {})):
            _scheduler().check_and_run(engine, AT_ELEVEN)
        message = engine.send_telegram_alert.call_args[0][0]
        self.assertIn("40 discrepancies", message)
        self.assertIn("and 35 more", message)

    def test_telegram_can_be_turned_off_without_disabling_the_audit(self):
        engine = _engine()
        with patch("xauby.track_record.validator.audit_track_record",
                   return_value=(False, ["boom"], {})) as audit:
            _scheduler(send_telegram=False).check_and_run(engine, AT_ELEVEN)
        audit.assert_called_once()
        engine.send_telegram_alert.assert_not_called()

    def test_an_audit_that_raises_does_not_take_the_engine_down(self):
        engine = _engine()
        with patch("xauby.track_record.validator.audit_track_record",
                   side_effect=RuntimeError("db locked")):
            _scheduler().check_and_run(engine, AT_ELEVEN)  # must not raise


class TestShippedConfig(unittest.TestCase):
    def test_the_self_audit_is_armed_in_bot_config(self):
        import yaml

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "bot_config.yaml"), encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        audit = cfg.get("self_audit") or {}
        self.assertTrue(audit.get("enabled"),
                        "the auditor is wired but switched off, which is the "
                        "state P1.6 existed to end")


if __name__ == "__main__":
    unittest.main()
