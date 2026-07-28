"""Off-host backup, and the master key that must not go with it. Roadmap P2.2.

The master key, the encrypted database and `/var/lib/xauby/backups` were all on
one VPS: losing the host lost every tenant's exchange connection permanently.
The operator's decision was object storage for the database bundle with the key
kept offline, so these tests are mostly about the *separation* holding — a
backup that quietly carried the key beside the ciphertext it opens would defeat
the whole point while looking like success.

The SigV4 signing is tested against a fixed clock so a wrong signature shows up
here rather than on first contact with a real bucket, where the failure is an
opaque 403.
"""
import datetime
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import backup_offsite  # noqa: E402
from scripts.saas_backup import carries_master_key, create_backup  # noqa: E402
from xauby.saas.objectstore import (  # noqa: E402
    ObjectStoreError,
    S3Target,
    put_object,
    signed_headers,
)
from xauby.saas.settings import SaaSSettings  # noqa: E402
from xauby.saas.store import ControlPlaneStore  # noqa: E402

TARGET = S3Target(
    endpoint="https://s3.us-west-004.backblazeb2.com",
    bucket="xauby-backups",
    region="us-west-004",
    access_key_id="000000000000000000000001",
    secret_access_key="K004exampleexampleexampleexample",
    prefix="control-plane",
)
FIXED = datetime.datetime(2026, 7, 28, 12, 0, 0, tzinfo=datetime.timezone.utc)


def _response(status=200, headers=None, text=""):
    response = MagicMock()
    response.status_code = status
    response.headers = headers or {}
    response.text = text
    return response


class TestSigning(unittest.TestCase):
    def test_it_produces_the_headers_s3_requires(self):
        headers = signed_headers(TARGET, "PUT", "control-plane/a.tar.gz",
                                 hashlib.sha256(b"x").hexdigest(), now=FIXED)
        self.assertIn("Authorization", headers)
        self.assertEqual(headers["x-amz-date"], "20260728T120000Z")
        self.assertTrue(headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential="))
        self.assertIn(f"/{TARGET.region}/s3/aws4_request", headers["Authorization"])

    def test_the_signature_is_deterministic_for_a_fixed_moment(self):
        first = signed_headers(TARGET, "PUT", "k", "abc", now=FIXED)
        second = signed_headers(TARGET, "PUT", "k", "abc", now=FIXED)
        self.assertEqual(first, second)

    def test_the_signature_covers_the_payload_digest(self):
        # If it did not, a corrupted body would still be accepted as signed.
        one = signed_headers(TARGET, "PUT", "k", "a" * 64, now=FIXED)["Authorization"]
        two = signed_headers(TARGET, "PUT", "k", "b" * 64, now=FIXED)["Authorization"]
        self.assertNotEqual(one, two)

    def test_the_signature_covers_the_method_and_key(self):
        base = signed_headers(TARGET, "PUT", "k", "abc", now=FIXED)["Authorization"]
        self.assertNotEqual(base, signed_headers(TARGET, "HEAD", "k", "abc", now=FIXED)["Authorization"])
        self.assertNotEqual(base, signed_headers(TARGET, "PUT", "j", "abc", now=FIXED)["Authorization"])

    def test_host_is_signed_but_not_sent_as_a_header(self):
        # requests sets Host itself; sending our own would risk a duplicate.
        headers = signed_headers(TARGET, "PUT", "k", "abc", now=FIXED)
        self.assertNotIn("host", headers)
        self.assertIn("host", headers["Authorization"])

    def test_the_key_layout_honours_the_prefix(self):
        self.assertEqual(TARGET.key_for("a.tar.gz"), "control-plane/a.tar.gz")
        bare = S3Target("https://e", "b", "r", "a", "s")
        self.assertEqual(bare.key_for("a.tar.gz"), "a.tar.gz")


class TestUploadConfirmsTheObjectLanded(unittest.TestCase):
    def setUp(self):
        self.body = b"backup-bytes"
        self.md5 = hashlib.md5(self.body).hexdigest()  # noqa: S324

    def _session(self, put=None, head=None):
        session = MagicMock()
        session.put.return_value = put or _response(200)
        session.head.return_value = head or _response(
            200, {"Content-Length": str(len(self.body)), "ETag": f'"{self.md5}"'})
        return session

    def test_a_good_upload_reports_what_it_verified(self):
        result = put_object(TARGET, "k", self.body, session=self._session())
        self.assertEqual(result["bytes"], len(self.body))
        self.assertTrue(result["etag_verified"])
        self.assertEqual(result["sha256"], hashlib.sha256(self.body).hexdigest())

    def test_a_failed_put_raises(self):
        with self.assertRaises(ObjectStoreError):
            put_object(TARGET, "k", self.body,
                       session=self._session(put=_response(403, text="denied")))

    def test_an_object_that_cannot_be_read_back_is_a_failure(self):
        # A 200 on PUT is a claim; the HEAD is the check.
        with self.assertRaises(ObjectStoreError):
            put_object(TARGET, "k", self.body, session=self._session(head=_response(404)))

    def test_a_truncated_object_is_a_failure(self):
        with self.assertRaises(ObjectStoreError):
            put_object(TARGET, "k", self.body,
                       session=self._session(head=_response(200, {"Content-Length": "3"})))

    def test_a_mismatching_etag_is_a_failure(self):
        with self.assertRaises(ObjectStoreError):
            put_object(TARGET, "k", self.body, session=self._session(
                head=_response(200, {"Content-Length": str(len(self.body)),
                                     "ETag": '"' + "0" * 32 + '"'})))

    def test_an_opaque_etag_is_accepted_but_reported_as_unverified(self):
        # R2 and some gateways do not return an MD5. Accepting it silently as
        # "verified" would overstate what was checked.
        result = put_object(TARGET, "k", self.body, session=self._session(
            head=_response(200, {"Content-Length": str(len(self.body)),
                                 "ETag": '"opaque-gateway-value"'})))
        self.assertFalse(result["etag_verified"])

    def test_an_unconfigured_target_raises_before_any_request(self):
        session = MagicMock()
        with self.assertRaises(ObjectStoreError):
            put_object(S3Target("", "", "r", "", ""), "k", b"x", session=session)
        session.put.assert_not_called()

    def test_missing_settings_are_named(self):
        missing = S3Target("", "b", "r", "", "s").missing()
        self.assertIn("XAUBY_BACKUP_S3_ENDPOINT", missing)
        self.assertIn("XAUBY_BACKUP_S3_ACCESS_KEY_ID", missing)
        self.assertNotIn("XAUBY_BACKUP_S3_BUCKET", missing)

    def test_there_is_no_delete_verb(self):
        # A host that can erase its own off-host backups has not gained much.
        import xauby.saas.objectstore as module
        source = Path(module.__file__).read_text(encoding="utf-8")
        self.assertNotIn(".delete(", source)
        self.assertNotIn('"DELETE"', source)


class TestTheMasterKeyNeverLeavesTheHost(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _env_file(self, text):
        path = self.root / "control.env"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_real_assignment_is_detected(self):
        self.assertTrue(carries_master_key(
            self._env_file("XAUBY_CREDENTIAL_MASTER_KEY=c29tZS1yZWFsLWtleQ==\n")))

    def test_the_retired_key_list_counts_too(self):
        self.assertTrue(carries_master_key(
            self._env_file("XAUBY_CREDENTIAL_RETIRED_KEYS=1:c29tZXRoaW5n\n")))

    def test_a_commented_example_does_not_trip_the_guard(self):
        self.assertFalse(carries_master_key(
            self._env_file("#XAUBY_CREDENTIAL_MASTER_KEY=example\n")))

    def test_an_empty_placeholder_does_not_trip_the_guard(self):
        self.assertFalse(carries_master_key(
            self._env_file("XAUBY_CREDENTIAL_MASTER_KEY=\n")))

    def test_an_unrelated_env_file_is_fine(self):
        self.assertFalse(carries_master_key(
            self._env_file("XAUBY_PUBLIC_BASE_URL=https://example.test\n")))

    def test_creating_a_backup_refuses_a_host_config_holding_the_key(self):
        settings = SaaSSettings(
            project_root=Path(__file__).resolve().parents[1],
            data_root=self.root / "data", tenant_config_root=self.root / "config",
            tenant_runtime_root=self.root / "runtime",
            database_path=self.root / "control.db",
            public_base_url="http://testserver", session_secret="x" * 32,
        )
        settings.ensure_directories()
        ControlPlaneStore(settings.database_path).migrate()
        control_env = self._env_file("XAUBY_CREDENTIAL_MASTER_KEY=c29tZS1yZWFsLWtleQ==\n")
        with self.assertRaises(RuntimeError) as caught:
            create_backup(settings, self.root / "out", kind="daily", release_id="t",
                          retention_days=7, host_config=[control_env])
        self.assertIn("master key", str(caught.exception))

    def test_upload_refuses_an_archive_carrying_the_key(self):
        # The second of two independent checks, placed at the step that moves
        # bytes off the host — closest to the irreversible action.
        archive = self.root / "xauby-saas-daily-x.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            payload = b"XAUBY_CREDENTIAL_MASTER_KEY=c29tZS1yZWFsLWtleQ==\n"
            info = tarfile.TarInfo("xauby-saas/host-config/control.env")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        self.assertEqual(backup_offsite.master_key_members(archive),
                         ["xauby-saas/host-config/control.env"])

    def test_a_clean_archive_reports_no_offenders(self):
        archive = self.root / "xauby-saas-daily-x.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            payload = json.dumps({"format": 2}).encode()
            info = tarfile.TarInfo("xauby-saas/manifest.json")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        self.assertEqual(backup_offsite.master_key_members(archive), [])

    def test_the_backup_manifest_records_the_exclusion(self):
        settings = SaaSSettings(
            project_root=Path(__file__).resolve().parents[1],
            data_root=self.root / "data", tenant_config_root=self.root / "config",
            tenant_runtime_root=self.root / "runtime",
            database_path=self.root / "control.db",
            public_base_url="http://testserver", session_secret="x" * 32,
        )
        settings.ensure_directories()
        ControlPlaneStore(settings.database_path).migrate()
        from scripts.saas_backup import verify_backup
        archive = create_backup(settings, self.root / "out", kind="daily",
                                release_id="t", retention_days=7, host_config=[])
        self.assertIs(verify_backup(archive)["master_key_excluded"], True)


class TestTheEntryPointFailsLoudly(unittest.TestCase):
    def test_it_reports_unconfigured_rather_than_doing_nothing(self):
        # A backup task that silently succeeds while uploading nothing is the
        # failure this whole phase keeps finding.
        from unittest.mock import patch
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(backup_offsite.main(["--check"]), 1)
            self.assertEqual(backup_offsite.main(["--latest", "/tmp"]), 1)

    def test_check_passes_once_configured(self):
        from unittest.mock import patch
        env = {
            "XAUBY_BACKUP_S3_ENDPOINT": TARGET.endpoint,
            "XAUBY_BACKUP_S3_BUCKET": TARGET.bucket,
            "XAUBY_BACKUP_S3_ACCESS_KEY_ID": TARGET.access_key_id,
            "XAUBY_BACKUP_S3_SECRET_ACCESS_KEY": TARGET.secret_access_key,
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(backup_offsite.main(["--check"]), 0)
            self.assertEqual(backup_offsite.target_from_env().region, "us-east-1")

    def test_a_missing_directory_is_an_error_not_a_silent_skip(self):
        from unittest.mock import patch
        env = {
            "XAUBY_BACKUP_S3_ENDPOINT": TARGET.endpoint,
            "XAUBY_BACKUP_S3_BUCKET": TARGET.bucket,
            "XAUBY_BACKUP_S3_ACCESS_KEY_ID": TARGET.access_key_id,
            "XAUBY_BACKUP_S3_SECRET_ACCESS_KEY": TARGET.secret_access_key,
        }
        with tempfile.TemporaryDirectory() as empty, patch.dict(os.environ, env, clear=True):
            self.assertEqual(backup_offsite.main(["--latest", empty]), 1)

    def test_the_backup_unit_runs_the_offsite_step(self):
        unit = (Path(__file__).resolve().parents[1] / "deploy" / "systemd"
                / "xauby-backup.service").read_text(encoding="utf-8")
        self.assertIn("scripts.backup_offsite", unit)
        offsite = [line for line in unit.splitlines() if "backup_offsite" in line]
        # No `-` prefix: a failed off-host copy must fail the unit.
        self.assertTrue(all(not line.split("=", 1)[1].strip().startswith("-")
                            for line in offsite))


class TestUploadVerifiesBeforeSending(unittest.TestCase):
    def test_a_corrupt_archive_is_never_uploaded(self):
        # Uploading it would replace a real backup in the operator's mind.
        session = MagicMock()
        with tempfile.TemporaryDirectory() as temp:
            broken = Path(temp) / "xauby-saas-daily-x.tar.gz"
            broken.write_bytes(b"not a tarball")
            with self.assertRaises(tarfile.ReadError):
                backup_offsite.upload(broken, TARGET, session=session)
        session.put.assert_not_called()


if __name__ == "__main__":
    unittest.main()
