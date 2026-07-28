"""Key rotation is real, and the column that named it now means something.

Roadmap P2.2. `encrypted_credentials` carried a `key_version` column from the
day it was created and nothing rotated. The column was not merely unused: both
credential upserts wrote it as a SQL **literal** `1`, so the schema was
incapable of recording a second key. The audit called it "a column that names a
capability nobody implemented"; the choice was to implement it or drop it.

The tests that matter most here are the ones about *not losing credentials*.
A rotation bug does not look like a failed request — it looks like every
tenant's exchange keys becoming permanently unreadable, discovered on the next
engine restart. So: every failure path is checked for what it does to the
stored blob, and the happy paths are checked for what they leave untouched.
"""
import base64
import contextlib
import io
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xauby.saas.credentials import (  # noqa: E402
    LEGACY_KEY_ID,
    CredentialCipher,
    CredentialDecryptError,
    UnknownCredentialKey,
    parse_key_set,
)
from xauby.saas.keyfile import read_env, write_env  # noqa: E402
from xauby.saas.store import ControlPlaneStore  # noqa: E402

KEY_1 = base64.b64encode(b"1" * 32).decode()
KEY_2 = base64.b64encode(b"2" * 32).decode()
KEY_3 = base64.b64encode(b"3" * 32).decode()

CREDS = {"api_key": "ak-live-0001", "api_secret": "as-live-0002", "passphrase": "pp-0003"}


def cipher(active=KEY_1, *, key_id=1, retired=""):
    return CredentialCipher(active, active_key_id=key_id, retired_keys=retired)


class TestEnvelopeFormat(unittest.TestCase):
    def test_a_new_envelope_names_its_key(self):
        blob = cipher(KEY_2, key_id=7).encrypt("t1", "okx-swap", CREDS)
        self.assertEqual(CredentialCipher.key_id_of(blob), 7)

    def test_an_envelope_with_no_key_id_is_the_legacy_key(self):
        # Every blob written before this change looks like this.
        legacy = '{"v":1,"n":"AAAAAAAAAAAAAAAA","c":"AAAA"}'
        self.assertEqual(CredentialCipher.key_id_of(legacy), LEGACY_KEY_ID)

    def test_legacy_envelopes_still_decrypt(self):
        # Written by the previous implementation: same AAD, no "k" field.
        import json

        old = cipher()
        blob = json.loads(old.encrypt("t1", "okx-swap", CREDS))
        blob.pop("k")
        self.assertEqual(old.decrypt("t1", "okx-swap", json.dumps(blob)), CREDS)

    def test_a_rollback_can_still_read_what_this_code_wrote(self):
        # The key id is additive and outside the AAD, so as long as the active
        # key is still id 1 a rollback to the pre-rotation code is not lossy.
        import json

        blob = json.loads(cipher().encrypt("t1", "okx-swap", CREDS))
        self.assertEqual(blob["v"], 1)
        self.assertEqual(blob["k"], 1)
        # Reconstruct what the old decrypt did: it never read "k".
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        plaintext = AESGCM(base64.b64decode(KEY_1)).decrypt(
            base64.b64decode(blob["n"]), base64.b64decode(blob["c"]),
            b"xauby:t1:okx-swap:v1")
        self.assertEqual(json.loads(plaintext), CREDS)

    def test_the_aad_still_binds_tenant_and_target(self):
        blob = cipher().encrypt("t1", "okx-swap", CREDS)
        for tenant, target in (("t2", "okx-swap"), ("t1", "binance-th-spot")):
            with self.subTest(tenant=tenant, target=target):
                with self.assertRaises(CredentialDecryptError):
                    cipher().decrypt(tenant, target, blob)


class TestRotation(unittest.TestCase):
    def test_a_retired_key_still_opens_its_own_blobs(self):
        old_blob = cipher(KEY_1, key_id=1).encrypt("t1", "okx-swap", CREDS)
        rotated = cipher(KEY_2, key_id=2, retired=f"1:{KEY_1}")
        self.assertEqual(rotated.decrypt("t1", "okx-swap", old_blob), CREDS)

    def test_rewrap_moves_a_blob_to_the_active_key(self):
        old_blob = cipher(KEY_1, key_id=1).encrypt("t1", "okx-swap", CREDS)
        rotated = cipher(KEY_2, key_id=2, retired=f"1:{KEY_1}")
        new_blob = rotated.rewrap("t1", "okx-swap", old_blob)
        self.assertEqual(CredentialCipher.key_id_of(new_blob), 2)
        self.assertEqual(rotated.decrypt("t1", "okx-swap", new_blob), CREDS)

    def test_a_rewrapped_blob_no_longer_needs_the_old_key(self):
        # This is the property that makes --retire-complete safe.
        old_blob = cipher(KEY_1, key_id=1).encrypt("t1", "okx-swap", CREDS)
        rotated = cipher(KEY_2, key_id=2, retired=f"1:{KEY_1}")
        new_blob = rotated.rewrap("t1", "okx-swap", old_blob)
        retired_gone = cipher(KEY_2, key_id=2)
        self.assertEqual(retired_gone.decrypt("t1", "okx-swap", new_blob), CREDS)

    def test_rewrap_is_a_noop_when_already_current(self):
        blob = cipher(KEY_2, key_id=2).encrypt("t1", "okx-swap", CREDS)
        self.assertIsNone(cipher(KEY_2, key_id=2).rewrap("t1", "okx-swap", blob))

    def test_rewrap_is_idempotent(self):
        old_blob = cipher(KEY_1, key_id=1).encrypt("t1", "okx-swap", CREDS)
        rotated = cipher(KEY_2, key_id=2, retired=f"1:{KEY_1}")
        once = rotated.rewrap("t1", "okx-swap", old_blob)
        self.assertIsNone(rotated.rewrap("t1", "okx-swap", once))

    def test_three_generations_of_keys_all_stay_readable(self):
        gen1 = cipher(KEY_1, key_id=1).encrypt("t1", "okx-swap", CREDS)
        gen2 = cipher(KEY_2, key_id=2, retired=f"1:{KEY_1}").encrypt("t2", "okx-swap", CREDS)
        newest = cipher(KEY_3, key_id=3, retired=f"1:{KEY_1},2:{KEY_2}")
        self.assertEqual(newest.decrypt("t1", "okx-swap", gen1), CREDS)
        self.assertEqual(newest.decrypt("t2", "okx-swap", gen2), CREDS)


class TestEveryFailurePathProtectsTheBlob(unittest.TestCase):
    def test_a_missing_key_is_distinguishable_from_a_broken_blob(self):
        # The remedies are opposite: put the retired key back, versus tell the
        # tenant to reconnect. Collapsing them into one error would send the
        # operator down the wrong path on the worse of the two.
        blob = cipher(KEY_2, key_id=2).encrypt("t1", "okx-swap", CREDS)
        with self.assertRaises(UnknownCredentialKey):
            cipher(KEY_1, key_id=1).decrypt("t1", "okx-swap", blob)

    def test_a_tampered_blob_is_not_reported_as_a_missing_key(self):
        import json

        payload = json.loads(cipher().encrypt("t1", "okx-swap", CREDS))
        payload["c"] = base64.b64encode(b"\x00" * 48).decode()
        with self.assertRaises(CredentialDecryptError) as caught:
            cipher().decrypt("t1", "okx-swap", json.dumps(payload))
        self.assertNotIsInstance(caught.exception, UnknownCredentialKey)

    def test_rewrap_raises_rather_than_returning_an_unopenable_envelope(self):
        blob = cipher(KEY_2, key_id=2).encrypt("t1", "okx-swap", CREDS)
        with self.assertRaises(UnknownCredentialKey):
            cipher(KEY_1, key_id=1).rewrap("t1", "okx-swap", blob)

    def test_every_decrypt_error_is_still_a_valueerror(self):
        # Existing call sites catch ValueError; narrowing the hierarchy would
        # turn a handled failure into a 500.
        for exc in (CredentialDecryptError, UnknownCredentialKey):
            self.assertTrue(issubclass(exc, ValueError))

    def test_one_key_id_may_not_name_two_different_keys(self):
        # Whichever one is consulted, half the blobs stop opening and the tag
        # failure looks like corruption.
        with self.assertRaises(ValueError):
            cipher(KEY_2, key_id=1, retired=f"1:{KEY_1}")

    def test_restating_the_active_key_as_retired_is_harmless(self):
        c = cipher(KEY_1, key_id=1, retired=f"1:{KEY_1}")
        self.assertEqual(c.active_key_id, 1)
        self.assertEqual(c.retired_key_ids, ())

    def test_a_malformed_retired_list_raises_instead_of_being_skipped(self):
        # A silently dropped retired key looks exactly like a finished rotation.
        for bad in ("nonsense", "1:not-base64", f"x:{KEY_1}", f"1:{base64.b64encode(b'short').decode()}"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_key_set(bad)

    def test_an_empty_retired_list_is_fine(self):
        self.assertEqual(parse_key_set(""), {})
        self.assertEqual(parse_key_set("  ,  "), {})


class TestStoreRecordsTheKey(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ControlPlaneStore(Path(self.temp.name) / "control.db")
        self.store.migrate()
        _, tenant = self.store.bootstrap_owner("owner@example.com", "owner-test")
        self.tenant_id = tenant["id"]

    def _exchange_row(self):
        with self.store.connection() as conn:
            return dict(conn.execute(
                "SELECT * FROM exchange_connections WHERE tenant_id=?",
                (self.tenant_id,)).fetchone())

    def test_key_version_is_stored_rather_than_hardcoded(self):
        # It was a SQL literal `1` in both upserts.
        self.store.set_exchange_connection(
            self.tenant_id, "okx", "1234", credential_blob="blob", key_version=4)
        self.assertEqual(self._exchange_row()["key_version"], 4)

    def test_telegram_key_version_is_stored_too(self):
        self.store.set_telegram_connection(
            self.tenant_id, "123", "9999", credential_blob="blob", key_version=5)
        with self.store.connection() as conn:
            row = conn.execute("SELECT key_version FROM telegram_connections WHERE tenant_id=?",
                               (self.tenant_id,)).fetchone()
        self.assertEqual(row["key_version"], 5)

    def test_a_status_only_update_does_not_relabel_the_existing_blob(self):
        # The upsert keeps the old ciphertext via coalesce when no blob is
        # passed, so taking the new key id would label a blob with a key that
        # never encrypted it — and rotation would then skip it forever.
        self.store.set_exchange_connection(
            self.tenant_id, "okx", "1234", credential_blob="blob-v2", key_version=2)
        self.store.set_exchange_connection(
            self.tenant_id, "okx", "1234", status="tested", key_version=9)
        row = self._exchange_row()
        self.assertEqual(row["credential_blob"], "blob-v2")
        self.assertEqual(row["key_version"], 2)

    def test_credential_blobs_reports_both_kinds_with_their_aad(self):
        self.store.set_exchange_connection(
            self.tenant_id, "okx", "1234", target_id="okx-swap",
            credential_blob="ex", key_version=2)
        self.store.set_telegram_connection(
            self.tenant_id, "123", "9999", credential_blob="tg", key_version=2)
        rows = {row["kind"]: row for row in self.store.credential_blobs()}
        self.assertEqual(rows["exchange"]["target_id"], "okx-swap")
        self.assertEqual(rows["telegram"]["target_id"], "telegram")
        self.assertEqual(rows["exchange"]["key_version"], 2)

    def test_credential_blobs_skips_rows_with_no_envelope(self):
        self.store.set_exchange_connection(self.tenant_id, "okx", "1234")
        self.assertEqual(self.store.credential_blobs(), [])

    def test_rewrapping_does_not_disturb_status_or_capabilities(self):
        # Re-encrypting is not re-testing. A rotation that reset a tenant's
        # live-activation state would be worse than the gap it closes.
        self.store.set_exchange_connection(
            self.tenant_id, "okx", "1234", status="tested",
            capabilities={"swap": True}, credential_blob="old", key_version=1)
        before = self._exchange_row()
        self.store.update_credential_blob("exchange", self.tenant_id, "new", 2)
        after = self._exchange_row()
        self.assertEqual(after["credential_blob"], "new")
        self.assertEqual(after["key_version"], 2)
        for field in ("status", "capabilities_json", "tested_at", "key_last4", "updated_at"):
            self.assertEqual(after[field], before[field], field)

    def test_updating_a_missing_row_raises_rather_than_inserting(self):
        with self.assertRaises(KeyError):
            self.store.update_credential_blob("exchange", self.tenant_id, "new", 2)

    def test_an_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.update_credential_blob("sqli", self.tenant_id, "new", 2)

    def test_the_audit_row_carries_no_key_material(self):
        self.store.set_exchange_connection(
            self.tenant_id, "okx", "1234", credential_blob="old", key_version=1)
        self.store.update_credential_blob("exchange", self.tenant_id, "secret-blob", 2)
        with self.store.connection() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM audit_events WHERE event_type='credential_key_rotated'")]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("secret-blob", str(rows[0]))

    def test_the_upserts_no_longer_pin_key_version(self):
        source = Path(__file__).resolve().parents[1] / "xauby" / "saas" / "store.py"
        text = source.read_text(encoding="utf-8")
        for literal in ("VALUES (?,?,?,?,?,?,?,?,1,?)", "VALUES (?,?,?,?,?,?,?,1,?,?)"):
            self.assertNotIn(literal, text,
                             "key_version is a SQL literal again; rotation cannot record a key")


class TestEnvFileEditing(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "control.env"
        self.path.write_text(
            "# operator notes worth keeping\n"
            "XAUBY_CREDENTIAL_MASTER_KEY=old\n"
            "XAUBY_PUBLIC_BASE_URL=https://example.test\n",
            encoding="utf-8")
        os.chmod(self.path, 0o640)

    def test_it_replaces_a_value_and_keeps_everything_else(self):
        self.assertTrue(write_env(self.path, {"XAUBY_CREDENTIAL_MASTER_KEY": "new"}))
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("XAUBY_CREDENTIAL_MASTER_KEY=new", text)
        self.assertIn("# operator notes worth keeping", text)
        self.assertIn("XAUBY_PUBLIC_BASE_URL=https://example.test", text)

    def test_it_appends_a_key_that_was_absent(self):
        write_env(self.path, {"XAUBY_CREDENTIAL_MASTER_KEY_ID": "2"})
        self.assertEqual(read_env(self.path)["XAUBY_CREDENTIAL_MASTER_KEY_ID"], "2")

    def test_none_removes_a_key(self):
        write_env(self.path, {"XAUBY_PUBLIC_BASE_URL": None})
        self.assertNotIn("XAUBY_PUBLIC_BASE_URL", read_env(self.path))

    def test_it_rewrites_every_duplicate_not_just_the_first(self):
        # A leftover duplicate would shadow the new value depending on which
        # line the loader reads last — on the file holding the master key.
        self.path.write_text("K=one\nK=two\n", encoding="utf-8")
        write_env(self.path, {"K": "three"})
        self.assertEqual(self.path.read_text(encoding="utf-8").count("K=three"), 2)
        self.assertNotIn("K=one", self.path.read_text(encoding="utf-8"))

    def test_the_mode_survives_the_rewrite(self):
        write_env(self.path, {"XAUBY_CREDENTIAL_MASTER_KEY": "new"})
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)

    def test_an_unchanged_write_reports_no_change(self):
        self.assertFalse(write_env(self.path, {"XAUBY_CREDENTIAL_MASTER_KEY": "old"}))

    def test_the_provisioning_script_still_works_through_the_shared_helper(self):
        import importlib.util

        script = Path(__file__).resolve().parents[1] / "scripts" / "ensure_control_master_key.py"
        spec = importlib.util.spec_from_file_location("ensure_key", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        self.assertTrue(module.ensure_master_key(self.path))       # "old" is not valid base64
        minted = read_env(self.path)["XAUBY_CREDENTIAL_MASTER_KEY"]
        self.assertEqual(len(base64.b64decode(minted, validate=True)), 32)
        self.assertFalse(module.ensure_master_key(self.path))      # now valid: left alone
        self.assertEqual(read_env(self.path)["XAUBY_CREDENTIAL_MASTER_KEY"], minted)


class TestSettingsCarryTheKeySet(unittest.TestCase):
    """The wiring, not just the cipher. P1.6's finding was a correct component
    that nothing ever called."""

    def _from_env(self, **overrides):
        from unittest.mock import patch

        from xauby.saas.settings import SaaSSettings

        env = {
            "XAUBY_CONTROL_ENV": "/nonexistent",
            "XAUBY_SAAS_SESSION_SECRET": "x" * 32,
            "XAUBY_CREDENTIAL_MASTER_KEY": KEY_1,
            **overrides,
        }
        with patch.dict(os.environ, env, clear=True):
            return SaaSSettings.from_env(project_root=self.__class__.__module__ and ".")

    def test_defaults_reproduce_a_single_key_deployment(self):
        settings = self._from_env()
        self.assertEqual(settings.credential_master_key_id, 1)
        self.assertEqual(settings.credential_retired_keys, "")

    def test_the_key_set_reaches_the_cipher(self):
        settings = self._from_env(
            XAUBY_CREDENTIAL_MASTER_KEY=KEY_2,
            XAUBY_CREDENTIAL_MASTER_KEY_ID="2",
            XAUBY_CREDENTIAL_RETIRED_KEYS=f"1:{KEY_1}",
        )
        built = CredentialCipher(
            settings.credential_master_key,
            active_key_id=settings.credential_master_key_id,
            retired_keys=settings.credential_retired_keys)
        self.assertEqual(built.active_key_id, 2)
        self.assertEqual(built.retired_key_ids, (1,))
        old_blob = cipher(KEY_1, key_id=1).encrypt("t1", "okx-swap", CREDS)
        self.assertEqual(built.decrypt("t1", "okx-swap", old_blob), CREDS)

    def test_a_typod_key_id_stops_startup_rather_than_being_rounded(self):
        # Clamping to 1 would stamp envelopes with a key the operator does not
        # think is in use, and the mislabelling would only surface at rotation.
        for bad in ("nonsense", "1.5", "2 3"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self._from_env(XAUBY_CREDENTIAL_MASTER_KEY_ID=bad)

    def test_a_blank_key_id_means_unset(self):
        # `KEY_ID=` with nothing after it is how an operator leaves a commented
        # example in place; it is not a typo and must not block startup.
        self.assertEqual(
            self._from_env(XAUBY_CREDENTIAL_MASTER_KEY_ID="").credential_master_key_id, 1)

    def test_a_non_positive_key_id_is_refused_by_the_cipher(self):
        settings = self._from_env(XAUBY_CREDENTIAL_MASTER_KEY_ID="0")
        with self.assertRaises(ValueError):
            CredentialCipher(settings.credential_master_key,
                             active_key_id=settings.credential_master_key_id)


def _load_script():
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "rotate_credential_key.py"
    spec = importlib.util.spec_from_file_location("rotate_credential_key", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestTheRunbookEndToEnd(unittest.TestCase):
    """The four commands, in the order the runbook gives them.

    Testing the script rather than only the cipher, because P1.6's finding was
    an auditor that computed correctly and was never invoked.
    """

    def setUp(self):
        self.script = _load_script()
        # The tool reports to stdout by design; keep it out of the suite's log.
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = ControlPlaneStore(root / "control.db")
        self.store.migrate()
        _, tenant = self.store.bootstrap_owner("owner@example.com", "owner-test")
        self.tenant_id = tenant["id"]
        self.env = root / "control.env"
        self.env.write_text(f"XAUBY_CREDENTIAL_MASTER_KEY={KEY_1}\n", encoding="utf-8")

        # A tenant connected under key 1, exchange and telegram both.
        old = cipher(KEY_1, key_id=1)
        self.store.set_exchange_connection(
            self.tenant_id, "okx", "1234", target_id="okx-swap", status="tested",
            credential_blob=old.encrypt(self.tenant_id, "okx-swap", CREDS), key_version=1)
        self.store.set_telegram_connection(
            self.tenant_id, "123", "9999",
            credential_blob=old.encrypt(self.tenant_id, "telegram", {"bot_token": "tok"}),
            key_version=1)

    def _rotated_cipher(self):
        values = read_env(self.env)
        return CredentialCipher(
            values["XAUBY_CREDENTIAL_MASTER_KEY"],
            active_key_id=int(values.get("XAUBY_CREDENTIAL_MASTER_KEY_ID", "1")),
            retired_keys=values.get("XAUBY_CREDENTIAL_RETIRED_KEYS", ""))

    def test_status_is_clean_before_any_rotation(self):
        self.assertEqual(self.script.cmd_status(self.store, cipher(KEY_1, key_id=1)), 0)

    def test_the_full_runbook(self):
        # 2. stage a new key
        self.assertEqual(self.script.cmd_stage_key(self.env), 0)
        values = read_env(self.env)
        self.assertEqual(values["XAUBY_CREDENTIAL_MASTER_KEY_ID"], "2")
        self.assertNotEqual(values["XAUBY_CREDENTIAL_MASTER_KEY"], KEY_1)
        self.assertIn("1:", values["XAUBY_CREDENTIAL_RETIRED_KEYS"])

        rotated = self._rotated_cipher()
        # Old blobs are still readable, which is the point of the retired list.
        self.assertEqual(self.script.cmd_status(self.store, rotated), 1)

        # 4. refuses to drop the old key while blobs still need it
        self.assertEqual(self.script.cmd_retire_complete(self.store, rotated, self.env), 1)
        self.assertIn("XAUBY_CREDENTIAL_RETIRED_KEYS", read_env(self.env))

        # 3. rewrap
        self.assertEqual(self.script.cmd_apply(self.store, rotated), 0)
        for row in self.store.credential_blobs():
            self.assertEqual(CredentialCipher.key_id_of(row["blob"]), 2)
            self.assertEqual(row["key_version"], 2)

        # The credentials survived the round trip intact.
        blobs = {r["kind"]: r for r in self.store.credential_blobs()}
        self.assertEqual(
            rotated.decrypt(self.tenant_id, "okx-swap", blobs["exchange"]["blob"]), CREDS)
        self.assertEqual(
            rotated.decrypt(self.tenant_id, "telegram", blobs["telegram"]["blob"]),
            {"bot_token": "tok"})

        # 4. now it drops the old key
        self.assertEqual(self.script.cmd_retire_complete(self.store, rotated, self.env), 0)
        self.assertNotIn("XAUBY_CREDENTIAL_RETIRED_KEYS", read_env(self.env))

        # And everything still opens with the old key gone entirely.
        final = self._rotated_cipher()
        self.assertEqual(final.retired_key_ids, ())
        self.assertEqual(self.script.cmd_status(self.store, final), 0)

    def test_apply_is_idempotent(self):
        self.script.cmd_stage_key(self.env)
        rotated = self._rotated_cipher()
        self.assertEqual(self.script.cmd_apply(self.store, rotated), 0)
        self.assertEqual(self.script.cmd_apply(self.store, rotated), 0)

    def test_rotation_does_not_disturb_the_tested_status(self):
        before = self.store.exchange_connection(self.tenant_id)
        self.script.cmd_stage_key(self.env)
        self.script.cmd_apply(self.store, self._rotated_cipher())
        after = self.store.exchange_connection(self.tenant_id)
        self.assertEqual(after["status"], "tested")
        self.assertEqual(after["tested_at"], before["tested_at"])

    def test_an_undecryptable_blob_is_reported_and_left_alone(self):
        # The worst case: a retired key was dropped too early. The tool must not
        # destroy the row on its way past.
        self.script.cmd_stage_key(self.env)
        values = read_env(self.env)
        stranded = CredentialCipher(values["XAUBY_CREDENTIAL_MASTER_KEY"],
                                    active_key_id=2)  # retired key withheld
        original = {r["kind"]: r["blob"] for r in self.store.credential_blobs()}
        self.assertEqual(self.script.cmd_apply(self.store, stranded), 1)
        surviving = {r["kind"]: r["blob"] for r in self.store.credential_blobs()}
        self.assertEqual(surviving, original)

    def test_staging_twice_without_rewrapping_loses_nothing(self):
        # An interrupted rotation must not wedge: staging again while blobs are
        # still under key 1 pushes key 2 onto the retired list rather than
        # replacing it, so a later --apply can still reach every generation.
        self.assertEqual(self.script.cmd_stage_key(self.env), 0)
        self.assertEqual(self.script.cmd_stage_key(self.env), 0)
        values = read_env(self.env)
        self.assertEqual(values["XAUBY_CREDENTIAL_MASTER_KEY_ID"], "3")
        # Both older keys are retained, so nothing written under either is lost.
        self.assertEqual(sorted(parse_key_set(values["XAUBY_CREDENTIAL_RETIRED_KEYS"])), [1, 2])
        self.assertEqual(self.script.cmd_apply(self.store, self._rotated_cipher()), 0)

    def test_it_refuses_to_stage_against_a_file_with_no_key(self):
        self.env.write_text("XAUBY_PUBLIC_BASE_URL=https://example.test\n", encoding="utf-8")
        self.assertEqual(self.script.cmd_stage_key(self.env), 1)

    def test_it_refuses_to_stage_against_an_invalid_key(self):
        self.env.write_text("XAUBY_CREDENTIAL_MASTER_KEY=not-base64\n", encoding="utf-8")
        self.assertEqual(self.script.cmd_stage_key(self.env), 1)

    def test_a_drifted_key_version_column_is_corrected(self):
        # The envelope is self-describing and wins; the column is an index.
        self.store.update_credential_blob(
            "exchange", self.tenant_id,
            cipher(KEY_1, key_id=1).encrypt(self.tenant_id, "okx-swap", CREDS), 7)
        current = cipher(KEY_1, key_id=1)
        self.assertEqual(self.script.cmd_apply(self.store, current), 0)
        rows = {r["kind"]: r for r in self.store.credential_blobs()}
        self.assertEqual(rows["exchange"]["key_version"], 1)


class TestTheToolNeverLeaksKeyMaterial(unittest.TestCase):
    def test_stage_key_writes_the_key_to_the_file_and_not_to_stdout(self):
        script = _load_script()
        with tempfile.TemporaryDirectory() as temp:
            env = Path(temp) / "control.env"
            env.write_text(f"XAUBY_CREDENTIAL_MASTER_KEY={KEY_1}\n", encoding="utf-8")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                script.cmd_stage_key(env)
            printed = buffer.getvalue()
            values = read_env(env)
        self.assertNotIn(values["XAUBY_CREDENTIAL_MASTER_KEY"], printed)
        self.assertNotIn(KEY_1, printed)
        self.assertNotIn(values["XAUBY_CREDENTIAL_RETIRED_KEYS"], printed)

    def test_apply_prints_no_plaintext_credentials(self):
        script = _load_script()
        with tempfile.TemporaryDirectory() as temp:
            store = ControlPlaneStore(Path(temp) / "control.db")
            store.migrate()
            _, tenant = store.bootstrap_owner("owner@example.com", "owner-test")
            store.set_exchange_connection(
                tenant["id"], "okx", "1234", target_id="okx-swap",
                credential_blob=cipher(KEY_1, key_id=1).encrypt(
                    tenant["id"], "okx-swap", CREDS),
                key_version=1)
            rotated = cipher(KEY_2, key_id=2, retired=f"1:{KEY_1}")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                script.cmd_apply(store, rotated)
            printed = buffer.getvalue()
        for secret in CREDS.values():
            self.assertNotIn(secret, printed)


if __name__ == "__main__":
    unittest.main()
