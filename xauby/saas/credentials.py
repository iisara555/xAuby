from __future__ import annotations

import hashlib
import json
from base64 import b64decode, b64encode
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialCipher:
    """Versioned AES-256-GCM envelope encryption for exchange credentials."""

    VERSION = 1

    def __init__(self, master_key: str, *, fallback_secret: str = "") -> None:
        value = str(master_key or "").strip()
        if value:
            try:
                key = b64decode(value, validate=True)
            except ValueError as exc:
                raise ValueError("XAUBY_CREDENTIAL_MASTER_KEY must be valid base64") from exc
            if len(key) != 32:
                raise ValueError("XAUBY_CREDENTIAL_MASTER_KEY must decode to 32 bytes")
        elif fallback_secret:
            key = hashlib.sha256(f"xauby-dev:{fallback_secret}".encode()).digest()
        else:
            raise ValueError("XAUBY_CREDENTIAL_MASTER_KEY is required")
        self._cipher = AESGCM(key)

    @staticmethod
    def _aad(tenant_id: str, target_id: str, version: int) -> bytes:
        return f"xauby:{tenant_id}:{target_id}:v{version}".encode("utf-8")

    def encrypt(self, tenant_id: str, target_id: str, credentials: dict[str, str]) -> str:
        import os

        clean = {str(key): str(value) for key, value in credentials.items() if value}
        nonce = os.urandom(12)
        plaintext = json.dumps(clean, sort_keys=True, separators=(",", ":")).encode()
        ciphertext = self._cipher.encrypt(
            nonce, plaintext, self._aad(tenant_id, target_id, self.VERSION)
        )
        return json.dumps(
            {"v": self.VERSION, "n": b64encode(nonce).decode(), "c": b64encode(ciphertext).decode()},
            separators=(",", ":"),
        )

    def decrypt(self, tenant_id: str, target_id: str, envelope: str) -> dict[str, str]:
        try:
            payload: dict[str, Any] = json.loads(envelope)
            version = int(payload["v"])
            nonce = b64decode(payload["n"], validate=True)
            ciphertext = b64decode(payload["c"], validate=True)
            plaintext = self._cipher.decrypt(
                nonce, ciphertext, self._aad(tenant_id, target_id, version)
            )
            decoded = json.loads(plaintext)
        except Exception as exc:
            raise ValueError("exchange credentials could not be decrypted") from exc
        if version != self.VERSION or not isinstance(decoded, dict):
            raise ValueError("unsupported credential envelope")
        return {str(key): str(value) for key, value in decoded.items()}


def parse_previous_keys(value: str) -> dict[int, str]:
    """Parse ``version:base64`` key entries from the control environment.

    The master-key version is intentionally distinct from ``CredentialCipher``'s
    envelope-format version.  Keeping old keys in this small keyring makes a
    key rotation recoverable: a process started after the environment changes
    can still decrypt rows that have not yet been re-encrypted.
    """

    keys: dict[int, str] = {}
    for raw in str(value or "").split(","):
        item = raw.strip()
        if not item:
            continue
        version_text, separator, encoded = item.partition(":")
        if not separator or not version_text.isdigit() or int(version_text) < 1:
            raise ValueError("XAUBY_CREDENTIAL_PREVIOUS_KEYS entries must be version:base64")
        version = int(version_text)
        if version in keys or not encoded:
            raise ValueError("duplicate or empty previous credential key version")
        # Constructing a cipher validates the base64 encoding and key length
        # without exposing the value in an exception or a log.
        CredentialCipher(encoded)
        keys[version] = encoded
    return keys


class CredentialKeyring:
    """Decrypt versioned credential rows while writing only the active key.

    ``key_version`` used to be a decorative database column.  This keyring is
    the corresponding runtime contract: new credentials are written with the
    active version, while retained prior keys keep an interrupted rotation and
    retained backups recoverable.
    """

    def __init__(
        self,
        active_key: str,
        *,
        active_version: int = 1,
        previous_keys: str = "",
        fallback_secret: str = "",
    ) -> None:
        if int(active_version) < 1:
            raise ValueError("XAUBY_CREDENTIAL_MASTER_KEY_VERSION must be positive")
        self.active_version = int(active_version)
        previous = parse_previous_keys(previous_keys)
        if self.active_version in previous:
            raise ValueError("active credential key version must not appear in previous keys")
        self._keys = {self.active_version: str(active_key or "").strip(), **previous}
        self._ciphers = {
            version: CredentialCipher(
                key, fallback_secret=fallback_secret if version == self.active_version else ""
            )
            for version, key in self._keys.items()
        }

    def encrypt(self, tenant_id: str, target_id: str, credentials: dict[str, str]) -> str:
        return self._ciphers[self.active_version].encrypt(tenant_id, target_id, credentials)

    def decrypt(
        self,
        tenant_id: str,
        target_id: str,
        envelope: str,
        *,
        key_version: int = 1,
    ) -> dict[str, str]:
        try:
            cipher = self._ciphers[int(key_version)]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"credential key version {key_version} is unavailable") from exc
        return cipher.decrypt(tenant_id, target_id, envelope)

    def previous_keys_value(self) -> str:
        """Stable environment representation, excluding the active key."""

        return ",".join(
            f"{version}:{self._keys[version]}"
            for version in sorted(self._keys)
            if version != self.active_version
        )

    def keys_for_recovery(self) -> dict[int, str]:
        """Return a copy for an encrypted recovery bundle; never log it."""

        return dict(self._keys)
