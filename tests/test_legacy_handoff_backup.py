from __future__ import annotations

import json
import sqlite3
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.legacy_handoff_backup import create_backup, verify_backup


class LegacyHandoffBackupTests(unittest.TestCase):
    def test_backup_is_consistent_and_excludes_plaintext_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            project = root / "project"
            runtime = root / "core"
            project.mkdir()
            (runtime / "logs").mkdir(parents=True)
            (project / "bot_config.yaml").write_text("simulate_only: false\n")
            (project / "coin_whitelist.json").write_text('{"assets": []}\n')
            secret = "test-secret-placeholder"
            (project / ".env").write_text(
                f"OKX_API_KEY={secret}\nOKX_API_SECRET={secret}\nOKX_API_PASSPHRASE={secret}\n"
            )
            with sqlite3.connect(runtime / "xauby.db") as conn:
                conn.execute("CREATE TABLE closed_trades (id INTEGER PRIMARY KEY)")
                conn.execute("INSERT INTO closed_trades DEFAULT VALUES")
            (runtime / "logs" / "xauby_bot_state.json").write_text(json.dumps({
                "symbol": "XAUUSDT",
                "position": {"state": "bought", "position_side": "SHORT", "quantity": 0.1},
            }))

            archive = create_backup(project, runtime, root / "backups", "owner")
            manifest = verify_backup(archive)

            self.assertEqual(manifest["database_integrity"], "ok")
            self.assertTrue(manifest["position"]["open"])
            self.assertTrue(all(manifest["credential_presence"].values()))
            with tarfile.open(archive, "r:gz") as bundle:
                self.assertNotIn(secret.encode(), b"".join(
                    handle.read() for member in bundle.getmembers()
                    if member.isfile() and (handle := bundle.extractfile(member)) is not None
                ))


if __name__ == "__main__":
    unittest.main()
