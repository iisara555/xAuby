from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import struct
import time
from base64 import b32decode, b32encode, urlsafe_b64decode, urlsafe_b64encode
from typing import Any

TENANT_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$")


class AttemptThrottle:
    """In-process sliding-window throttle for credential-guessing endpoints.

    The control plane runs as a single uvicorn process, so process-local
    state is authoritative. Keys should combine the target identity and the
    client address so one attacker cannot lock out other clients globally.
    """

    def __init__(self, *, max_attempts: int = 8, window_seconds: float = 300.0,
                 lockout_seconds: float = 900.0, max_keys: int = 50_000):
        import threading

        self.max_attempts = int(max_attempts)
        self.window_seconds = float(window_seconds)
        self.lockout_seconds = float(lockout_seconds)
        self.max_keys = int(max_keys)
        self._lock = threading.Lock()
        self._attempts: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def _prune(self, now: float) -> None:
        if len(self._attempts) > self.max_keys:
            cutoff = now - self.window_seconds
            self._attempts = {
                key: stamps for key, stamps in self._attempts.items()
                if stamps and stamps[-1] >= cutoff
            }
        self._locked_until = {
            key: until for key, until in self._locked_until.items() if until > now
        }

    def retry_after(self, key: str) -> int:
        """Seconds until the key may try again; 0 when not locked."""
        now = time.time()
        with self._lock:
            until = self._locked_until.get(key, 0.0)
            return max(0, int(until - now)) if until > now else 0

    def record_failure(self, key: str) -> None:
        now = time.time()
        with self._lock:
            self._prune(now)
            stamps = [s for s in self._attempts.get(key, []) if s >= now - self.window_seconds]
            stamps.append(now)
            self._attempts[key] = stamps
            if len(stamps) >= self.max_attempts:
                self._locked_until[key] = now + self.lockout_seconds

    def clear(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)
            self._locked_until.pop(key, None)


def validate_tenant_slug(value: str) -> str:
    slug = str(value or "").strip().lower()
    if not TENANT_SLUG_RE.fullmatch(slug):
        raise ValueError("tenant slug must be 1-64 lowercase letters, digits, or hyphens")
    return slug


def token_hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def order_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def sign_state(secret: str, payload: dict[str, Any], ttl_seconds: int = 600) -> str:
    body = dict(payload)
    body["exp"] = int(time.time()) + int(ttl_seconds)
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encoded = urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    sig = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{sig}"


def verify_state(secret: str, signed: str) -> dict[str, Any] | None:
    encoded, sep, supplied = str(signed or "").partition(".")
    if not sep:
        return None
    expected = hmac.new(
        secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        return None
    try:
        raw = urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(raw.decode("utf-8"))
        if int(payload.get("exp") or 0) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def hash_trade_pin(pin: str) -> str:
    _validate_trade_pin(pin)
    try:
        from argon2 import PasswordHasher
        return PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2).hash(pin)
    except Exception:
        # Some embedded Python distributions cannot load argon2's native module.
        # Scrypt remains memory-hard and avoids disabling Trade PIN protection.
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(
            pin.encode("utf-8"), salt=salt, n=2**15, r=8, p=1, dklen=32,
            maxmem=64 * 1024 * 1024,
        )
        return "$scrypt$32768$8$1${}${}".format(
            urlsafe_b64encode(salt).decode("ascii").rstrip("="),
            urlsafe_b64encode(digest).decode("ascii").rstrip("="),
        )


def hash_password(password: str) -> str:
    _validate_password(password)
    try:
        from argon2 import PasswordHasher

        return PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2).hash(password)
    except Exception:
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=2**15, r=8, p=1, dklen=32,
            maxmem=64 * 1024 * 1024,
        )
        return "$scrypt$32768$8$1${}${}".format(
            urlsafe_b64encode(salt).decode("ascii").rstrip("="),
            urlsafe_b64encode(digest).decode("ascii").rstrip("="),
        )


def verify_password(encoded: str, password: str) -> bool:
    # Password and Trade PIN hashes intentionally share the same hardened format.
    return verify_trade_pin(encoded, password)


def _validate_password(password: str) -> None:
    value = str(password or "")
    if not 12 <= len(value) <= 128:
        raise ValueError("Password must contain 12-128 characters")
    if value.lower() == value or value.upper() == value or not any(ch.isdigit() for ch in value):
        raise ValueError("Password must include upper/lowercase letters and a number")


def new_totp_secret() -> str:
    return b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def totp_code(secret: str, *, timestamp: float | None = None, step: int = 30) -> str:
    normalized = str(secret or "").strip().upper()
    key = b32decode(normalized + "=" * (-len(normalized) % 8), casefold=True)
    counter = int((time.time() if timestamp is None else timestamp) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{number:06d}"


def verify_totp(secret: str, supplied: str, *, timestamp: float | None = None) -> bool:
    now = time.time() if timestamp is None else timestamp
    value = str(supplied or "").strip()
    if not re.fullmatch(r"[0-9]{6}", value):
        return False
    try:
        return any(
            hmac.compare_digest(totp_code(secret, timestamp=now + offset), value)
            for offset in (-30, 0, 30)
        )
    except (ValueError, TypeError):
        return False


def verify_trade_pin(encoded: str, pin: str) -> bool:
    if str(encoded).startswith("$scrypt$"):
        try:
            _, _, n, r, p, salt_text, digest_text = str(encoded).split("$")
            salt = urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
            expected = urlsafe_b64decode(digest_text + "=" * (-len(digest_text) % 4))
            supplied = hashlib.scrypt(
                str(pin).encode("utf-8"), salt=salt,
                n=int(n), r=int(r), p=int(p), dklen=len(expected),
                maxmem=64 * 1024 * 1024,
            )
            return hmac.compare_digest(expected, supplied)
        except (ValueError, TypeError):
            return False
    try:
        from argon2 import PasswordHasher
        from argon2.exceptions import InvalidHashError, VerifyMismatchError

        return PasswordHasher().verify(encoded, pin)
    except (VerifyMismatchError, InvalidHashError, ValueError, TypeError, ImportError, OSError):
        return False


def _validate_trade_pin(pin: str) -> None:
    value = str(pin or "")
    if not value.isdigit() or not 8 <= len(value) <= 12:
        raise ValueError("Trade PIN must contain 8-12 digits")
