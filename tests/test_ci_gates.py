"""CI is the only gate between an agent's commit and real money. Roadmap P2.3.

`AGENTS.md` lets an agent merge its own PR once CI is green, and `main` is what
the live trading engine pulls and restarts on. That makes the CI configuration
itself load-bearing, so it gets tests — the same reason P1.6 tested that the
self-audit is actually wired rather than only that it computes correctly.

`ruff` was configured in `pyproject.toml` and never invoked by anything.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "secret-scan.yml"
SECURITY = ROOT / ".github" / "workflows" / "security.yml"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"
GITIGNORE = ROOT / ".gitignore"

#: Rules the blocking gate must keep. Each catches a defect, not a style
#: preference, and dropping one silently widens what can reach `main`.
REQUIRED_GATE_RULES = (
    "E9",     # syntax and IO errors
    "F811",   # a redefinition that silently discards the earlier one
    "F821",   # undefined name — a NameError at runtime
    "F822",   # undefined name in __all__
    "F823",   # local used before assignment
    "F632",   # `is` against a literal, always False
    "B006",   # mutable default argument
    "B012",   # break/return inside finally, swallowing exceptions
    "B023",   # closure over a loop variable
)


def _workflow(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(workflow, job):
    return workflow["jobs"][job]["steps"]


class TestLintGate(unittest.TestCase):
    def setUp(self):
        self.ci = _workflow(CI)

    def test_ci_runs_ruff_at_all(self):
        # It was configured in pyproject and called by nothing.
        self.assertIn("lint", self.ci["jobs"], "no lint job in CI")
        commands = " ".join(str(s.get("run", "")) for s in _steps(self.ci, "lint"))
        self.assertIn("ruff check", commands)

    def test_the_gate_step_is_blocking(self):
        blocking = [
            s for s in _steps(self.ci, "lint")
            if "ruff check" in str(s.get("run", "")) and not s.get("continue-on-error")
        ]
        self.assertTrue(blocking, "every ruff step is continue-on-error, so nothing gates")

    def test_the_gate_keeps_the_defect_rules(self):
        blocking = " ".join(
            str(s.get("run", "")) for s in _steps(self.ci, "lint")
            if "ruff check" in str(s.get("run", "")) and not s.get("continue-on-error")
        )
        for rule in REQUIRED_GATE_RULES:
            with self.subTest(rule=rule):
                self.assertIn(rule, blocking)

    def test_the_full_set_is_still_reported(self):
        # The ~3,800 style findings stay visible. A gate that hides its own
        # backlog is how the backlog stops being anyone's problem.
        reporting = [
            s for s in _steps(self.ci, "lint")
            if "ruff check" in str(s.get("run", "")) and s.get("continue-on-error")
        ]
        self.assertTrue(reporting, "the full ruff set is not reported anywhere")

    def test_pyproject_select_was_not_weakened_to_make_ci_pass(self):
        # The narrower gate lives in the workflow. Trimming pyproject instead
        # would lose the target as well as the check.
        import tomllib

        with open(ROOT / "pyproject.toml", "rb") as fh:
            cfg = tomllib.load(fh)
        select = cfg["tool"]["ruff"]["lint"]["select"]
        for family in ("E", "F", "I", "UP", "B"):
            self.assertIn(family, select)


class TestDependencyAudit(unittest.TestCase):
    def setUp(self):
        self.security = _workflow(SECURITY)

    def test_python_and_node_are_both_audited(self):
        self.assertIn("python-dependencies", self.security["jobs"])
        self.assertIn("node-dependencies", self.security["jobs"])
        python = " ".join(str(s.get("run", ""))
                          for s in _steps(self.security, "python-dependencies"))
        node = " ".join(str(s.get("run", ""))
                        for s in _steps(self.security, "node-dependencies"))
        self.assertIn("pip-audit", python)
        self.assertIn("npm audit", node)

    def test_it_runs_on_a_schedule_not_only_on_push(self):
        # A CVE disclosed against something already shipped does not arrive with
        # a push, so a push-only audit would never see it.
        triggers = self.security.get("on") or self.security.get(True)
        self.assertIn("schedule", triggers, "no scheduled run")
        self.assertTrue(triggers["schedule"][0]["cron"])

    def test_each_audit_has_a_blocking_step(self):
        for job in ("python-dependencies", "node-dependencies"):
            with self.subTest(job=job):
                blocking = [
                    s for s in _steps(self.security, job)
                    if ("audit" in str(s.get("run", "")))
                    and not s.get("continue-on-error")
                ]
                self.assertTrue(
                    blocking,
                    "the whole job is continue-on-error, which reports without gating")

    def test_known_exceptions_are_named_rather_than_blanket_suppressed(self):
        text = SECURITY.read_text(encoding="utf-8")
        # The one pip advisory we cannot act on is ignored by id, with the
        # reason recorded next to it — not by dropping the whole check.
        self.assertIn("--ignore-vuln PYSEC-2026-3447", text)
        self.assertIn("pandas-ta", text, "the reason for the ignore is not recorded")
        # And the npm findings are described rather than silently downgraded.
        self.assertIn("sharp", text)
        self.assertIn("audit-level=critical", text)


class TestDependabot(unittest.TestCase):
    def test_it_covers_both_ecosystems_and_the_actions_themselves(self):
        cfg = yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))
        ecosystems = {u["package-ecosystem"] for u in cfg["updates"]}
        # github-actions included deliberately: a compromised action runs with
        # whatever the workflow grants it, on the last gate before live money.
        self.assertEqual(ecosystems, {"pip", "npm", "github-actions"})

    def test_the_npm_entry_points_at_the_frontend(self):
        cfg = yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))
        npm = next(u for u in cfg["updates"] if u["package-ecosystem"] == "npm")
        self.assertEqual(npm["directory"], "/Website")


class TestRepositoryHygiene(unittest.TestCase):
    def test_core_runtime_directory_is_blanket_ignored(self):
        rules = {
            line.strip()
            for line in GITIGNORE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertIn("core/", rules)

    def test_core_runtime_directory_has_no_tracked_files(self):
        result = subprocess.run(
            ["git", "ls-files", "--", "core"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        self.assertEqual(
            result.stdout.strip(),
            "",
            f"runtime files must not be tracked:\n{result.stdout}",
        )


if __name__ == "__main__":
    unittest.main()
