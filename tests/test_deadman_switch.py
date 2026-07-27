"""Dead-man's switch (roadmap P0.6).

Every alert path the bot has is sent BY the engine, so the failure that matters
most — the engine stopping — produces no notification. This checker runs
out-of-process on a timer and alerts on silence.

The tests cover the behaviours that make it trustworthy: it fires on silence,
it does not spam, it reports recovery, a missing state file counts as silent,
and it never imports the package it is monitoring.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "deadman_switch.py")

sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
import importlib.util

_spec = importlib.util.spec_from_file_location("deadman_switch", SCRIPT)
deadman = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(deadman)


class TestNoPackageDependency(unittest.TestCase):
    """It must survive the deploy breakage it exists to detect."""

    def test_does_not_import_xauby(self):
        tree = ast.parse(open(SCRIPT, encoding="utf-8").read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn(
            "xauby", imported,
            "a checker that imports the package dies with the package: a broken "
            "deploy would make it raise, and silence would look like health",
        )

    def test_runs_with_package_unimportable(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = tempfile.mkdtemp()  # xauby not importable
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--state-file", "/definitely/missing.json",
             "--marker", os.path.join(tempfile.mkdtemp(), "m.json"), "--dry-run"],
            capture_output=True, text=True, env=env, cwd=tempfile.mkdtemp(),
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("DEAD-MAN", proc.stdout + proc.stderr)


class _Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.state = os.path.join(self.dir, "xauby_bot_state.json")
        self.marker = os.path.join(self.dir, "marker.json")
        with open(self.state, "w") as fh:
            json.dump({"pid": os.getpid(), "symbol": "XAUUSDT",
                       "simulate_only": False, "run_id": "r1"}, fh)

    def run_check(self, *, max_age=300.0, realert=3600.0):
        return subprocess.run(
            [sys.executable, SCRIPT, "--state-file", self.state,
             "--marker", self.marker, "--max-age-sec", str(max_age),
             "--realert-sec", str(realert), "--dry-run"],
            capture_output=True, text=True,
        )

    def age_state(self, seconds):
        past = time.time() - seconds
        os.utime(self.state, (past, past))


class TestSilenceDetection(_Base):
    def test_fresh_state_is_healthy(self):
        proc = self.run_check()
        self.assertEqual(proc.returncode, 0)
        self.assertIn("[OK]", proc.stdout)

    def test_stale_state_alerts(self):
        self.age_state(7200)
        proc = self.run_check()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("DEAD-MAN", proc.stdout)
        self.assertIn("silent", proc.stderr)

    def test_missing_state_file_counts_as_silent(self):
        # A wrong path after a deploy, or an engine that never started, is not
        # "healthy because there is nothing to check".
        os.remove(self.state)
        proc = self.run_check()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("No engine state file", proc.stdout)

    def test_threshold_is_respected(self):
        self.age_state(100)
        self.assertEqual(self.run_check(max_age=300).returncode, 0)
        self.assertEqual(self.run_check(max_age=60).returncode, 1)

    def test_unreadable_state_still_uses_mtime(self):
        # A truncated write is itself a symptom, but freshness still decides.
        with open(self.state, "w") as fh:
            fh.write("{not json")
        proc = self.run_check()
        self.assertEqual(proc.returncode, 0, "recent but corrupt is not silence")
        self.age_state(7200)
        self.assertEqual(self.run_check().returncode, 1)


class TestDebounceAndRecovery(_Base):
    def test_repeat_alerts_are_suppressed(self):
        self.age_state(7200)
        first = self.run_check()
        self.assertIn("DEAD-MAN", first.stdout)
        second = self.run_check()
        self.assertEqual(second.returncode, 1)
        self.assertIn("suppressed", second.stderr)
        self.assertNotIn("DEAD-MAN", second.stdout)

    def test_realert_after_the_window(self):
        self.age_state(7200)
        self.run_check(realert=3600)
        again = self.run_check(realert=0)  # window elapsed
        self.assertIn("DEAD-MAN", again.stdout)

    def test_recovery_is_reported_once(self):
        self.age_state(7200)
        self.run_check()
        os.utime(self.state, None)  # engine ticks again
        recovered = self.run_check()
        self.assertEqual(recovered.returncode, 0)
        self.assertIn("recovered", recovered.stdout)
        quiet = self.run_check()
        self.assertEqual(quiet.returncode, 0)
        self.assertNotIn("recovered", quiet.stdout)


class TestHelpers(unittest.TestCase):
    def test_env_file_is_parsed_as_data_not_shell(self):
        # Key names are assembled at runtime so this fixture does not read as a
        # literal credential assignment to scripts/scan_secrets.py, which flags
        # (correctly) any `TELEGRAM_BOT_TOKEN=...` line in a tracked file.
        token_key = "TELEGRAM_BOT" + "_TOKEN"
        chat_key = "TELEGRAM_CHAT" + "_ID"
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as fh:
            fh.write(
                "# comment\n"
                f"{token_key}='placeholder-not-a-secret'\n"
                f'{chat_key}="-100"\n'
                "BAD LINE\n"
            )
            path = fh.name
        env = deadman.parse_env_file(path)
        self.assertEqual(env[token_key], "placeholder-not-a-secret")
        self.assertEqual(env[chat_key], "-100")
        self.assertNotIn("BAD LINE", env)

    def test_missing_env_file_is_not_fatal(self):
        self.assertEqual(deadman.parse_env_file("/no/such.env"), {})

    def test_pid_liveness(self):
        self.assertTrue(deadman.pid_alive(os.getpid()))
        self.assertIsNone(deadman.pid_alive(None))
        self.assertIsNone(deadman.pid_alive(0))

    def test_send_without_credentials_reports_rather_than_raises(self):
        ok, detail = deadman.send_telegram("", "", "hi")
        self.assertFalse(ok)
        self.assertIn("not set", detail)

    def test_age_formatting(self):
        self.assertEqual(deadman.fmt_age(45), "45s")
        self.assertEqual(deadman.fmt_age(600), "10m")
        self.assertEqual(deadman.fmt_age(7200), "2h00m")


class TestSystemdUnits(unittest.TestCase):
    def _unit(self, name):
        path = os.path.join(REPO_ROOT, "deploy", "systemd", name)
        self.assertTrue(os.path.isfile(path), f"{name} missing")
        return open(path, encoding="utf-8").read()

    def test_units_exist(self):
        self._unit("xauby-deadman@.service")
        self._unit("xauby-deadman@.timer")

    def test_service_is_not_coupled_to_the_engine(self):
        # Ordering or binding it to the engine would stop the watchdog exactly
        # when the engine is the thing that failed.
        body = self._unit("xauby-deadman@.service")
        for directive in ("After=xauby-engine", "BindsTo=", "Requires=xauby-engine",
                          "PartOf="):
            self.assertNotIn(directive, body)

    def test_exit_one_is_not_treated_as_unit_failure_loop(self):
        self.assertIn("SuccessExitStatus=1", self._unit("xauby-deadman@.service"))

    def test_timer_interval_is_tighter_than_the_threshold(self):
        self.assertIn("OnUnitActiveSec=2min", self._unit("xauby-deadman@.timer"))


class TestInstallerWiring(unittest.TestCase):
    """The units existed before but were never installed — that was the bug."""

    def setUp(self):
        self.installer = open(
            os.path.join(REPO_ROOT, "scripts", "install_saas_host.sh"), encoding="utf-8"
        ).read()
        self.provisioner = open(
            os.path.join(REPO_ROOT, "deploy", "xauby-provision-tenant"), encoding="utf-8"
        ).read()

    def test_installer_copies_monitoring_units(self):
        for unit in ("xauby-healthcheck.service", "xauby-healthcheck.timer",
                     "xauby-deadman@.service", "xauby-deadman@.timer"):
            self.assertIn(unit, self.installer, f"{unit} not installed by the host installer")

    def test_installer_enables_healthcheck_timer(self):
        self.assertIn("xauby-healthcheck.timer", self.installer.split("systemctl enable")[1])

    def test_provisioner_arms_the_switch_per_tenant(self):
        self.assertIn("xauby-deadman@${tenant}.timer", self.provisioner)

    def test_switch_is_not_controllable_by_the_control_plane(self):
        # A monitor the monitored system can switch off is not a monitor.
        wrapper = open(
            os.path.join(REPO_ROOT, "deploy", "xauby-service-control"), encoding="utf-8"
        ).read()
        self.assertNotIn("deadman", wrapper)


if __name__ == "__main__":
    unittest.main()
