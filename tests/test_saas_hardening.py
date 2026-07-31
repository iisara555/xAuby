"""Regression tests for the control plane's abuse and durability guards.

Covers the four defects found in the 2026-07-31 architecture review:
  * rate limiting that could be bypassed with a client-supplied header
  * unbounded argon2 concurrency and unbounded request bodies, either of
    which could OOM the service inside its systemd MemoryMax
  * the audit hash chain forking under concurrent writes
"""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from xauby.saas import security
from xauby.saas.app import MAX_REQUEST_BODY_BYTES, create_app
from xauby.saas.settings import SaaSSettings
from xauby.saas.store import ControlPlaneStore
from xauby.saas.supervisor import TenantSupervisor

# The leading "test-" is what marks this as a fixture rather than a credential
# for scripts/scan_secrets.py, whose PLACEHOLDER_RE exempts test-prefixed
# values. Still has to satisfy the 12-char upper/lower/digit password policy.
PASSWORD = "test-Password-9"


class ControlPlaneHardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = SaaSSettings(
            project_root=Path(__file__).resolve().parents[1],
            data_root=root / "data",
            tenant_config_root=root / "config",
            tenant_runtime_root=root / "runtime",
            database_path=root / "control.db",
            public_base_url="http://testserver",
            session_secret="test-session-secret-that-is-long-enough",
            systemctl_bin="mock",
            cookie_secure=False,
            dev_login_enabled=True,
        )
        self.store = ControlPlaneStore(self.settings.database_path)
        self.store.migrate()
        user, _ = self.store.bootstrap_owner("owner@example.com", "owner-itsara")
        self.user_id = user["id"]
        self.store.set_password(self.user_id, PASSWORD)
        self.app = create_app(
            self.settings, store=self.store, supervisor=TenantSupervisor(self.settings)
        )
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def _login(self, password: str, headers: dict[str, str] | None = None):
        return self.client.post(
            "/auth/login",
            json={"email": "owner@example.com", "password": password},
            headers=headers or {},
        )

    # --- C1: brute-force protection must not depend on a client header ---

    def _exhaust_account_attempts(self) -> None:
        """Fail enough logins to trip the account lock.

        Driven through the store rather than the HTTP route on purpose: the
        per-address throttle would answer 429 first and stop the account
        counter advancing. An attacker who can vary the apparent client
        address never trips that throttle, which is precisely the case this
        lockout exists to cover.
        """
        for _ in range(ControlPlaneStore.LOGIN_MAX_FAILURES):
            self.assertIsNone(
                self.store.authenticate_password("owner@example.com", "Wrong-Password-1")
            )

    def test_account_lock_holds_when_the_client_address_cannot_be_trusted(self):
        self._exhaust_account_attempts()
        self.assertGreater(self.store.login_retry_after("owner@example.com"), 0)

        # The account, not the caller, is what is locked: even the correct
        # password is refused, and from an address the throttle has never seen.
        blocked = self._login(PASSWORD, headers={"X-Forwarded-For": "203.0.113.9"})
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Retry-After", blocked.headers)
        self.assertIsNone(
            self.store.authenticate_password("owner@example.com", PASSWORD),
            "a locked account must not authenticate even with the right password",
        )

    def test_successful_login_clears_the_failure_counter(self):
        for _ in range(3):
            self.assertEqual(self._login("Wrong-Password-1").status_code, 401)
        self.assertEqual(self._login(PASSWORD).status_code, 200)
        self.assertEqual(
            self.store.user_by_email("owner@example.com")["login_failures"], 0
        )

    def test_password_reset_releases_a_locked_account(self):
        self._exhaust_account_attempts()
        self.assertGreater(self.store.login_retry_after("owner@example.com"), 0)
        self.store.set_password(self.user_id, "Another-Good-7")
        self.assertEqual(self.store.login_retry_after("owner@example.com"), 0)
        self.assertIsNotNone(
            self.store.authenticate_password("owner@example.com", "Another-Good-7")
        )

    # --- C2: bounded memory ---

    def test_password_hashing_is_bounded_to_two_concurrent_calls(self):
        """argon2 allocates 64 MiB a call; unbounded concurrency is an OOM."""
        observed: list[int] = []
        live = 0
        guard = threading.Lock()
        release = threading.Event()

        def slow_verify(encoded: str, pin: str) -> bool:
            nonlocal live
            with guard:
                live += 1
                observed.append(live)
            release.wait(timeout=2.0)
            with guard:
                live -= 1
            return True

        with mock.patch.object(security, "_verify_trade_pin_unguarded", slow_verify):
            threads = [
                threading.Thread(target=security.verify_password, args=("$argon2id$x", "pw"))
                for _ in range(8)
            ]
            for thread in threads:
                thread.start()
            # Give the pool time to pile up behind the semaphore, then drain.
            threading.Event().wait(0.3)
            release.set()
            for thread in threads:
                thread.join(timeout=5.0)

        self.assertEqual(len(observed), 8)
        self.assertLessEqual(max(observed), security._HASH_CONCURRENCY)

    def test_oversized_body_is_rejected_before_it_is_buffered(self):
        oversized = "x" * (MAX_REQUEST_BODY_BYTES + 1024)
        response = self.client.post(
            "/auth/login",
            content=f'{{"email":"a@b.co","password":"{oversized}"}}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["detail"], "request body is too large")

    def test_streamed_body_without_content_length_is_also_capped(self):
        """The realistic shape: the Next.js proxy drops content-length.

        It forwards request.body as a stream, so real traffic reaches uvicorn
        chunked with no declared size — a content-length check alone would
        never fire on the path that matters.
        """
        def chunks():
            yield b'{"email":"a@b.co","password":"'
            for _ in range((MAX_REQUEST_BODY_BYTES // 65536) + 4):
                yield b"x" * 65536
            yield b'"}'

        response = self.client.post(
            "/auth/login", content=chunks(), headers={"Content-Type": "application/json"}
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["detail"], "request body is too large")

    def test_normal_sized_body_still_passes_through(self):
        self.assertEqual(self._login(PASSWORD).status_code, 200)

    # --- R1 / R4: storage durability ---

    def test_concurrent_audits_do_not_fork_the_hash_chain(self):
        errors: list[Exception] = []

        def write(index: int) -> None:
            try:
                self.store.audit("concurrency_probe", payload={"n": index})
            except Exception as exc:  # noqa: BLE001 - the assertion is the point
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(i,)) for i in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30.0)

        self.assertEqual(errors, [], f"audit writes raised: {errors}")
        with self.store.connection() as conn:
            rows = conn.execute(
                "SELECT previous_hash, event_hash FROM audit_events ORDER BY seq"
            ).fetchall()
        previous = [row["previous_hash"] for row in rows]
        self.assertEqual(
            len(previous), len(set(previous)),
            "two events sharing a previous_hash means the chain forked",
        )
        # Every link must point at the row before it.
        for earlier, later in zip(rows, rows[1:]):
            self.assertEqual(later["previous_hash"], earlier["event_hash"])

    def test_database_stays_in_wal_after_the_pragma_moved_to_migrate(self):
        with self.store.connection() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(str(mode).lower(), "wal")

    def test_check_trade_pin_locks_out_after_repeated_failures(self):
        self.store.set_trade_pin(self.user_id, "12345678")
        for _ in range(5):
            ok, _ = self.store.check_trade_pin(self.user_id, "87654321")
            self.assertFalse(ok)
        ok, reason = self.store.check_trade_pin(self.user_id, "12345678")
        self.assertFalse(ok)
        self.assertIn("locked", reason.lower())


class ApiProxyContractTests(unittest.TestCase):
    """The Next.js proxy is the only thing between a browser and uvicorn.

    uvicorn runs with proxy_headers=True and trusts 127.0.0.1, so whatever the
    proxy forwards becomes request.client.host. This asserts the header list
    the TypeScript proxy strips stays in sync with what the server trusts.
    """

    def test_proxy_strips_every_client_authored_forwarding_header(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "Website" / "lib" / "server" / "api-proxy.ts"
        ).read_text(encoding="utf-8")
        for header in (
            "forwarded",
            "x-forwarded-for",
            "x-forwarded-host",
            "x-forwarded-port",
            "x-forwarded-proto",
            "x-real-ip",
        ):
            self.assertIn(f'"{header}"', source, f"{header} is not stripped by the proxy")
        self.assertIn("CLIENT_SPOOFABLE_HEADERS", source)
        self.assertIn("for (const header of CLIENT_SPOOFABLE_HEADERS) headers.delete(header);", source)


if __name__ == "__main__":
    unittest.main()
