from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from xauby.saas.security import (
    hash_password,
    hash_trade_pin,
    new_token,
    order_digest,
    token_hash,
    validate_tenant_slug,
    verify_password,
    verify_trade_pin,
)

logger = logging.getLogger("xauby.saas.store")


class ControlPlaneStore:
    # Deliberately above the per-address AttemptThrottle's 8, so ordinary users
    # meet the short IP throttle first. This one only engages for a caller who
    # can vary their apparent address — the case the IP throttle cannot see.
    LOGIN_MAX_FAILURES = 10
    LOGIN_LOCKOUT_SECONDS = 900

    class StrategyPoolConflict(RuntimeError):
        """Raised when a stale pool revision would overwrite newer state."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        # journal_mode is a persistent property of the database file and is set
        # once in migrate(); re-issuing it per connection only costs a lock
        # acquisition and can itself return SQLITE_BUSY. synchronous is
        # per-connection, and NORMAL is the correct pairing for WAL.
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    @contextmanager
    def connection(self):
        """Yield a transactional connection and always release its file handle."""
        conn = self.connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def migrate(self) -> None:
        with self.connection() as conn:
            # Persistent in the database file, so this is the one place it
            # belongs. journal_mode cannot be changed inside a transaction.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,
                    google_sub TEXT UNIQUE, role TEXT NOT NULL DEFAULT 'user',
                    display_name TEXT, avatar_ext TEXT,
                    avatar_version INTEGER NOT NULL DEFAULT 0,
                    password_hash TEXT, email_verified INTEGER NOT NULL DEFAULT 0,
                    account_status TEXT NOT NULL DEFAULT 'pending_approval',
                    totp_secret TEXT, totp_enabled INTEGER NOT NULL DEFAULT 0,
                    pending_totp_secret TEXT,
                    pending_email TEXT,
                    trade_pin_hash TEXT, pin_failures INTEGER NOT NULL DEFAULT 0,
                    pin_locked_until REAL NOT NULL DEFAULT 0,
                    login_failures INTEGER NOT NULL DEFAULT 0,
                    login_locked_until REAL NOT NULL DEFAULT 0,
                    original_email TEXT,
                    deleted_at REAL,
                    deleted_by_user_id TEXT,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tenants (
                    id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE,
                    owner_user_id TEXT NOT NULL UNIQUE REFERENCES users(id),
                    status TEXT NOT NULL DEFAULT 'queued',
                    live_status TEXT NOT NULL DEFAULT 'not_requested',
                    exchange_id TEXT NOT NULL DEFAULT 'okx',
                    market_type TEXT NOT NULL DEFAULT 'swap',
                    deleted_at REAL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    csrf_token TEXT NOT NULL, mfa_verified INTEGER NOT NULL DEFAULT 0,
                    expires_at REAL NOT NULL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS live_approvals (
                    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id),
                    requested_by TEXT NOT NULL REFERENCES users(id), approved_by TEXT,
                    status TEXT NOT NULL, risk_json TEXT NOT NULL DEFAULT '{}',
                    requested_at REAL NOT NULL, decided_at REAL
                );
                CREATE TABLE IF NOT EXISTS exchange_connections (
                    tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),
                    exchange_id TEXT NOT NULL, key_last4 TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'stored',
                    capabilities_json TEXT NOT NULL DEFAULT '{}', updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS telegram_connections (
                    tenant_id TEXT PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
                    chat_id TEXT NOT NULL, token_last4 TEXT NOT NULL,
                    bot_username TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'stored',
                    enabled INTEGER NOT NULL DEFAULT 1, credential_blob TEXT,
                    key_version INTEGER NOT NULL DEFAULT 1,
                    tested_at REAL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS config_revisions (
                    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id),
                    revision INTEGER NOT NULL, author_user_id TEXT NOT NULL REFERENCES users(id),
                    config_json TEXT NOT NULL, created_at REAL NOT NULL,
                    UNIQUE(tenant_id, revision)
                );
                CREATE TABLE IF NOT EXISTS order_challenges (
                    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id),
                    user_id TEXT NOT NULL REFERENCES users(id), payload_json TEXT NOT NULL,
                    digest TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
                    expires_at REAL NOT NULL, command_id TEXT, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT, user_id TEXT,
                    event_type TEXT NOT NULL, payload_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL UNIQUE,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_tokens (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    purpose TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}',
                    expires_at REAL NOT NULL, used_at REAL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS recovery_codes (
                    code_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    used_at REAL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trading_profiles (
                    tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),
                    profile_json TEXT NOT NULL, updated_by TEXT NOT NULL REFERENCES users(id),
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategy_pools (
                    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                    symbol TEXT NOT NULL,
                    pool_json TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    updated_by TEXT NOT NULL REFERENCES users(id),
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (tenant_id, symbol)
                );
                CREATE TABLE IF NOT EXISTS invitations (
                    id TEXT PRIMARY KEY, email TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    invited_by TEXT NOT NULL REFERENCES users(id),
                    status TEXT NOT NULL DEFAULT 'pending',
                    expires_at REAL NOT NULL, accepted_at REAL, created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_tenants_status ON tenants(status);
                CREATE INDEX IF NOT EXISTS ix_challenges_tenant ON order_challenges(tenant_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS ix_audit_tenant ON audit_events(tenant_id, seq DESC);
                CREATE INDEX IF NOT EXISTS ix_auth_tokens_user ON auth_tokens(user_id,purpose);
                CREATE INDEX IF NOT EXISTS ix_invitations_email ON invitations(email,status);
                """
            )
            # Additive migration for control databases created by the original pilot.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
            additions = {
                "password_hash": "TEXT", "email_verified": "INTEGER NOT NULL DEFAULT 0",
                "account_status": "TEXT NOT NULL DEFAULT 'active'", "totp_secret": "TEXT",
                "totp_enabled": "INTEGER NOT NULL DEFAULT 0", "pending_email": "TEXT",
                "display_name": "TEXT", "avatar_ext": "TEXT",
                "avatar_version": "INTEGER NOT NULL DEFAULT 0",
                "pending_totp_secret": "TEXT",
                "login_failures": "INTEGER NOT NULL DEFAULT 0",
                "login_locked_until": "REAL NOT NULL DEFAULT 0",
                "original_email": "TEXT",
                "deleted_at": "REAL",
                "deleted_by_user_id": "TEXT",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE users ADD COLUMN {name} {declaration}")
            session_columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
            if "mfa_verified" not in session_columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN mfa_verified INTEGER NOT NULL DEFAULT 0")
            connection_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(exchange_connections)")
            }
            connection_additions = {
                "target_id": "TEXT NOT NULL DEFAULT 'okx-swap'",
                "credential_blob": "TEXT",
                "key_version": "INTEGER NOT NULL DEFAULT 1",
                "tested_at": "REAL",
            }
            for name, declaration in connection_additions.items():
                if name not in connection_columns:
                    conn.execute(
                        f"ALTER TABLE exchange_connections ADD COLUMN {name} {declaration}"
                    )
            tenant_columns = {row[1] for row in conn.execute("PRAGMA table_info(tenants)")}
            if "deleted_at" not in tenant_columns:
                conn.execute("ALTER TABLE tenants ADD COLUMN deleted_at REAL")
            pool_columns = {row[1] for row in conn.execute("PRAGMA table_info(strategy_pools)")}
            if "revision" not in pool_columns:
                conn.execute(
                    "ALTER TABLE strategy_pools ADD COLUMN revision INTEGER NOT NULL DEFAULT 0"
                )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def audit(self, event_type: str, *, tenant_id: str | None = None,
              user_id: str | None = None, payload: dict[str, Any] | None = None) -> None:
        created = time.time()
        payload_json = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"))
        with self.connection() as conn:
            # The chain read below decides what this row commits, so the write
            # lock has to be held from the start. Under a DEFERRED transaction
            # in WAL, a concurrent commit makes the upgrade to write fail with
            # SQLITE_BUSY_SNAPSHOT, which busy_timeout cannot retry away — and
            # two audits reading the same previous_hash would fork the
            # tamper-evident chain silently.
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                "SELECT event_hash FROM audit_events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            previous_hash = str(previous[0]) if previous else "GENESIS"
            digest = order_digest(
                {
                    "previous": previous_hash, "tenant": tenant_id, "user": user_id,
                    "event": event_type, "payload": payload_json, "created": created,
                }
            )
            conn.execute(
                "INSERT INTO audit_events (tenant_id,user_id,event_type,payload_json,previous_hash,event_hash,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (tenant_id, user_id, event_type, payload_json, previous_hash, digest, created),
            )

    def bootstrap_owner(self, email: str, slug: str) -> tuple[dict[str, Any], dict[str, Any]]:
        normalized_email = str(email or "").strip().lower()
        tenant_slug = validate_tenant_slug(slug)
        if "@" not in normalized_email:
            raise ValueError("valid owner email is required")
        now = time.time()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            user = conn.execute("SELECT * FROM users WHERE email=?", (normalized_email,)).fetchone()
            if user is None:
                user_id = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO users (id,email,role,email_verified,account_status,created_at) "
                    "VALUES (?,?,'platform_admin',1,'active',?)",
                    (user_id, normalized_email, now),
                )
            else:
                user_id = user["id"]
                conn.execute(
                    "UPDATE users SET role='platform_admin',email_verified=1,account_status='active' WHERE id=?",
                    (user_id,),
                )
            tenant = conn.execute("SELECT * FROM tenants WHERE owner_user_id=?", (user_id,)).fetchone()
            if tenant is None:
                tenant_id = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO tenants (id,slug,owner_user_id,status,created_at) "
                    "VALUES (?,?,?,'stopped',?)",
                    (tenant_id, tenant_slug, user_id, now),
                )
            user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            tenant = conn.execute("SELECT * FROM tenants WHERE owner_user_id=?", (user_id,)).fetchone()
        self.audit("owner_bootstrapped", tenant_id=tenant["id"], user_id=user_id)
        return dict(user), dict(tenant)

    def upsert_google_user(self, email: str, google_sub: str) -> tuple[dict[str, Any], bool]:
        normalized_email = str(email or "").strip().lower()
        if "@" not in normalized_email or not google_sub:
            raise ValueError("verified Google identity is required")
        created = False
        now = time.time()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM users WHERE email=?", (normalized_email,)).fetchone()
            if row is None:
                user_id = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO users (id,email,google_sub,email_verified,account_status,created_at) "
                    "VALUES (?,?,?,1,'pending_approval',?)",
                    (user_id, normalized_email, google_sub, now),
                )
                created = True
            else:
                user_id = row["id"]
                if row["google_sub"] and row["google_sub"] != google_sub:
                    raise ValueError("email is already linked to another Google identity")
                conn.execute("UPDATE users SET google_sub=? WHERE id=?", (google_sub, user_id))
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row), created

    def user_by_email(self, email: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            return self._row(conn.execute(
                "SELECT * FROM users WHERE email=? AND deleted_at IS NULL",
                (str(email).strip().lower(),),
            ).fetchone())

    def user_by_id(self, user_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            return self._row(conn.execute(
                "SELECT * FROM users WHERE id=? AND deleted_at IS NULL", (user_id,)
            ).fetchone())

    def user_count(self) -> int:
        with self.connection() as conn:
            return int(conn.execute(
                "SELECT count(*) FROM users WHERE deleted_at IS NULL"
            ).fetchone()[0])

    def update_user_appearance(
        self,
        user_id: str,
        *,
        display_name: str,
        avatar_ext: str | None,
        avatar_changed: bool,
    ) -> dict[str, Any]:
        with self.connection() as conn:
            if avatar_changed:
                conn.execute(
                    "UPDATE users SET display_name=?,avatar_ext=?,"
                    "avatar_version=avatar_version+1 WHERE id=?",
                    (display_name, avatar_ext, user_id),
                )
            else:
                conn.execute(
                    "UPDATE users SET display_name=? WHERE id=?",
                    (display_name, user_id),
                )
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise KeyError("user not found")
        self.audit(
            "profile_appearance_updated",
            user_id=user_id,
            payload={"avatar_changed": avatar_changed},
        )
        return dict(row)

    def create_invite(
        self, email: str, invited_by: str, *, ttl_seconds: int = 604800
    ) -> tuple[dict[str, Any], str]:
        normalized = str(email or "").strip().lower()
        if "@" not in normalized or len(normalized) > 254:
            raise ValueError("valid email is required")
        if self.user_by_email(normalized):
            raise ValueError("email is already registered")
        token = new_token()
        invite_id = uuid.uuid4().hex
        now = time.time()
        with self.connection() as conn:
            conn.execute(
                "UPDATE invitations SET status='superseded' "
                "WHERE email=? AND status='pending'",
                (normalized,),
            )
            conn.execute(
                "INSERT INTO invitations "
                "(id,email,token_hash,invited_by,status,expires_at,created_at) "
                "VALUES (?,?,?,?,'pending',?,?)",
                (invite_id, normalized, token_hash(token), invited_by, now + ttl_seconds, now),
            )
            row = conn.execute("SELECT * FROM invitations WHERE id=?", (invite_id,)).fetchone()
        self.audit("user_invited", user_id=invited_by, payload={"email": normalized})
        return dict(row), token

    def invite_for_token(self, token: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT id,email,status,expires_at,created_at FROM invitations "
                "WHERE token_hash=?",
                (token_hash(token),),
            ).fetchone()
        invite = self._row(row)
        if not invite or invite["status"] != "pending" or float(invite["expires_at"]) < time.time():
            return None
        return invite

    def accept_invite(
        self, token: str, *, password: str | None = None, google_sub: str | None = None
    ) -> dict[str, Any]:
        encoded = hash_password(password) if password else None
        now = time.time()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            invite = conn.execute(
                "SELECT * FROM invitations WHERE token_hash=? AND status='pending' AND expires_at>=?",
                (token_hash(token), now),
            ).fetchone()
            if invite is None:
                raise ValueError("invitation is invalid or expired")
            existing = conn.execute("SELECT 1 FROM users WHERE email=?", (invite["email"],)).fetchone()
            if existing:
                raise ValueError("email is already registered")
            if not encoded and not google_sub:
                raise ValueError("password or verified Google identity is required")
            user_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO users "
                "(id,email,google_sub,password_hash,email_verified,account_status,created_at) "
                "VALUES (?,?,?,?,1,'active',?)",
                (user_id, invite["email"], google_sub, encoded, now),
            )
            conn.execute(
                "UPDATE invitations SET status='accepted',accepted_at=? WHERE id=?",
                (now, invite["id"]),
            )
            user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        self.audit("invite_accepted", user_id=user_id)
        return dict(user)

    def create_password_user(self, email: str, password: str) -> tuple[dict[str, Any], str]:
        normalized = str(email or "").strip().lower()
        if "@" not in normalized or len(normalized) > 254:
            raise ValueError("valid email is required")
        encoded = hash_password(password)
        if self.user_by_email(normalized):
            raise ValueError("email is already registered")
        user_id = uuid.uuid4().hex
        now = time.time()
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO users (id,email,password_hash,account_status,created_at) "
                "VALUES (?,?,?,'pending_email',?)",
                (user_id, normalized, encoded, now),
            )
        token = self.create_auth_token(user_id, "verify_email", ttl_seconds=86400)
        self.audit("signup", user_id=user_id)
        return self.user_by_id(user_id) or {}, token

    def login_retry_after(self, email: str) -> int:
        """Seconds until this account may attempt a password login again.

        The in-process AttemptThrottle is keyed on the client address, which
        arrives from a reverse proxy and is therefore only as trustworthy as
        the proxy in front of it. This lockout lives with the account itself,
        so brute-force protection does not depend on the network layer being
        configured correctly. Mirrors the Trade PIN lockout in check_trade_pin.
        """
        user = self.user_by_email(email)
        if not user:
            return 0
        remaining = float(user.get("login_locked_until") or 0) - time.time()
        return max(0, int(remaining)) if remaining > 0 else 0

    def authenticate_password(self, email: str, password: str) -> dict[str, Any] | None:
        user = self.user_by_email(email)
        if not user or not user.get("password_hash"):
            return None
        if float(user.get("login_locked_until") or 0) > time.time():
            return None
        if not verify_password(str(user["password_hash"]), password):
            self._record_login_failure(str(user["id"]))
            return None
        self.clear_login_failures(str(user["id"]))
        return user

    def _record_login_failure(self, user_id: str) -> None:
        now = time.time()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT login_failures FROM users WHERE id=?", (user_id,)
            ).fetchone()
            if row is None:
                return
            failures = int(row["login_failures"] or 0) + 1
            locked_until = now + self.LOGIN_LOCKOUT_SECONDS if failures >= self.LOGIN_MAX_FAILURES else 0
            conn.execute(
                "UPDATE users SET login_failures=?,login_locked_until=? WHERE id=?",
                (0 if locked_until else failures, locked_until, user_id),
            )

    def clear_login_failures(self, user_id: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE users SET login_failures=0,login_locked_until=0 WHERE id=?", (user_id,)
            )

    def create_auth_token(self, user_id: str, purpose: str, *, ttl_seconds: int,
                          payload: dict[str, Any] | None = None) -> str:
        token = new_token()
        now = time.time()
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO auth_tokens (token_hash,user_id,purpose,payload_json,expires_at,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (token_hash(token), user_id, purpose, json.dumps(payload or {}, sort_keys=True),
                 now + ttl_seconds, now),
            )
        return token

    def consume_auth_token(self, token: str, purpose: str) -> tuple[dict[str, Any], dict[str, Any]]:
        now = time.time()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM auth_tokens WHERE token_hash=? AND purpose=? AND used_at IS NULL AND expires_at>=?",
                (token_hash(token), purpose, now),
            ).fetchone()
            if row is None:
                raise ValueError("token is invalid or expired")
            conn.execute("UPDATE auth_tokens SET used_at=? WHERE token_hash=?", (now, row["token_hash"]))
            user = conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
        if user is None:
            raise ValueError("user no longer exists")
        return dict(user), json.loads(row["payload_json"] or "{}")

    def verify_email_token(self, token: str) -> dict[str, Any]:
        user, _ = self.consume_auth_token(token, "verify_email")
        with self.connection() as conn:
            conn.execute(
                "UPDATE users SET email_verified=1,account_status='pending_approval' WHERE id=?",
                (user["id"],),
            )
        self.audit("email_verified", user_id=user["id"])
        return self.user_by_id(user["id"]) or {}

    def set_password(self, user_id: str, password: str) -> None:
        encoded = hash_password(password)
        with self.connection() as conn:
            # Setting a new password clears the lockout: the credential being
            # guessed no longer exists, and a legitimate reset must not leave
            # the owner locked out of the account they just recovered.
            conn.execute(
                "UPDATE users SET password_hash=?,login_failures=0,login_locked_until=0 WHERE id=?",
                (encoded, user_id),
            )
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            conn.execute(
                "UPDATE auth_tokens SET used_at=? WHERE user_id=? AND purpose='password_reset' AND used_at IS NULL",
                (time.time(), user_id),
            )
        self.audit("password_changed", user_id=user_id)

    def set_account_status(self, user_id: str, status: str, admin_user_id: str) -> dict[str, Any]:
        if status not in {"active", "rejected", "suspended"}:
            raise ValueError("unsupported account status")
        with self.connection() as conn:
            conn.execute("UPDATE users SET account_status=? WHERE id=?", (status, user_id))
            if status != "active":
                conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        self.audit("account_status_changed", user_id=admin_user_id,
                   payload={"target_user_id": user_id, "status": status})
        return self.user_by_id(user_id) or {}

    def remove_pilot(self, user_id: str, admin_user_id: str) -> dict[str, Any]:
        """Revoke and tombstone a suspended pilot without breaking audit history.

        Foreign-keyed history stays intact, while the login identity, credentials,
        sessions, pending commands, and capacity slot are removed. Keeping the
        tenant slug reserved prevents a future invitation from inheriting the old
        tenant's on-disk workspace; ``ensure_tenant`` will allocate a suffixed slug.
        """
        now = time.time()
        tombstone_email = f"removed+{user_id}@deleted.invalid"
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            user = conn.execute(
                "SELECT * FROM users WHERE id=? AND deleted_at IS NULL", (user_id,)
            ).fetchone()
            if user is None:
                raise KeyError("user not found")
            if user["role"] == "platform_admin":
                raise ValueError("owner account cannot be removed")
            if user["account_status"] != "suspended":
                raise ValueError("pilot must be suspended before removal")
            tenant = conn.execute(
                "SELECT * FROM tenants WHERE owner_user_id=? AND deleted_at IS NULL",
                (user_id,),
            ).fetchone()

            conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM auth_tokens WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM recovery_codes WHERE user_id=?", (user_id,))
            conn.execute("DELETE FROM order_challenges WHERE user_id=?", (user_id,))
            conn.execute(
                "UPDATE invitations SET status='revoked' WHERE email=? AND status='pending'",
                (user["email"],),
            )
            if tenant is not None:
                conn.execute(
                    "DELETE FROM exchange_connections WHERE tenant_id=?", (tenant["id"],)
                )
                conn.execute(
                    "DELETE FROM telegram_connections WHERE tenant_id=?", (tenant["id"],)
                )
                conn.execute(
                    "DELETE FROM order_challenges WHERE tenant_id=?", (tenant["id"],)
                )
                conn.execute(
                    "UPDATE tenants SET status='removed',live_status='not_requested',deleted_at=? "
                    "WHERE id=?",
                    (now, tenant["id"]),
                )

            conn.execute(
                "UPDATE users SET original_email=email,email=?,google_sub=NULL,"
                "display_name=NULL,avatar_ext=NULL,password_hash=NULL,email_verified=0,"
                "account_status='removed',totp_secret=NULL,totp_enabled=0,"
                "pending_totp_secret=NULL,pending_email=NULL,trade_pin_hash=NULL,"
                "pin_failures=0,pin_locked_until=0,login_failures=0,login_locked_until=0,"
                "deleted_at=?,deleted_by_user_id=? WHERE id=?",
                (tombstone_email, now, admin_user_id, user_id),
            )
            result = {
                "user_id": user_id,
                "email": str(user["email"]),
                "tenant_id": str(tenant["id"]) if tenant is not None else None,
                "tenant_slug": str(tenant["slug"]) if tenant is not None else None,
                "deleted_at": now,
            }
        self.audit(
            "pilot_removed",
            tenant_id=str(tenant["id"]) if tenant is not None else None,
            user_id=admin_user_id,
            payload={"target_user_id": user_id},
        )
        return result

    def begin_email_change(self, user_id: str, new_email: str) -> str:
        normalized = str(new_email or "").strip().lower()
        if "@" not in normalized or self.user_by_email(normalized):
            raise ValueError("new email is invalid or already registered")
        with self.connection() as conn:
            conn.execute("UPDATE users SET pending_email=? WHERE id=?", (normalized, user_id))
        return self.create_auth_token(
            user_id, "change_email", ttl_seconds=3600, payload={"new_email": normalized}
        )

    def confirm_email_change(self, token: str) -> dict[str, Any]:
        user, payload = self.consume_auth_token(token, "change_email")
        new_email = str(payload.get("new_email") or "")
        if not new_email or user.get("pending_email") != new_email:
            raise ValueError("email change is no longer valid")
        with self.connection() as conn:
            conn.execute(
                "UPDATE users SET email=?,pending_email=NULL,email_verified=1 WHERE id=?",
                (new_email, user["id"]),
            )
            conn.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        self.audit("email_changed", user_id=user["id"])
        return self.user_by_id(user["id"]) or {}

    def set_totp_secret(self, user_id: str, secret: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE users SET pending_totp_secret=? WHERE id=?",
                (secret, user_id),
            )

    def enable_totp(self, user_id: str, recovery_codes: list[str]) -> None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT pending_totp_secret FROM users WHERE id=?",
                (user_id,),
            ).fetchone()
            if row is None or not row["pending_totp_secret"]:
                raise ValueError("TOTP setup is not pending")
            conn.execute(
                "UPDATE users SET totp_secret=pending_totp_secret,"
                "pending_totp_secret=NULL,totp_enabled=1 WHERE id=?",
                (user_id,),
            )
            conn.execute("DELETE FROM recovery_codes WHERE user_id=?", (user_id,))
            now = time.time()
            conn.executemany(
                "INSERT INTO recovery_codes (code_hash,user_id,created_at) VALUES (?,?,?)",
                [(token_hash(code), user_id, now) for code in recovery_codes],
            )
        self.audit("totp_enabled", user_id=user_id)

    def use_recovery_code(self, user_id: str, code: str) -> bool:
        """Consume one unused TOTP recovery code; returns True when it matched."""
        normalized = str(code or "").strip().upper().replace("-", "").replace(" ", "")
        if not normalized:
            return False
        now = time.time()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT code_hash FROM recovery_codes "
                "WHERE user_id=? AND code_hash=? AND used_at IS NULL",
                (user_id, token_hash(normalized)),
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                "UPDATE recovery_codes SET used_at=? WHERE code_hash=?",
                (now, row["code_hash"]),
            )
        self.audit("recovery_code_used", user_id=user_id)
        return True

    def save_trading_profile(self, tenant_id: str, user_id: str, profile: dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO trading_profiles (tenant_id,profile_json,updated_by,updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(tenant_id) DO UPDATE SET profile_json=excluded.profile_json,"
                "updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                (tenant_id, json.dumps(profile, sort_keys=True), user_id, time.time()),
            )
        self.audit("trading_profile_updated", tenant_id=tenant_id, user_id=user_id)

    def trading_profile(self, tenant_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT profile_json FROM trading_profiles WHERE tenant_id=?", (tenant_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_strategy_pool(self, tenant_id: str, user_id: str, pool: dict[str, Any]) -> dict[str, Any]:
        """Persist one pair arena with an optimistic-concurrency revision.

        The pool JSON is intentionally kept small, but a stale evaluator or
        browser must never overwrite a newer promotion.  Existing pilot rows
        start at revision zero and are upgraded on their first successful save.
        """
        symbol = str(pool.get("symbol") or "").strip().upper()
        if not symbol:
            raise ValueError("strategy pool symbol is required")
        try:
            expected_revision = max(0, int(pool.get("revision", 0) or 0))
        except (TypeError, ValueError):
            expected_revision = 0
        now = time.time()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT revision FROM strategy_pools WHERE tenant_id=? AND symbol=?",
                (tenant_id, symbol),
            ).fetchone()
            current_revision = int(row[0]) if row is not None else 0
            if row is None and expected_revision != 0:
                raise self.StrategyPoolConflict(
                    f"strategy pool {symbol} does not exist at revision {expected_revision}"
                )
            if row is not None and current_revision != expected_revision:
                raise self.StrategyPoolConflict(
                    f"strategy pool {symbol} changed (expected revision "
                    f"{expected_revision}, current {current_revision})"
                )
            new_revision = current_revision + 1
            stored = dict(pool)
            stored["revision"] = new_revision
            encoded = json.dumps(stored, sort_keys=True, separators=(",", ":"))
            if row is None:
                conn.execute(
                    "INSERT INTO strategy_pools "
                    "(tenant_id,symbol,pool_json,revision,updated_by,updated_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (tenant_id, symbol, encoded, new_revision, user_id, now),
                )
            else:
                cursor = conn.execute(
                    "UPDATE strategy_pools SET pool_json=?,revision=?,updated_by=?,updated_at=? "
                    "WHERE tenant_id=? AND symbol=? AND revision=?",
                    (encoded, new_revision, user_id, now, tenant_id, symbol, expected_revision),
                )
                if cursor.rowcount != 1:
                    raise self.StrategyPoolConflict("strategy pool update lost its revision race")
        # The pool row is already committed at this point.  A transient audit
        # database failure must not make the API report a failed write (and
        # trigger a rollback that cannot undo the committed row).  Keep the
        # state durable and leave an operator-visible trace instead.
        try:
            self.audit(
                "strategy_pool_updated",
                tenant_id=tenant_id,
                user_id=user_id,
                payload={"symbol": symbol, "champion_id": pool.get("champion_id")},
            )
        except Exception:
            logger.exception("strategy pool saved but audit append failed for %s", symbol)
        return json.loads(encoded)

    def strategy_pool(self, tenant_id: str, symbol: str) -> dict[str, Any] | None:
        normalized = str(symbol or "").strip().upper()
        with self.connection() as conn:
            row = conn.execute(
                "SELECT pool_json,revision FROM strategy_pools WHERE tenant_id=? AND symbol=?",
                (tenant_id, normalized),
            ).fetchone()
        if not row:
            return None
        pool = json.loads(row[0])
        if isinstance(pool, dict):
            pool["revision"] = int(row[1] or 0)
        return pool

    def strategy_pools(self, tenant_id: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT pool_json,revision FROM strategy_pools WHERE tenant_id=? ORDER BY symbol",
                (tenant_id,),
            ).fetchall()
        pools = []
        for row in rows:
            pool = json.loads(row[0])
            if isinstance(pool, dict):
                pool["revision"] = int(row[1] or 0)
            pools.append(pool)
        return pools

    def ensure_tenant(self, user_id: str, preferred_slug: str) -> tuple[dict[str, Any], bool]:
        with self.connection() as conn:
            existing = conn.execute("SELECT * FROM tenants WHERE owner_user_id=?", (user_id,)).fetchone()
            if existing:
                return dict(existing), False
            base = validate_tenant_slug(preferred_slug)
            slug = base
            suffix = 2
            while conn.execute("SELECT 1 FROM tenants WHERE slug=?", (slug,)).fetchone():
                tail = f"-{suffix}"
                slug = f"{base[:64-len(tail)]}{tail}"
                suffix += 1
            tenant_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO tenants (id,slug,owner_user_id,status,created_at) "
                "VALUES (?,?,?,'queued',?)",
                (tenant_id, slug, user_id, time.time()),
            )
            row = conn.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
        return dict(row), True

    def create_session(self, user_id: str, ttl_seconds: int = 604800,
                       *, mfa_verified: bool = False) -> tuple[str, str]:
        token, csrf = new_token(), new_token()
        now = time.time()
        with self.connection() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at<?", (now,))
            conn.execute(
                "INSERT INTO sessions (token_hash,user_id,csrf_token,mfa_verified,expires_at,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (token_hash(token), user_id, csrf, int(mfa_verified), now + ttl_seconds, now),
            )
        return token, csrf

    def session(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT u.*, s.csrf_token, s.mfa_verified, s.expires_at
                FROM sessions s JOIN users u ON u.id=s.user_id
                WHERE s.token_hash=? AND s.expires_at>=? AND u.deleted_at IS NULL
                """,
                (token_hash(token), time.time()),
            ).fetchone()
        return self._row(row)

    def mark_session_mfa(self, token: str) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE sessions SET mfa_verified=1 WHERE token_hash=?", (token_hash(token),))

    def revoke_session(self, token: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))

    def tenant_for_user(self, user_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            return self._row(conn.execute(
                "SELECT * FROM tenants WHERE owner_user_id=? AND deleted_at IS NULL", (user_id,)
            ).fetchone())

    def tenant_by_id(self, tenant_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            return self._row(conn.execute(
                "SELECT * FROM tenants WHERE id=? AND deleted_at IS NULL", (tenant_id,)
            ).fetchone())

    def tenant_by_slug(self, slug: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            return self._row(conn.execute(
                "SELECT * FROM tenants WHERE slug=? AND deleted_at IS NULL", (slug,)
            ).fetchone())

    def list_tenants(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT t.*,u.email AS owner_email FROM tenants t JOIN users u ON u.id=t.owner_user_id "
                "WHERE t.deleted_at IS NULL AND u.deleted_at IS NULL ORDER BY t.created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def list_users(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT id,email,role,email_verified,account_status,totp_enabled,created_at "
                "FROM users WHERE deleted_at IS NULL ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def active_count(self) -> int:
        with self.connection() as conn:
            return int(conn.execute(
                "SELECT count(*) FROM tenants WHERE status IN ('starting','running','degraded')"
            ).fetchone()[0])

    def reserve_engine_slot(self, tenant_id: str, maximum: int) -> bool:
        """Atomically reserve capacity before invoking the process supervisor."""
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            tenant = conn.execute(
                "SELECT status FROM tenants WHERE id=?", (tenant_id,)
            ).fetchone()
            if tenant is None:
                raise KeyError("tenant not found")
            if tenant["status"] in {"starting", "running", "degraded"}:
                return True
            active = int(conn.execute(
                "SELECT count(*) FROM tenants WHERE status IN ('starting','running','degraded')"
            ).fetchone()[0])
            if active >= max(1, int(maximum)):
                conn.execute("UPDATE tenants SET status='queued' WHERE id=?", (tenant_id,))
                return False
            conn.execute("UPDATE tenants SET status='starting' WHERE id=?", (tenant_id,))
            return True

    def update_tenant(self, tenant_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {"status", "live_status", "exchange_id", "market_type"}
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            raise ValueError("no supported tenant fields supplied")
        sql = ",".join(f"{key}=?" for key in values)
        with self.connection() as conn:
            conn.execute(f"UPDATE tenants SET {sql} WHERE id=?", (*values.values(), tenant_id))
            row = conn.execute("SELECT * FROM tenants WHERE id=?", (tenant_id,)).fetchone()
        if row is None:
            raise KeyError("tenant not found")
        return dict(row)

    def set_exchange_connection(self, tenant_id: str, exchange_id: str, key_last4: str,
                                *, target_id: str = "okx-swap", status: str = "stored",
                                capabilities: dict[str, Any] | None = None,
                                credential_blob: str | None = None,
                                key_version: int = 1) -> dict[str, Any]:
        if int(key_version) < 1:
            raise ValueError("credential key version must be positive")
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO exchange_connections
                (tenant_id,exchange_id,key_last4,status,capabilities_json,updated_at,
                 target_id,credential_blob,key_version,tested_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(tenant_id) DO UPDATE SET
                  exchange_id=excluded.exchange_id,key_last4=excluded.key_last4,
                  status=excluded.status,capabilities_json=excluded.capabilities_json,
                  updated_at=excluded.updated_at,target_id=excluded.target_id,
                  credential_blob=coalesce(excluded.credential_blob,exchange_connections.credential_blob),
                  key_version=CASE WHEN excluded.credential_blob IS NULL
                    THEN exchange_connections.key_version ELSE excluded.key_version END,
                  tested_at=excluded.tested_at
                """,
                (tenant_id, exchange_id, key_last4, status,
                 json.dumps(capabilities or {}, sort_keys=True), time.time(), target_id,
                 credential_blob, int(key_version), time.time() if status == "tested" else None),
            )
            row = conn.execute(
                "SELECT * FROM exchange_connections WHERE tenant_id=?", (tenant_id,)
            ).fetchone()
        self.audit("exchange_connection_updated", tenant_id=tenant_id,
                   payload={"exchange_id": exchange_id, "status": status})
        result = dict(row)
        result.pop("credential_blob", None)
        result["capabilities"] = json.loads(result.pop("capabilities_json") or "{}")
        return result

    def exchange_connection(self, tenant_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM exchange_connections WHERE tenant_id=?", (tenant_id,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result.pop("credential_blob", None)
        result["capabilities"] = json.loads(result.pop("capabilities_json") or "{}")
        return result

    def encrypted_credentials(self, tenant_id: str) -> tuple[str, str] | None:
        row = self.encrypted_credentials_with_version(tenant_id)
        return None if row is None else (row[0], row[1])

    def encrypted_credentials_with_version(self, tenant_id: str) -> tuple[str, str, int] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT target_id,credential_blob,key_version FROM exchange_connections WHERE tenant_id=?",
                (tenant_id,),
            ).fetchone()
        if not row or not row["credential_blob"]:
            return None
        return str(row["target_id"]), str(row["credential_blob"]), int(row["key_version"] or 1)

    # --- Telegram alert connection -------------------------------------------
    # New columns on this table must be double-written: once into the CREATE
    # script above and once into a PRAGMA table_info additions block, the way
    # exchange_connections does it.

    @staticmethod
    def _telegram_view(row: sqlite3.Row) -> dict[str, Any]:
        """Masked read model. The bot token never leaves this module."""
        result = dict(row)
        result.pop("credential_blob", None)
        result["enabled"] = bool(result.get("enabled"))
        return result

    def set_telegram_connection(self, tenant_id: str, chat_id: str, token_last4: str, *,
                                status: str = "stored", enabled: bool = True,
                                bot_username: str = "",
                                credential_blob: str | None = None,
                                key_version: int = 1) -> dict[str, Any]:
        if int(key_version) < 1:
            raise ValueError("credential key version must be positive")
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO telegram_connections
                (tenant_id,chat_id,token_last4,bot_username,status,enabled,
                 credential_blob,key_version,tested_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(tenant_id) DO UPDATE SET
                  chat_id=excluded.chat_id,token_last4=excluded.token_last4,
                  bot_username=excluded.bot_username,status=excluded.status,
                  enabled=excluded.enabled,
                  credential_blob=coalesce(excluded.credential_blob,telegram_connections.credential_blob),
                  key_version=CASE WHEN excluded.credential_blob IS NULL
                    THEN telegram_connections.key_version ELSE excluded.key_version END,
                  tested_at=excluded.tested_at,
                  updated_at=excluded.updated_at
                """,
                (tenant_id, chat_id, token_last4, bot_username, status, int(bool(enabled)),
                 credential_blob, int(key_version), time.time() if status == "tested" else None,
                 time.time()),
            )
            row = conn.execute(
                "SELECT * FROM telegram_connections WHERE tenant_id=?", (tenant_id,)
            ).fetchone()
        # Payload carries no chat id and no token fragment: the audit log is
        # append-only and hash-chained, so partial disclosure there is forever.
        self.audit("telegram_connection_updated", tenant_id=tenant_id,
                   payload={"status": status, "enabled": bool(enabled)})
        return self._telegram_view(row)

    def telegram_connection(self, tenant_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM telegram_connections WHERE tenant_id=?", (tenant_id,)
            ).fetchone()
        return None if row is None else self._telegram_view(row)

    def encrypted_telegram_token(self, tenant_id: str) -> str | None:
        row = self.encrypted_telegram_token_with_version(tenant_id)
        return None if row is None else row[0]

    def encrypted_telegram_token_with_version(self, tenant_id: str) -> tuple[str, int] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT credential_blob,key_version FROM telegram_connections WHERE tenant_id=?",
                (tenant_id,),
            ).fetchone()
        if not row or not row["credential_blob"]:
            return None
        return str(row["credential_blob"]), int(row["key_version"] or 1)

    def delete_telegram_connection(self, tenant_id: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM telegram_connections WHERE tenant_id=?", (tenant_id,))

    def record_config_revision(self, tenant_id: str, user_id: str,
                               config: dict[str, Any]) -> int:
        with self.connection() as conn:
            revision = int(conn.execute(
                "SELECT coalesce(max(revision),0)+1 FROM config_revisions WHERE tenant_id=?",
                (tenant_id,),
            ).fetchone()[0])
            conn.execute(
                "INSERT INTO config_revisions (id,tenant_id,revision,author_user_id,config_json,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (uuid.uuid4().hex, tenant_id, revision, user_id,
                 json.dumps(config, sort_keys=True), time.time()),
            )
        self.audit("config_revised", tenant_id=tenant_id, user_id=user_id,
                   payload={"revision": revision})
        return revision

    def set_trade_pin(self, user_id: str, pin: str) -> None:
        encoded = hash_trade_pin(pin)
        with self.connection() as conn:
            conn.execute(
                "UPDATE users SET trade_pin_hash=?,pin_failures=0,pin_locked_until=0 WHERE id=?",
                (encoded, user_id),
            )
        self.audit("trade_pin_set", user_id=user_id)

    def check_trade_pin(self, user_id: str, pin: str) -> tuple[bool, str]:
        now = time.time()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row or not row["trade_pin_hash"]:
                return False, "Trade PIN is not configured"
            if float(row["pin_locked_until"] or 0) > now:
                return False, "Trade PIN is temporarily locked"
            if verify_trade_pin(str(row["trade_pin_hash"]), pin):
                conn.execute(
                    "UPDATE users SET pin_failures=0,pin_locked_until=0 WHERE id=?", (user_id,)
                )
                return True, ""
            failures = int(row["pin_failures"] or 0) + 1
            locked_until = now + 900 if failures >= 5 else 0
            conn.execute(
                "UPDATE users SET pin_failures=?,pin_locked_until=? WHERE id=?",
                (0 if locked_until else failures, locked_until, user_id),
            )
        return False, "Invalid Trade PIN"

    def create_challenge(self, tenant_id: str, user_id: str, payload: dict[str, Any],
                         ttl_seconds: int = 60) -> dict[str, Any]:
        challenge_id = uuid.uuid4().hex
        now = time.time()
        digest = order_digest(payload)
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO order_challenges (id,tenant_id,user_id,payload_json,digest,expires_at,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (challenge_id, tenant_id, user_id, json.dumps(payload, sort_keys=True), digest,
                 now + ttl_seconds, now),
            )
            row = conn.execute("SELECT * FROM order_challenges WHERE id=?", (challenge_id,)).fetchone()
        return dict(row)

    def confirm_challenge(self, challenge_id: str, tenant_id: str, user_id: str,
                          command_id: str) -> dict[str, Any]:
        now = time.time()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM order_challenges WHERE id=? AND tenant_id=? AND user_id=?",
                (challenge_id, tenant_id, user_id),
            ).fetchone()
            if row is None:
                raise KeyError("order challenge not found")
            if row["status"] in {"confirmed", "queued"}:
                if str(row["command_id"] or "") != str(command_id):
                    raise ValueError("order challenge was already confirmed with another idempotency key")
                return dict(row)
            if row["status"] != "draft" or float(row["expires_at"]) < now:
                conn.execute("UPDATE order_challenges SET status='expired' WHERE id=?", (challenge_id,))
                raise ValueError("order challenge expired")
            conn.execute(
                "UPDATE order_challenges SET status='confirmed',command_id=? WHERE id=?",
                (command_id, challenge_id),
            )
            row = conn.execute("SELECT * FROM order_challenges WHERE id=?", (challenge_id,)).fetchone()
        return dict(row)

    def mark_challenge_queued(self, challenge_id: str, command_id: str) -> dict[str, Any]:
        with self.connection() as conn:
            conn.execute(
                "UPDATE order_challenges SET status='queued' WHERE id=? AND command_id=?",
                (challenge_id, command_id),
            )
            row = conn.execute("SELECT * FROM order_challenges WHERE id=?", (challenge_id,)).fetchone()
        if row is None:
            raise KeyError("order challenge not found")
        return dict(row)

    def list_challenges(self, tenant_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM order_challenges WHERE tenant_id=? ORDER BY created_at DESC LIMIT ?",
                (tenant_id, max(1, min(int(limit), 100))),
            ).fetchall()
        return [dict(row) for row in rows]

    def challenge(self, challenge_id: str, tenant_id: str, user_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM order_challenges WHERE id=? AND tenant_id=? AND user_id=?",
                (challenge_id, tenant_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def request_live(
        self, tenant_id: str, user_id: str, *, risk: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        approval_id = uuid.uuid4().hex
        now = time.time()
        supplied = risk or {}
        approval_risk = {
            "max_leverage": float(supplied.get("max_leverage", 1)),
            "max_open_positions": int(supplied.get("max_open_positions", 1)),
            "max_allocation_pct": float(
                supplied.get("max_position_per_trade_pct", 10)
            ),
            "max_daily_loss_pct": float(supplied.get("max_daily_loss_pct", 6)),
            "risk_pct": float(supplied.get("risk_pct", 0.01)),
            "stop_loss_required": bool(supplied.get("stop_loss_required", True)),
            "execution_mode": "cdc_pure"
            if not bool(supplied.get("stop_loss_required", True))
            else "stop_loss",
        }
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO live_approvals (id,tenant_id,requested_by,status,risk_json,requested_at) "
                "VALUES (?,?,?,'requested',?,?)",
                (approval_id, tenant_id, user_id, json.dumps(approval_risk), now),
            )
            conn.execute("UPDATE tenants SET live_status='requested' WHERE id=?", (tenant_id,))
        self.audit("live_requested", tenant_id=tenant_id, user_id=user_id, payload=approval_risk)
        return {"id": approval_id, "status": "requested", "risk": approval_risk}

    def reset_live_approval(self, tenant_id: str, user_id: str, reason: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE live_approvals SET status='superseded',decided_at=? "
                "WHERE tenant_id=? AND status IN ('requested','approved')",
                (time.time(), tenant_id),
            )
            conn.execute(
                "UPDATE tenants SET live_status='not_requested' WHERE id=?", (tenant_id,)
            )
        self.audit(
            "live_approval_reset", tenant_id=tenant_id, user_id=user_id,
            payload={"reason": str(reason)[:200]},
        )

    def approve_live(self, tenant_id: str, admin_user_id: str) -> None:
        now = time.time()
        with self.connection() as conn:
            approval = conn.execute(
                "SELECT * FROM live_approvals WHERE tenant_id=? AND status='requested' ORDER BY requested_at DESC LIMIT 1",
                (tenant_id,),
            ).fetchone()
            if approval is None:
                raise ValueError("tenant has no pending live request")
            conn.execute(
                "UPDATE live_approvals SET status='approved',approved_by=?,decided_at=? WHERE id=?",
                (admin_user_id, now, approval["id"]),
            )
            conn.execute("UPDATE tenants SET live_status='approved' WHERE id=?", (tenant_id,))
        self.audit("live_approved", tenant_id=tenant_id, user_id=admin_user_id)

    def fail_live_activation(self, tenant_id: str, admin_user_id: str, reason: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE live_approvals SET status='activation_failed',decided_at=? "
                "WHERE id=(SELECT id FROM live_approvals WHERE tenant_id=? "
                "ORDER BY requested_at DESC LIMIT 1)",
                (time.time(), tenant_id),
            )
            conn.execute(
                "UPDATE tenants SET live_status='activation_failed' WHERE id=?", (tenant_id,)
            )
        self.audit(
            "live_activation_failed", tenant_id=tenant_id, user_id=admin_user_id,
            payload={"reason": str(reason)[:500]},
        )
