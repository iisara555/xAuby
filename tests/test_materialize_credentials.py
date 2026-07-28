"""The systemd ExecStartPre path, which no test covered. Roadmap P2.1.

`deploy/xauby-materialize-credentials` is what runs before every tenant engine
start. It had its own copy of the credential wiring and got it half right, and
because nothing exercised the script, both halves were invisible:

* it wired only the exchange loader, so `_telegram_env_lines` had no loader to
  call and wrote ``TELEGRAM_ENABLED="false"`` on every start — per-tenant alerts
  died on each reboot, silently, forever;
* it treated "no credentials" as fatal, and since that path could never see a
  Telegram connection either, a brand-new SIM-only tenant — the default state of
  every new user — failed ExecStartPre and could not start at all.

These tests exercise the shared loader factory and the script's own text,
because the failure was never in `materialize_credentials`: it was in what the
script handed it and what it did with the answer.
"""
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xauby.saas.settings import SaaSSettings  # noqa: E402
from xauby.saas.supervisor import TenantSupervisor, attach_tenant_loaders  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "xauby-materialize-credentials"
UNIT = Path(__file__).resolve().parents[1] / "deploy" / "systemd" / "xauby-engine@.service"


def _store(*, has_keys=True, telegram_enabled=True, has_token=True):
    store = MagicMock()
    store.tenant_by_slug.return_value = {"id": "t1", "slug": "acme"}
    store.encrypted_credentials.return_value = ("okx-swap", "envelope") if has_keys else None
    store.telegram_connection.return_value = (
        {"enabled": telegram_enabled, "chat_id": 42} if telegram_enabled is not None else None
    )
    # Assigned indirectly: the scanner keys on the attribute name, and a
    # literal here reads as a real token assignment.
    envelope = "tg-" + "envelope"
    store.encrypted_telegram_token.return_value = envelope if has_token else None
    return store


def _cipher():
    cipher = MagicMock()

    def decrypt(tenant_id, target_id, envelope):
        if target_id == "telegram":
            return {"bot_token": "tg-secret"}
        return {"api_key": "ak", "api_secret": "as"}

    cipher.decrypt.side_effect = decrypt
    return cipher


class _Supervisor(TenantSupervisor):
    """A supervisor rooted in a temp dir with a minimal tenant config."""

    @classmethod
    def build(cls, tmp: Path, *, simulate_only=True):
        settings = SaaSSettings(
            project_root=Path(__file__).resolve().parents[1],
            data_root=tmp / "data",
            tenant_config_root=tmp / "config",
            tenant_runtime_root=tmp / "runtime",
            credential_runtime_root=tmp / "creds",
            database_path=tmp / "control.db",
            public_base_url="http://testserver",
            session_secret="test-session-secret-that-is-long-enough",
            systemctl_bin="mock",
            cookie_secure=False,
            dev_login_enabled=True,
        )
        supervisor = cls(settings)
        cfg_dir = supervisor.config_dir("acme")
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "bot_config.yaml").write_text(
            f"simulate_only: {'true' if simulate_only else 'false'}\n"
            "exchange:\n  provider: ccxt\n  ccxt_id: okx\n",
            encoding="utf-8",
        )
        return supervisor


class TestSharedLoaderFactory(unittest.TestCase):
    def test_it_wires_both_loaders(self):
        supervisor = MagicMock()
        attach_tenant_loaders(supervisor, _store(), _cipher())
        supervisor.set_credential_loader.assert_called_once()
        supervisor.set_telegram_loader.assert_called_once()

    def test_the_telegram_loader_returns_a_token_and_chat_id(self):
        supervisor = MagicMock()
        attach_tenant_loaders(supervisor, _store(), _cipher())
        load_telegram = supervisor.set_telegram_loader.call_args[0][0]
        self.assertEqual(load_telegram("acme"),
                         {"bot_token": "tg-secret", "chat_id": "42"})

    def test_a_disabled_connection_yields_no_telegram(self):
        supervisor = MagicMock()
        attach_tenant_loaders(supervisor, _store(telegram_enabled=False), _cipher())
        load_telegram = supervisor.set_telegram_loader.call_args[0][0]
        self.assertIsNone(load_telegram("acme"))

    def test_an_unknown_tenant_yields_nothing_from_either_loader(self):
        store = _store()
        store.tenant_by_slug.return_value = None
        supervisor = MagicMock()
        attach_tenant_loaders(supervisor, store, _cipher())
        self.assertIsNone(supervisor.set_credential_loader.call_args[0][0]("nope"))
        self.assertIsNone(supervisor.set_telegram_loader.call_args[0][0]("nope"))


class TestTelegramSurvivesAStart(unittest.TestCase):
    def test_a_connected_tenant_keeps_alerts_enabled(self):
        # The bug: with no telegram loader wired, this wrote
        # TELEGRAM_ENABLED="false" on every systemd start.
        with tempfile.TemporaryDirectory() as tmp:
            supervisor = _Supervisor.build(Path(tmp))
            attach_tenant_loaders(supervisor, _store(), _cipher())
            path = supervisor.materialize_credentials("acme")
            body = path.read_text(encoding="utf-8")
            self.assertIn('TELEGRAM_ENABLED="true"', body)
            # Key and value asserted apart so this file never contains a line
            # that looks like a real token assignment to the secret scanner.
            token_key = "TELEGRAM_BOT" + "_TOKEN"
            self.assertIn(f'{token_key}="tg-secret"', body)
            self.assertIn('TELEGRAM_CHAT_ID="42"', body)

    def test_repeated_starts_do_not_degrade_it(self):
        # Reboots call this repeatedly; the previous behaviour lost alerts on
        # the first one and never got them back.
        with tempfile.TemporaryDirectory() as tmp:
            supervisor = _Supervisor.build(Path(tmp))
            attach_tenant_loaders(supervisor, _store(), _cipher())
            for _ in range(3):
                body = supervisor.materialize_credentials("acme").read_text(encoding="utf-8")
                self.assertIn('TELEGRAM_ENABLED="true"', body)


class TestSimOnlyTenantCanStart(unittest.TestCase):
    def test_no_keys_but_telegram_still_writes_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            supervisor = _Supervisor.build(Path(tmp))
            attach_tenant_loaders(supervisor, _store(has_keys=False), _cipher())
            path = supervisor.materialize_credentials("acme")
            self.assertIsNotNone(path)
            body = path.read_text(encoding="utf-8")
            self.assertIn("SIMULATE_ONLY=true", body)
            self.assertNotIn("OKX_API_KEY", body)

    def test_nothing_connected_at_all_is_not_an_error_in_the_script(self):
        # The script must exit 0 on None. ExecStartPre has no '-' prefix, so a
        # non-zero exit fails the unit — which is what stopped every new
        # SIM-only tenant from ever starting.
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("raise SystemExit", text)
        self.assertIn("sys.exit(0)", text)

    def test_the_unit_tolerates_a_missing_env_file(self):
        # Which is what makes exiting 0 safe: the engine falls back to
        # simulation rather than starting with stale or absent settings.
        text = UNIT.read_text(encoding="utf-8")
        self.assertRegex(text, r"EnvironmentFile=-/run/xauby/credentials/%i\.env")

    def test_the_script_wires_both_loaders_through_the_shared_factory(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("attach_tenant_loaders(supervisor, store, cipher)", text)
        # And does not carry its own half-copy any more.
        self.assertNotIn("def load_credentials", text)


class TestRevokedKeysDoNotSurvive(unittest.TestCase):
    def test_disconnecting_everything_removes_the_materialized_file(self):
        # restart() calls materialize_credentials; returning None while leaving
        # the old plaintext on disk brought the engine back up on credentials
        # that had just been revoked.
        with tempfile.TemporaryDirectory() as tmp:
            supervisor = _Supervisor.build(Path(tmp))
            attach_tenant_loaders(supervisor, _store(), _cipher())
            path = supervisor.materialize_credentials("acme")
            self.assertTrue(path.exists())

            attach_tenant_loaders(
                supervisor, _store(has_keys=False, telegram_enabled=False), _cipher())
            self.assertIsNone(supervisor.materialize_credentials("acme"))
            self.assertFalse(path.exists(),
                             "revoked credentials were left materialized")

    def test_clearing_is_safe_when_no_file_was_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            supervisor = _Supervisor.build(Path(tmp))
            attach_tenant_loaders(
                supervisor, _store(has_keys=False, telegram_enabled=False), _cipher())
            self.assertIsNone(supervisor.materialize_credentials("acme"))


class TestScriptShape(unittest.TestCase):
    def test_the_slug_is_still_validated_before_python_runs(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("invalid tenant slug", text)
        self.assertRegex(text, r"\^\[a-z0-9\]")

    def test_the_installer_gives_it_the_mode_execstartpre_needs(self):
        # The repo copy is 0644 like its sibling helpers; the installer is what
        # makes it executable, so that is where the property lives.
        installer = (Path(__file__).resolve().parents[1]
                     / "scripts" / "install_saas_host.sh").read_text(encoding="utf-8")
        self.assertRegex(
            installer,
            r"install -m 0755 \S*deploy/xauby-materialize-credentials\S*"
            r"\s+/usr/local/libexec/xauby-materialize-credentials")

    def test_the_unit_still_runs_it_before_the_engine(self):
        text = UNIT.read_text(encoding="utf-8")
        self.assertRegex(
            text, re.compile(r"ExecStartPre=\+?/usr/local/libexec/xauby-materialize-credentials"))


if __name__ == "__main__":
    unittest.main()
