from __future__ import annotations

import base64
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.saas_backup import (
    _rclone_destination,
    create_backup,
    upload_offsite_backup,
    verify_backup,
)
from scripts.saas_restore import restore_drill
from scripts.rotate_credential_master_key import main as rotate_master_key
from xauby.saas.backup_crypto import BackupCipher
from xauby.saas.credentials import CredentialKeyring
from xauby.saas.key_rotation import rotate_credential_keyring, update_environment
from xauby.saas.settings import SaaSSettings
from xauby.saas.store import ControlPlaneStore


def _key(byte: bytes) -> str:
    return base64.b64encode(byte * 32).decode("ascii")


class OffsiteBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.master_key = _key(b"m")
        self.backup_key = _key(b"b")
        self.settings = SaaSSettings(
            project_root=Path(__file__).resolve().parents[1],
            data_root=root / "data",
            tenant_config_root=root / "config",
            tenant_runtime_root=root / "runtime",
            database_path=root / "control.db",
            public_base_url="https://example.invalid",
            session_secret="",
            credential_master_key=self.master_key,
            backup_encryption_key=self.backup_key,
            backup_rclone_destination="backup-crypt:xauby/prod",
            backup_rclone_config=root / "rclone.conf",
            backup_gpg_recipient="0123456789ABCDEF",
            backup_gpg_homedir=root / "gpg",
            dev_login_enabled=True,
        )
        self.settings.backup_rclone_config.write_text("[backup-crypt]\ntype = local\n", encoding="utf-8")
        self.store = ControlPlaneStore(self.settings.database_path)
        self.store.migrate()
        self.owner, tenant = self.store.bootstrap_owner("owner@example.com", "owner")
        self.tenant = tenant

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _archive(self) -> Path:
        return create_backup(
            self.settings, self.settings.data_root / "backups", kind="daily",
            release_id="test", retention_days=30, include_secrets=False, host_config=[],
        )

    def test_encrypted_archive_verifies_and_restore_drill_decrypts_credentials(self) -> None:
        ring = CredentialKeyring(self.master_key)
        exchange = ring.encrypt(self.tenant["id"], "okx-swap", {
            "api_key": "key", "api_secret": "secret", "passphrase": "pass",
        })
        telegram = ring.encrypt(self.tenant["id"], "telegram", {"bot_token": "bot-token"})
        self.store.set_exchange_connection(
            self.tenant["id"], "okx", "1234", target_id="okx-swap", credential_blob=exchange,
        )
        self.store.set_telegram_connection(
            self.tenant["id"], "-100", "9876", credential_blob=telegram,
        )
        archive = self._archive()
        encrypted = archive.with_suffix(archive.suffix + ".enc")
        BackupCipher(self.backup_key).encrypt_file(archive, encrypted)
        manifest = verify_backup(encrypted, encryption_key=self.backup_key)
        self.assertEqual(manifest["format"], 2)
        with self.assertRaises(ValueError):
            verify_backup(encrypted)
        result = restore_drill(encrypted, self.settings)
        self.assertEqual(result["exchange_credentials"], 1)
        self.assertEqual(result["telegram_credentials"], 1)

    def test_backup_cipher_rejects_tampered_archive(self) -> None:
        source = self.settings.data_root / "plain.bin"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"backup payload" * 100)
        encrypted = self.settings.data_root / "plain.bin.enc"
        BackupCipher(self.backup_key).encrypt_file(source, encrypted)
        body = bytearray(encrypted.read_bytes())
        body[-1] ^= 0x01
        encrypted.write_bytes(body)
        with self.assertRaises(ValueError):
            BackupCipher(self.backup_key).decrypt_file(encrypted, self.settings.data_root / "restored.bin")
        self.assertFalse((self.settings.data_root / "restored.bin").exists())

    def test_offsite_upload_sends_only_encrypted_archive_and_gpg_recovery_bundle(self) -> None:
        archive = self._archive()
        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            calls.append(list(command))
            if command[0] == "gpg":
                output = Path(command[command.index("--output") + 1])
                output.write_text("-----BEGIN PGP MESSAGE-----\nfixture\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, b"", b"")

        with patch("scripts.saas_backup.shutil.which", return_value="/usr/bin/tool"), patch(
            "scripts.saas_backup.subprocess.run", side_effect=fake_run
        ):
            result = upload_offsite_backup(archive, self.settings)
        self.assertTrue(result["archive"].endswith(".tar.gz.enc"))
        self.assertTrue(result["recovery_bundle"].endswith(".tar.gz.asc"))
        copy_sources = [call[-2] for call in calls if call[:4] == ["rclone", "--config", str(self.settings.backup_rclone_config), "copyto"]]
        self.assertEqual(len(copy_sources), 2)
        self.assertTrue(any(source.endswith(".enc") for source in copy_sources))
        self.assertTrue(any(source.endswith(".asc") for source in copy_sources))
        self.assertFalse(any(source == str(archive) for source in copy_sources))

    def test_rclone_destination_rejects_local_or_parent_paths(self) -> None:
        self.assertEqual(_rclone_destination("remote:folder", "a.enc"), "remote:folder/a.enc")
        for value in ("/tmp/backups", "relative/path", "remote:../wrong"):
            with self.assertRaises(ValueError):
                _rclone_destination(value, "a.enc")


class CredentialRotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = ControlPlaneStore(root / "control.db")
        self.store.migrate()
        self.owner, self.tenant = self.store.bootstrap_owner("owner@example.com", "owner")
        self.old_key = _key(b"o")
        self.new_key = _key(b"n")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_rotation_reencrypts_every_secret_and_preserves_version_on_status_update(self) -> None:
        old = CredentialKeyring(self.old_key, active_version=1)
        self.store.set_exchange_connection(
            self.tenant["id"], "okx", "1234", target_id="okx-swap",
            credential_blob=old.encrypt(self.tenant["id"], "okx-swap", {"api_secret": "secret"}),
            key_version=1,
        )
        self.store.set_telegram_connection(
            self.tenant["id"], "-100", "9876",
            credential_blob=old.encrypt(self.tenant["id"], "telegram", {"bot_token": "token"}),
            key_version=1,
        )
        rotated = CredentialKeyring(self.new_key, active_version=2, previous_keys=f"1:{self.old_key}")
        counts = rotate_credential_keyring(self.store, source=old, destination=rotated)
        self.assertEqual(counts, {"exchange_connections": 1, "telegram_connections": 1})
        exchange = self.store.encrypted_credentials_with_version(self.tenant["id"])
        self.assertEqual(exchange[2], 2)
        self.assertEqual(
            rotated.decrypt(self.tenant["id"], exchange[0], exchange[1], key_version=exchange[2]),
            {"api_secret": "secret"},
        )
        with self.assertRaises(ValueError):
            old.decrypt(self.tenant["id"], exchange[0], exchange[1], key_version=1)
        self.store.set_exchange_connection(
            self.tenant["id"], "okx", "1234", target_id="okx-swap", status="tested",
        )
        self.assertEqual(self.store.encrypted_credentials_with_version(self.tenant["id"])[2], 2)

    def test_environment_update_is_atomic_and_preserves_unrelated_values(self) -> None:
        path = Path(self.temp.name) / "control.env"
        path.write_text("OTHER=value\nXAUBY_CREDENTIAL_MASTER_KEY=old\n", encoding="utf-8")
        path.chmod(0o640)
        update_environment(path, {
            "XAUBY_CREDENTIAL_MASTER_KEY": "new",
            "XAUBY_CREDENTIAL_MASTER_KEY_VERSION": "2",
        })
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            "OTHER=value\nXAUBY_CREDENTIAL_MASTER_KEY=new\nXAUBY_CREDENTIAL_MASTER_KEY_VERSION=2\n",
        )
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)

    def test_rotation_command_stages_keyring_then_reencrypts_database(self) -> None:
        old = CredentialKeyring(self.old_key)
        self.store.set_exchange_connection(
            self.tenant["id"], "okx", "1234", target_id="okx-swap",
            credential_blob=old.encrypt(self.tenant["id"], "okx-swap", {"api_secret": "secret"}),
        )
        root = Path(self.temp.name)
        control_env = root / "control.env"
        control_env.write_text(
            "\n".join([
                f"XAUBY_PROJECT_ROOT={Path(__file__).resolve().parents[1]}",
                f"XAUBY_SAAS_DATA_ROOT={root / 'data'}",
                f"XAUBY_SAAS_DB={self.store.path}",
                f"XAUBY_CREDENTIAL_MASTER_KEY={self.old_key}",
                "XAUBY_SAAS_DEV_LOGIN=true",
                "",
            ]),
            encoding="utf-8",
        )
        missing_backup_env = root / "missing-backup.env"
        with patch.dict(
            os.environ,
            {"XAUBY_CONTROL_ENV": str(control_env), "XAUBY_BACKUP_ENV": str(missing_backup_env)},
            clear=True,
        ):
            self.assertEqual(
                rotate_master_key([
                    "--control-env", str(control_env), "--apply", "--confirm-services-stopped",
                ]),
                0,
            )
        values = dict(
            line.split("=", 1) for line in control_env.read_text(encoding="utf-8").splitlines() if "=" in line
        )
        self.assertEqual(values["XAUBY_CREDENTIAL_MASTER_KEY_VERSION"], "2")
        keyring = CredentialKeyring(
            values["XAUBY_CREDENTIAL_MASTER_KEY"], active_version=2,
            previous_keys=values["XAUBY_CREDENTIAL_PREVIOUS_KEYS"],
        )
        encrypted = self.store.encrypted_credentials_with_version(self.tenant["id"])
        self.assertEqual(encrypted[2], 2)
        self.assertEqual(
            keyring.decrypt(self.tenant["id"], encrypted[0], encrypted[1], key_version=encrypted[2]),
            {"api_secret": "secret"},
        )


if __name__ == "__main__":
    unittest.main()
