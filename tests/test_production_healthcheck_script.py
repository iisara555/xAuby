from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


class ProductionHealthcheckScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.log = root / "curl.log"
        self.curl = root / "curl"
        self.curl.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
echo "$*" >> "$FAKE_CURL_LOG"
if [[ "${FAKE_BAD_ORIGIN:-}" != "" && "$*" == *"${FAKE_BAD_ORIGIN}/healthz"* ]]; then
  echo '{"ok":false}'
else
  echo '{"ok":true}'
fi
""",
            encoding="utf-8",
        )
        self.curl.chmod(self.curl.stat().st_mode | stat.S_IXUSR)
        self.script = Path(__file__).resolve().parents[1] / "scripts" / "check_production_health.sh"

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, bad_origin=""):
        env = os.environ.copy()
        env.update(
            {
                "CURL_BIN": str(self.curl),
                "FAKE_CURL_LOG": str(self.log),
                "FAKE_BAD_ORIGIN": bad_origin,
                "XAUBY_PUBLIC_URL": "https://frontend.example",
                "XAUBY_API_ORIGIN": "https://api.example",
            }
        )
        return subprocess.run([str(self.script)], env=env, text=True, capture_output=True)

    def test_checks_both_authoritative_tls_origins(self):
        result = self._run()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.log.read_text(encoding="utf-8")
        self.assertIn("https://frontend.example/healthz", calls)
        self.assertIn("https://api.example/healthz", calls)

    def test_fails_when_control_plane_is_not_healthy(self):
        result = self._run("https://api.example")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("control-plane health did not return ok=true", result.stderr)


if __name__ == "__main__":
    unittest.main()
