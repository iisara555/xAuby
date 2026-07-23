from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ID = "prj_m653cHTPdEVORx8WNAYsRRPvKmKZ"
ORG_ID = "team_bVwJhoMi3IPSnRxtu3fageUS"


class DeployVercelScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "repo"
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "Website" / ".vercel").mkdir(parents=True)
        source = Path(__file__).resolve().parents[1] / "scripts" / "deploy_vercel.sh"
        self.script = self.root / "scripts" / "deploy_vercel.sh"
        shutil.copy2(source, self.script)
        self.script.chmod(self.script.stat().st_mode | stat.S_IXUSR)
        (self.root / "Website" / "package.json").write_text("{}\n", encoding="utf-8")
        (self.root / "Website" / "package-lock.json").write_text("{}\n", encoding="utf-8")
        (self.root / "Website" / ".vercel" / "project.json").write_text(
            json.dumps({"projectId": PROJECT_ID, "orgId": ORG_ID}),
            encoding="utf-8",
        )

        self.origin = base / "origin.git"
        self._run(["git", "init", "--bare", str(self.origin)], cwd=base)
        self._run(["git", "init", "-b", "main"], cwd=self.root)
        self._run(["git", "config", "user.email", "tests@example.com"], cwd=self.root)
        self._run(["git", "config", "user.name", "Tests"], cwd=self.root)
        self._run(["git", "add", "."], cwd=self.root)
        self._run(["git", "commit", "-m", "fixture"], cwd=self.root)
        self._run(["git", "remote", "add", "origin", str(self.origin)], cwd=self.root)
        self._run(["git", "push", "-u", "origin", "main"], cwd=self.root)

        self.log = base / "commands.log"
        self.fake_vercel = base / "vercel"
        self.fake_vercel.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
echo "$*" >> "$FAKE_COMMAND_LOG"
case "${1:-}" in
  --version) echo "Vercel CLI 56.2.1" ;;
  whoami) echo "test-user" ;;
  build) [[ "${FAKE_BUILD_FAIL:-0}" != "1" ]] ;;
  deploy) echo "https://fixture-deployment.vercel.app" ;;
  inspect) echo '{"id":"dpl_previous","readyState":"READY"}' ;;
  link|pull|promote) ;;
  *) ;;
esac
""",
            encoding="utf-8",
        )
        self.fake_vercel.chmod(0o755)
        self.fake_curl = base / "curl"
        self.fake_curl.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
echo "curl $*" >> "$FAKE_COMMAND_LOG"
if [[ "${FAKE_SMOKE_FAIL:-0}" == "1" && "$*" == *"fixture-deployment.vercel.app/healthz"* ]]; then
  exit 22
fi
echo '{"ok":true}'
""",
            encoding="utf-8",
        )
        self.fake_curl.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _run(args, *, cwd, env=None):
        return subprocess.run(
            args,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

    def _deploy(self, *, build_fail=False, smoke_fail=False, cwd="/"):
        env = os.environ.copy()
        env.update(
            {
                "VERCEL_CLI_BIN": str(self.fake_vercel),
                "CURL_BIN": str(self.fake_curl),
                "FAKE_COMMAND_LOG": str(self.log),
                "FAKE_BUILD_FAIL": "1" if build_fail else "0",
                "FAKE_SMOKE_FAIL": "1" if smoke_fail else "0",
            }
        )
        return subprocess.run(
            [str(self.script)],
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
        )

    def test_runs_from_any_working_directory_and_promotes_once(self):
        result = self._deploy(cwd="/")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        commands = self.log.read_text(encoding="utf-8")
        self.assertEqual(commands.count("promote https://fixture-deployment.vercel.app"), 1)
        self.assertIn("deploy --prebuilt --prod --skip-domain", commands)
        self.assertIn("Commit:", result.stdout)

    def test_dirty_website_is_rejected_before_vercel_runs(self):
        (self.root / "Website" / "page.tsx").write_text("dirty\n", encoding="utf-8")

        result = self._deploy()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("uncommitted changes", result.stderr)
        commands = self.log.read_text(encoding="utf-8")
        self.assertNotIn("whoami", commands)
        self.assertNotIn("build --prod", commands)
        self.assertNotIn("deploy --prebuilt", commands)
        self.assertNotIn("promote ", commands)

    def test_build_failure_never_deploys_or_promotes(self):
        result = self._deploy(build_fail=True)

        self.assertNotEqual(result.returncode, 0)
        commands = self.log.read_text(encoding="utf-8")
        self.assertIn("build --prod", commands)
        self.assertNotIn("deploy --prebuilt", commands)
        self.assertNotIn("promote ", commands)

    def test_smoke_failure_never_promotes(self):
        result = self._deploy(smoke_fail=True)

        self.assertNotEqual(result.returncode, 0)
        commands = self.log.read_text(encoding="utf-8")
        self.assertIn("deploy --prebuilt", commands)
        self.assertNotIn("promote ", commands)

    def test_wrong_project_link_is_repaired_before_build(self):
        (self.root / "Website" / ".vercel" / "project.json").write_text(
            json.dumps({"projectId": "wrong", "orgId": "wrong"}),
            encoding="utf-8",
        )
        self._run(["git", "add", "."], cwd=self.root)
        self._run(["git", "commit", "-m", "wrong link fixture"], cwd=self.root)
        self._run(["git", "push", "origin", "main"], cwd=self.root)

        result = self._deploy()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        commands = self.log.read_text(encoding="utf-8")
        self.assertIn("link --yes", commands)
        self.assertLess(commands.index("link --yes"), commands.index("build --prod"))


if __name__ == "__main__":
    unittest.main()
