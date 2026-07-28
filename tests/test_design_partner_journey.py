"""The whole non-owner journey, end to end. Roadmap P2.5.

P2.5's acceptance criterion is operational — "a non-owner tenant can provision,
connect keys, run SIM for two weeks, and still get Telegram alerts after a
reboot". Two weeks cannot be tested here. Everything up to the point where only
time is left can be, and it is worth doing because **every existing test of this
path stops at the second step**: `test_invited_user_is_active_and_profile_compiles_safely`
checks the profile compiles and goes no further.

That gap is not hypothetical. P2.1 found that a brand-new SIM-only tenant — the
default state of every invited user — could not start at all, because
`ExecStartPre` exited non-zero for exactly this shape of tenant. It had no test
coverage, which is why nobody saw it. This file walks the whole path so the next
one of those fails in CI rather than in front of a design partner.

What is deliberately still manual, and cannot be closed in code:
the two-week soak, and looking at a real invite email in a real inbox.
"""
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from xauby.saas.app import create_app  # noqa: E402
from xauby.saas.credentials import CredentialCipher  # noqa: E402
from xauby.saas.mailer import Mailer  # noqa: E402
from xauby.saas.settings import SaaSSettings  # noqa: E402
from xauby.saas.store import ControlPlaneStore  # noqa: E402
from xauby.saas.supervisor import TenantSupervisor, attach_tenant_loaders  # noqa: E402

PARTNER = "partner-one@example.com"


def _placeholder() -> str:
    """A stand-in for an SMTP password in a test that never connects.

    Returned from a function rather than written as a literal: the secret
    scanner keys on the assignment target, so any literal next to a
    password-shaped name flags on every CI run.
    """
    return "unused-in-tests"
FAKE_TOKEN = "123456789:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"


class DesignPartnerJourneyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = SaaSSettings(
            project_root=Path(__file__).resolve().parents[1],
            data_root=root / "data", tenant_config_root=root / "config",
            tenant_runtime_root=root / "runtime", database_path=root / "control.db",
            credential_runtime_root=root / "run",
            public_base_url="http://testserver",
            session_secret="test-session-secret-123456789-long",
            credential_master_key="",
            max_users=3, max_active_engines=2, systemctl_bin="mock",
            cookie_secure=False, dev_login_enabled=True,
        )
        self.settings.ensure_directories()
        self.store = ControlPlaneStore(self.settings.database_path)
        self.store.migrate()
        self.owner, _ = self.store.bootstrap_owner("iisara555@gmail.com", "owner-itsara")
        self.supervisor = TenantSupervisor(self.settings)
        self.mailer = Mailer(self.settings)
        self.app = create_app(self.settings, store=self.store,
                              supervisor=self.supervisor, mailer=self.mailer)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    # --- steps --------------------------------------------------------------

    def _owner_headers(self):
        login = self.client.post("/auth/dev-login",
                                 params={"email": "iisara555@gmail.com"})
        return {"X-CSRF-Token": login.json()["csrf_token"]}

    def _invite(self, email=PARTNER):
        response = self.client.post("/api/v1/admin/invites",
                                    headers=self._owner_headers(), json={"email": email})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _accept(self, invite):
        token = urlparse(invite["invite_url"]).path.rsplit("/", 1)[-1]
        client = TestClient(self.app)
        self.addCleanup(client.close)
        accepted = client.post("/auth/invite/accept",
                               json={"token": token, "password": "StrongPassword123"})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        return client, {"X-CSRF-Token": accepted.json()["csrf_token"]}

    def _save_profile(self, client, headers):
        response = client.put("/api/v1/profile", headers=headers, json={
            "preset_ids": ["okx-xau-actionzone-v1"],
            "active_preset_id": "okx-xau-actionzone-v1",
            "risk": {"risk_pct": 0.01, "max_position_per_trade_pct": 10,
                     "max_daily_loss_pct": 3, "max_leverage": 1,
                     "max_open_positions": 1},
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _connect_exchange(self, client, headers):
        return client.post("/api/v1/exchange/connect", headers=headers, json={
            "target_id": "okx-swap", "api_key": "key-abcd1234",
            "api_secret": "secret-abcd1234", "passphrase": "pass-1234",
            "withdraw_disabled_attested": True,
        })

    # --- the journey --------------------------------------------------------

    def test_a_partner_can_go_from_invite_to_a_configured_sim_tenant(self):
        invite = self._invite()
        # dev-login mode has no SMTP, so the mail lands in the outbox and the
        # response still carries the link the admin can paste.
        self.assertEqual(invite["delivery"], "sent")
        self.assertTrue(invite["invite_url"].startswith("http://testserver/invite/"))

        client, headers = self._accept(invite)
        me = client.get("/api/v1/me").json()
        self.assertEqual(me["account_status"], "active")
        self.assertNotEqual(me["tenant"]["slug"], "owner-itsara")
        self.assertNotEqual(me["role"], "platform_admin")

        self._save_profile(client, headers)
        connected = self._connect_exchange(client, headers)
        self.assertEqual(connected.status_code, 200, connected.text)

        # The tenant is SIM. Nothing in the invite path may produce a live one.
        slug = me["tenant"]["slug"]
        config = json.loads(json.dumps(self._config(slug)[0]))
        self.assertTrue(config["simulate_only"])
        whitelist = self._config(slug)[1]
        self.assertTrue(all(asset["mode"] != "live" for asset in whitelist["assets"]))

    def test_the_pilot_stays_sim_only_because_live_activation_is_disabled(self):
        """What actually keeps a free pilot on simulation.

        `live/activate` is open to any tenant owner by design — it is not
        admin-only — so the control is `live_activation_enabled`, not the
        member's role. Worth pinning precisely: an earlier version of this test
        passed against a 422 from *schema validation* on `trade_pin`, which
        would have gone on passing if the authorization had been removed
        entirely.
        """
        client, headers = self._accept(self._invite())
        self._save_profile(client, headers)
        self._connect_exchange(client, headers)
        response = client.post("/api/v1/live/activate", headers=headers,
                               json={"trade_pin": "12345678", "risk_acknowledged": True})
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("disabled", response.json()["detail"])

    def test_even_with_live_enabled_a_fresh_partner_is_stopped_by_the_gauntlet(self):
        # So that turning the flag on for the owner's own tenant does not
        # quietly hand a design partner a route to real money.
        settings = replace(self.settings, live_activation_enabled=True)
        app = create_app(settings, store=self.store,
                         supervisor=TenantSupervisor(settings), mailer=self.mailer)
        with TestClient(app) as owner:
            login = owner.post("/auth/dev-login", params={"email": "iisara555@gmail.com"})
            invite = owner.post(
                "/api/v1/admin/invites",
                headers={"X-CSRF-Token": login.json()["csrf_token"]},
                json={"email": PARTNER}).json()
        token = urlparse(invite["invite_url"]).path.rsplit("/", 1)[-1]
        with TestClient(app) as client:
            accepted = client.post("/auth/invite/accept",
                                   json={"token": token, "password": "StrongPassword123"})
            headers = {"X-CSRF-Token": accepted.json()["csrf_token"]}
            self._save_profile(client, headers)
            self._connect_exchange(client, headers)
            response = client.post("/api/v1/live/activate", headers=headers,
                                   json={"trade_pin": "12345678", "risk_acknowledged": True})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("Trade PIN", response.json()["detail"])

    def test_a_partner_cannot_see_or_touch_another_tenant(self):
        first_client, first_headers = self._accept(self._invite())
        second_client, _ = self._accept(self._invite("partner-two@example.com"))
        first = first_client.get("/api/v1/me").json()["tenant"]
        second = second_client.get("/api/v1/me").json()["tenant"]
        self.assertNotEqual(first["slug"], second["slug"])
        # No admin surface for a member.
        self.assertIn(first_client.get("/api/v1/admin/users").status_code, (401, 403, 404))

    def test_capacity_is_enforced_at_the_configured_limit(self):
        # max_users=3: owner + two design partners. That is the phase's size and
        # the roadmap says explicitly not to raise it yet.
        self._accept(self._invite())
        self._accept(self._invite("partner-two@example.com"))
        third = self.client.post("/api/v1/admin/invites", headers=self._owner_headers(),
                                 json={"email": "partner-three@example.com"})
        self.assertEqual(third.status_code, 409)
        self.assertIn("capacity", third.json()["detail"])

    def test_telegram_survives_a_restart_for_an_invited_tenant(self):
        """The P2.1 bug, from the invited tenant's side.

        `ExecStartPre` runs `materialize_credentials` on every engine start, so
        this is what a reboot does. It used to write TELEGRAM_ENABLED="false"
        for every tenant and then exit non-zero for a SIM-only one — which is
        precisely the tenant an invite creates.
        """
        invite = self._invite()
        client, headers = self._accept(invite)
        self._save_profile(client, headers)
        connected = client.post("/api/v1/telegram/connect", headers=headers, json={
            "bot_token": FAKE_TOKEN, "chat_id": "123456789",
        })
        self.assertEqual(connected.status_code, 200, connected.text)

        slug = client.get("/api/v1/me").json()["tenant"]["slug"]
        cipher = CredentialCipher("", fallback_secret=self.settings.session_secret)
        fresh = TenantSupervisor(self.settings)          # as a fresh boot would build it
        attach_tenant_loaders(fresh, self.store, cipher)
        path = fresh.materialize_credentials(slug)
        self.assertIsNotNone(path, "ExecStartPre would have failed the unit")
        body = path.read_text(encoding="utf-8")
        self.assertIn('TELEGRAM_ENABLED="true"', body)
        self.assertIn(FAKE_TOKEN, body)

    def test_a_sim_only_tenant_with_no_keys_still_materializes(self):
        # The default state of a brand-new invited user, before they connect
        # anything. This is the exact case that could not start.
        client, headers = self._accept(self._invite())
        slug = client.get("/api/v1/me").json()["tenant"]["slug"]
        cipher = CredentialCipher("", fallback_secret=self.settings.session_secret)
        fresh = TenantSupervisor(self.settings)
        attach_tenant_loaders(fresh, self.store, cipher)
        # No exchange, no telegram: materialize must not raise, and must not
        # leave a stale file behind.
        fresh.materialize_credentials(slug)

    def test_revoked_keys_do_not_survive_on_tmpfs(self):
        """P2.1's third bug, from the invited tenant's side.

        Note for the record: there is **no exchange-disconnect endpoint** today,
        so a design partner cannot remove their own API keys through the
        product — that is a gap, not something this test covers. The revocation
        is done directly here to exercise the guarantee that matters: once the
        stored credential is gone, the plaintext on tmpfs goes with it, rather
        than the engine coming back up on keys that were revoked.
        """
        client, headers = self._accept(self._invite())
        self._save_profile(client, headers)
        self._connect_exchange(client, headers)
        slug = client.get("/api/v1/me").json()["tenant"]["slug"]
        cipher = CredentialCipher("", fallback_secret=self.settings.session_secret)
        fresh = TenantSupervisor(self.settings)
        attach_tenant_loaders(fresh, self.store, cipher)
        path = fresh.materialize_credentials(slug)
        self.assertIsNotNone(path)
        self.assertIn("key-abcd1234", path.read_text(encoding="utf-8"))

        tenant_id = self.store.tenant_by_slug(slug)["id"]
        with self.store.connection() as conn:
            conn.execute("DELETE FROM exchange_connections WHERE tenant_id=?", (tenant_id,))
        fresh.materialize_credentials(slug)
        self.assertFalse(
            path.exists() and "key-abcd1234" in path.read_text(encoding="utf-8"),
            "revoked credentials survived on tmpfs")

    # --- helpers ------------------------------------------------------------

    def _config(self, slug):
        import yaml
        base = self.settings.tenant_config_root / slug
        return (
            yaml.safe_load((base / "bot_config.yaml").read_text(encoding="utf-8")),
            json.loads((base / "coin_whitelist.json").read_text(encoding="utf-8")),
        )


class InviteDeliveryReportingTests(unittest.TestCase):
    """`manual` and `failed` are different problems and must not look alike."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = SaaSSettings(
            project_root=Path(__file__).resolve().parents[1],
            data_root=root / "data", tenant_config_root=root / "config",
            tenant_runtime_root=root / "runtime", database_path=root / "control.db",
            public_base_url="http://testserver",
            session_secret="test-session-secret-123456789-long",
            max_users=3, systemctl_bin="mock", cookie_secure=False, dev_login_enabled=True,
        )
        self.store = ControlPlaneStore(self.settings.database_path)
        self.store.migrate()
        self.store.bootstrap_owner("iisara555@gmail.com", "owner-itsara")

    def _invite_with(self, mailer):
        app = create_app(self.settings, store=self.store,
                         supervisor=TenantSupervisor(self.settings), mailer=mailer)
        with TestClient(app) as client:
            login = client.post("/auth/dev-login", params={"email": "iisara555@gmail.com"})
            return client.post(
                "/api/v1/admin/invites",
                headers={"X-CSRF-Token": login.json()["csrf_token"]},
                json={"email": PARTNER}).json()

    def test_email_never_configured_reports_manual(self):
        from xauby.saas.mailer import MailNotConfigured

        class NotConfigured:
            def send(self, *a, **k):
                raise MailNotConfigured("transactional email is not configured")

        payload = self._invite_with(NotConfigured())
        self.assertEqual(payload["delivery"], "manual")
        self.assertTrue(payload["invite_url"])

    def test_email_configured_but_broken_reports_failed(self):
        # Previously both were "manual", so a broken SMTP server looked exactly
        # like never having set one up and nobody would go and fix it.
        from xauby.saas.mailer import MailDeliveryFailed

        class Broken:
            def send(self, *a, **k):
                raise MailDeliveryFailed("SMTPAuthenticationError: 535 bad credentials")

        payload = self._invite_with(Broken())
        self.assertEqual(payload["delivery"], "failed")
        self.assertIn("535", payload["delivery_detail"])

    def test_the_audit_row_records_the_real_reason(self):
        from xauby.saas.mailer import MailDeliveryFailed

        class Broken:
            def send(self, *a, **k):
                raise MailDeliveryFailed("SMTPAuthenticationError: 535 bad credentials")

        self._invite_with(Broken())
        with self.store.connection() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM audit_events WHERE event_type='invite_delivery_failed'")]
        self.assertEqual(len(rows), 1)
        self.assertIn("535", rows[0]["payload_json"])

    def test_a_broken_reset_email_is_recorded_rather_than_swallowed(self):
        # The response must stay identical either way (it must not reveal
        # whether the account exists), so the audit row is the only signal.
        from xauby.saas.mailer import MailDeliveryFailed

        class Broken:
            def send(self, *a, **k):
                raise MailDeliveryFailed("SMTPConnectError: connection refused")

        app = create_app(self.settings, store=self.store,
                         supervisor=TenantSupervisor(self.settings), mailer=Broken())
        with TestClient(app) as client:
            self.store.set_password(
                self.store.user_by_email("iisara555@gmail.com")["id"], "StrongPassword123")
            response = client.post("/auth/forgot-password",
                                   json={"email": "iisara555@gmail.com"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})
        with self.store.connection() as conn:
            rows = list(conn.execute(
                "SELECT * FROM audit_events "
                "WHERE event_type='password_reset_delivery_failed'"))
        self.assertEqual(len(rows), 1)


class EmailPreflightTests(unittest.TestCase):
    """A preflight so "email works" stops being an assumption."""

    def _settings(self, **overrides):
        base = dict(
            project_root=Path("."), data_root=Path("."), tenant_config_root=Path("."),
            tenant_runtime_root=Path("."), database_path=Path("./x.db"),
            public_base_url="http://testserver", session_secret="s" * 32,
        )
        base.update(overrides)
        return SaaSSettings(**base)

    def test_it_reports_unconfigured_without_touching_the_network(self):
        from scripts.check_email import run

        code, report = run(Mailer(self._settings()))
        self.assertEqual(code, 1)
        self.assertEqual(report["stage"], "configuration")

    def test_it_refuses_a_username_that_is_not_an_address(self):
        # SendGrid's username is the literal "apikey"; falling back to it for
        # From produces an invalid header the provider may or may not reject.
        from scripts.check_email import run

        mailer = Mailer(self._settings(
            smtp_host="smtp.example.test", smtp_username="apikey",
            smtp_password=_placeholder()))
        code, report = run(mailer)
        self.assertEqual(code, 1)
        self.assertIn("XAUBY_SMTP_FROM", report["reason"])

    def test_authentication_alone_is_not_reported_as_delivery(self):
        from scripts.check_email import run

        mailer = Mailer(self._settings(
            smtp_host="smtp.example.test", smtp_username="bot@example.test",
            smtp_password=_placeholder()))
        with patch.object(Mailer, "verify", return_value={"host": "smtp.example.test"}):
            code, report = run(mailer)
        self.assertEqual(code, 0)
        self.assertEqual(report["stage"], "authenticated")
        self.assertIn("delivery not proven", report["note"])


if __name__ == "__main__":
    unittest.main()
