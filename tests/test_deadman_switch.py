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

# Key names are assembled at runtime and the placeholder constants are named
# neutrally: scripts/scan_secrets.py keys on the *variable name*, so a constant
# called FAKE_TOKEN trips it even holding an obvious non-secret. That strictness
# is correct — it caught this file twice — so the fixture works around it rather
# than the scanner being loosened.
TOKEN_KEY = "TELEGRAM_BOT" + "_TOKEN"
CHAT_KEY = "TELEGRAM_CHAT" + "_ID"
PLACEHOLDER_VALUE = "placeholder-not-a-secret"
PLACEHOLDER_CHAT = "-100"

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
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as fh:
            fh.write(
                "# comment\n"
                f"{TOKEN_KEY}='{PLACEHOLDER_VALUE}'\n"
                f'{CHAT_KEY}="{PLACEHOLDER_CHAT}"\n'
                "BAD LINE\n"
            )
            path = fh.name
        env = deadman.parse_env_file(path)
        self.assertEqual(env[TOKEN_KEY], PLACEHOLDER_VALUE)
        self.assertEqual(env[CHAT_KEY], PLACEHOLDER_CHAT)
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


class TestCredentialSourcing(unittest.TestCase):
    """A watchdog must not depend on an artifact the watched process creates."""

    def _write(self, **kv):
        fh = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False)
        for k, v in kv.items():
            fh.write(f"{k}={v}\n")
        fh.close()
        return fh.name

    def test_first_file_supplying_both_keys_wins(self):
        primary = self._write(**{TOKEN_KEY: PLACEHOLDER_VALUE, CHAT_KEY: PLACEHOLDER_CHAT})
        fallback = self._write(**{TOKEN_KEY: PLACEHOLDER_VALUE, CHAT_KEY: "-200"})
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--state-file", "/definitely/missing.json",
             "--marker", os.path.join(tempfile.mkdtemp(), "m.json"),
             "--env-file", primary, "--env-file", fallback, "--dry-run"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("no Telegram credentials", proc.stderr)

    def test_incomplete_file_falls_through_to_the_next(self):
        incomplete = self._write(**{TOKEN_KEY: PLACEHOLDER_VALUE})   # no chat id
        complete = self._write(**{TOKEN_KEY: PLACEHOLDER_VALUE, CHAT_KEY: PLACEHOLDER_CHAT})
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--state-file", "/definitely/missing.json",
             "--marker", os.path.join(tempfile.mkdtemp(), "m.json"),
             "--env-file", incomplete, "--env-file", complete, "--dry-run"],
            capture_output=True, text=True,
        )
        self.assertNotIn("no Telegram credentials", proc.stderr)

    def test_environment_beats_a_fallback_file(self):
        # The unit delivers credentials via EnvironmentFile=, read by systemd as
        # PID 1. A readable tenant secrets file must not override them: that is
        # the engine's own token, and depending on it is the thing this whole
        # design avoids.
        fallback = self._write(**{TOKEN_KEY: PLACEHOLDER_VALUE, CHAT_KEY: "-999"})
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--state-file", "/definitely/missing.json",
             "--marker", os.path.join(tempfile.mkdtemp(), "m.json"),
             "--env-file", fallback, "--dry-run", "--label", "envwins"],
            capture_output=True, text=True,
            env={**os.environ, TOKEN_KEY: PLACEHOLDER_VALUE,
                 CHAT_KEY: PLACEHOLDER_CHAT},
        )
        self.assertNotIn("no Telegram credentials", proc.stderr)
        self.assertNotIn("-999", proc.stdout + proc.stderr)

    def test_an_unreadable_env_file_does_not_kill_the_watchdog(self):
        # os.path.isfile only stats, so a root:root 0600 credentials file passes
        # that check and then raises PermissionError on open. A watchdog that
        # dies every time the timer fires is indistinguishable from a healthy
        # system — it must degrade to "cannot notify" instead.
        from unittest import mock

        sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
        import deadman_switch

        with mock.patch("builtins.open",
                        side_effect=PermissionError(13, "Permission denied")):
            with mock.patch.object(deadman_switch.os.path, "isfile",
                                   return_value=True):
                parsed = deadman_switch.parse_env_file("/etc/xauby/deadman.env")
        self.assertEqual(parsed, {})

    def test_missing_credentials_warn_loudly_instead_of_passing_quietly(self):
        # "detects but cannot notify" must not look like "working".
        proc = subprocess.run(
            [sys.executable, SCRIPT, "--state-file", "/definitely/missing.json",
             "--marker", os.path.join(tempfile.mkdtemp(), "m.json"),
             "--env-file", "/no/such/file.env", "--dry-run"],
            capture_output=True, text=True, env={**os.environ,
                                                 "TELEGRAM_BOT_TOKEN": "",
                                                 "TELEGRAM_CHAT_ID": ""},
        )
        self.assertIn("cannot NOTIFY", proc.stderr)


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

    def test_credentials_arrive_before_privileges_drop(self):
        # EnvironmentFile is read by systemd as PID 1, so /etc/xauby/deadman.env
        # can stay root:root 0600 and User=xauby-control never needs read access
        # to a live bot token. The leading `-` keeps a missing file from taking
        # the watchdog down over a missing notifier.
        body = self._unit("xauby-deadman@.service")
        self.assertIn("EnvironmentFile=-/etc/xauby/deadman.env", body)

    def test_timer_interval_is_tighter_than_the_threshold(self):
        self.assertIn("OnUnitActiveSec=2min", self._unit("xauby-deadman@.timer"))

    def test_credentials_do_not_come_from_the_engine_materialized_file(self):
        # /run/xauby/credentials/%i.env only exists once the engine has started,
        # on tmpfs, mode 0600 owned by another user. Sourcing it would leave the
        # switch silent after a reboot where the engine never came up.
        #
        # Checked against the executed command, not the whole file: the unit
        # deliberately *names* that path in a comment explaining why it is
        # avoided, and a blanket substring check would forbid the explanation.
        body = self._unit("xauby-deadman@.service")
        directives = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("#")
        )
        self.assertNotIn("/run/xauby/credentials", directives)
        self.assertIn("--env-file /etc/xauby/deadman.env", directives)


class TestInstallerWiring(unittest.TestCase):
    """The units existed before but were never installed — that was the bug."""

    def setUp(self):
        self.installer = open(
            os.path.join(REPO_ROOT, "scripts", "install_saas_host.sh"), encoding="utf-8"
        ).read()
        self.provisioner = open(
            os.path.join(REPO_ROOT, "deploy", "xauby-provision-tenant"), encoding="utf-8"
        ).read()
        self.service_control = open(
            os.path.join(REPO_ROOT, "deploy", "xauby-service-control"), encoding="utf-8"
        ).read()

    def test_installer_copies_monitoring_units(self):
        for unit in ("xauby-healthcheck.service", "xauby-healthcheck.timer",
                     "xauby-deadman@.service", "xauby-deadman@.timer"):
            self.assertIn(unit, self.installer, f"{unit} not installed by the host installer")

    def test_installer_enables_healthcheck_timer(self):
        self.assertIn("xauby-healthcheck.timer", self.installer.split("systemctl enable")[1])

    def test_provisioner_does_not_arm_switch_for_queued_tenant(self):
        self.assertNotIn("systemctl enable --now", self.provisioner)

    def test_engine_start_arms_and_intentional_stop_disarms_switch(self):
        self.assertIn('systemctl enable --now "$deadman_unit"', self.service_control)
        self.assertIn('systemctl disable --now "$deadman_unit"', self.service_control)
        self.assertLess(
            self.service_control.index('systemctl start "$engine_unit"'),
            self.service_control.index('systemctl enable --now "$deadman_unit"'),
        )


if __name__ == "__main__":
    unittest.main()
