from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


class DeployFromGitHubScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.origin = base / "origin.git"
        self.repo = base / "repo"
        self.upstream = base / "upstream"
        source = Path(__file__).resolve().parents[1] / "scripts" / "deploy_from_github.sh"

        self._run(["git", "init", "--bare", str(self.origin)], cwd=base)
        self._run(["git", "init", "-b", "main", str(self.upstream)], cwd=base)
        for repo in (self.upstream,):
            self._run(["git", "config", "user.email", "tests@example.com"], cwd=repo)
            self._run(["git", "config", "user.name", "Tests"], cwd=repo)
        (self.upstream / "scripts").mkdir()
        shutil.copy2(source, self.upstream / "scripts" / "deploy_from_github.sh")
        (self.upstream / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
        self._run(["git", "add", "."], cwd=self.upstream)
        self._run(["git", "commit", "-m", "initial"], cwd=self.upstream)
        self._run(["git", "remote", "add", "origin", str(self.origin)], cwd=self.upstream)
        self._run(["git", "push", "-u", "origin", "main"], cwd=self.upstream)
        self._run(["git", "clone", "--branch", "main", str(self.origin), str(self.repo)], cwd=base)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _run(args, *, cwd, env=None):
        return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, check=True)

    def _upstream_commit(self, name: str, content: str):
        path = self.upstream / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._run(["git", "add", name], cwd=self.upstream)
        self._run(["git", "commit", "-m", f"update {name}"], cwd=self.upstream)
        self._run(["git", "push", "origin", "main"], cwd=self.upstream)

    def _deploy(self, *, token=""):
        env = os.environ.copy()
        env.update({"GITHUB_REMOTE_URL": str(self.origin), "GITHUB_TOKEN": token})
        return subprocess.run(
            [str(self.repo / "scripts" / "deploy_from_github.sh")],
            cwd=self.repo,
            env=env,
            text=True,
            capture_output=True,
        )

    def test_fetch_keeps_persistent_remote_credential_free(self):
        self._upstream_commit("module.py", "VALUE = 2\n")

        result = self._deploy(token="fixture-token")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        remote = self._run(["git", "remote", "get-url", "origin"], cwd=self.repo).stdout.strip()
        self.assertEqual(remote, str(self.origin))
        self.assertNotIn("fixture-token", remote)

    def test_restrictive_caller_umask_still_produces_service_readable_files(self):
        self._upstream_commit("xauby/new_module.py", "VALUE = 3\n")
        env = os.environ.copy()
        env.update({"GITHUB_REMOTE_URL": str(self.origin), "GITHUB_TOKEN": ""})

        result = subprocess.run(
            ["bash", "-c", f"umask 0077; {self.repo / 'scripts' / 'deploy_from_github.sh'}"],
            cwd=self.repo,
            env=env,
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        mode = stat.S_IMODE((self.repo / "xauby" / "new_module.py").stat().st_mode)
        self.assertEqual(mode & 0o004, 0o004)
        parent_mode = stat.S_IMODE((self.repo / "xauby").stat().st_mode)
        self.assertEqual(parent_mode & 0o001, 0o001)


if __name__ == "__main__":
    unittest.main()
