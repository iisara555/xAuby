import copy
import os
import tempfile
import unittest

import yaml

from scripts.audit_release_readiness import audit_static


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestReleaseReadiness(unittest.TestCase):
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
