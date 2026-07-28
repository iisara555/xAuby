"""Envelope encryption for tenant credentials, with real key rotation.

Roadmap P2.2. The `encrypted_credentials` table has carried a `key_version`
column since it was created, and there was no rotation code anywhere. The
column was worse than decorative: both credential upserts wrote it as a SQL
**literal** `1`, so it could not have recorded a second key even if one had
existed. A column naming a capability nobody built invites the assumption that
keys can be rotated — so either the column goes, or rotation becomes real. This
is rotation becoming real.

The model:

* **One active key** encrypts. Its id is stamped into the envelope (`"k"`) and
  into the row's `key_version`, so a blob always says which key opened it.
* **Retired keys decrypt only.** They are supplied separately and exist so that
  blobs written before a rotation stay readable until they are rewrapped.
* **Rewrapping is a separate, resumable pass** (`scripts/rotate_credential_key.py`),
  not something that happens implicitly on read. A rotation you cannot observe
  the progress of is one you cannot safely finish.

Two compatibility properties, both deliberate:

* An envelope with no `"k"` is key id 1 — that is every blob written before this
  change, and they keep decrypting untouched.
* The AAD is unchanged, and the key id is *not* part of it. So an envelope
  written by this code is still readable by the previous version as long as the
  active key is still id 1: adding rotation does not itself make a rollback
  lossy. Binding the key id into the AAD would buy nothing — presenting the
  wrong key already fails the tag.
"""
from __future__ import annotations

import hashlib
import json
import os
from base64 import b64decode, b64encode
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

#: Envelopes written before key ids existed. They decrypt under this id.
LEGACY_KEY_ID = 1


class CredentialDecryptError(ValueError):
    """A blob could not be opened with the key it names."""


class UnknownCredentialKey(CredentialDecryptError):
    """The envelope names a key id that was not supplied.

    Kept distinct from a failed tag check because the remedies are opposite: an
    unknown key id means the retired key was dropped too early and can be put
    back, while a failed tag means the blob itself is unusable and the tenant
    has to reconnect.
    """


def parse_key_set(text: str) -> dict[int, bytes]:
    """Parse ``"1:<base64>,2:<base64>"`` into ``{id: key_bytes}``.

    Raises on anything malformed rather than skipping it. A silently dropped
    retired key looks exactly like a rotation that finished.
    """
    keys: dict[int, bytes] = {}
    for chunk in str(text or "").split(","):
        entry = chunk.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise ValueError(f"retired key entry {entry!r} is not in <id>:<base64> form")
        raw_id, raw_key = entry.split(":", 1)
        try:
            key_id = int(raw_id.strip())
        except ValueError as exc:
            raise ValueError(f"retired key id {raw_id!r} is not an integer") from exc
        key = _decode_key(raw_key.strip(), f"retired key {key_id}")
        if key_id in keys and keys[key_id] != key:
            raise ValueError(f"retired key id {key_id} is listed twice with different keys")
        keys[key_id] = key
    return keys


def _decode_key(value: str, label: str) -> bytes:
    try:
        key = b64decode(value, validate=True)
    except ValueError as exc:
        raise ValueError(f"{label} must be valid base64") from exc
    if len(key) != 32:
        raise ValueError(f"{label} must decode to 32 bytes")
    return key


def generate_key() -> str:
    """A fresh base64 master key, for the rotation runbook."""
    return b64encode(os.urandom(32)).decode("ascii")


class CredentialCipher:
    """Versioned AES-256-GCM envelope encryption for exchange credentials."""

    VERSION = 1

    def __init__(
        self,
        master_key: str,
        *,
        fallback_secret: str = "",
        active_key_id: int = LEGACY_KEY_ID,
        retired_keys: str = "",
    ) -> None:
        value = str(master_key or "").strip()
        if value:
            key = _decode_key(value, "XAUBY_CREDENTIAL_MASTER_KEY")
        elif fallback_secret:
            key = hashlib.sha256(f"xauby-dev:{fallback_secret}".encode()).digest()
        else:
            raise ValueError("XAUBY_CREDENTIAL_MASTER_KEY is required")

        active_id = int(active_key_id)
        if active_id < 1:
            raise ValueError("XAUBY_CREDENTIAL_MASTER_KEY_ID must be a positive integer")

        keys = parse_key_set(retired_keys)
        if active_id in keys and keys[active_id] != key:
            # Two different keys under one id is the failure that loses data
            # quietly: whichever one is consulted, half the blobs stop opening
            # and the tag failure looks like corruption.
            raise ValueError(
                f"key id {active_id} is both the active key and a different retired key"
            )
        keys[active_id] = key

        self._active_key_id = active_id
        self._keys = {key_id: AESGCM(material) for key_id, material in keys.items()}
        self._retired_key_ids = tuple(sorted(k for k in keys if k != active_id))

    @property
    def active_key_id(self) -> int:
        return self._active_key_id

    @property
    def retired_key_ids(self) -> tuple[int, ...]:
        return self._retired_key_ids

    @staticmethod
    def _aad(tenant_id: str, target_id: str, version: int) -> bytes:
        return f"xauby:{tenant_id}:{target_id}:v{version}".encode("utf-8")

    @staticmethod
    def key_id_of(envelope: str) -> int:
        """Which key an envelope names, without decrypting it.

        Used for reporting rotation progress, so it must not need the key.
        """
        try:
            payload = json.loads(envelope)
            return int(payload.get("k", LEGACY_KEY_ID))
        except Exception as exc:  # noqa: BLE001 - any unreadable blob is a finding
            raise ValueError("credential envelope is not readable JSON") from exc

    def encrypt(self, tenant_id: str, target_id: str, credentials: dict[str, str]) -> str:
        clean = {str(key): str(value) for key, value in credentials.items() if value}
        nonce = os.urandom(12)
        plaintext = json.dumps(clean, sort_keys=True, separators=(",", ":")).encode()
        ciphertext = self._keys[self._active_key_id].encrypt(
            nonce, plaintext, self._aad(tenant_id, target_id, self.VERSION)
        )
        return json.dumps(
            {
                "v": self.VERSION,
                "k": self._active_key_id,
                "n": b64encode(nonce).decode(),
                "c": b64encode(ciphertext).decode(),
            },
            separators=(",", ":"),
        )

    def decrypt(self, tenant_id: str, target_id: str, envelope: str) -> dict[str, str]:
        try:
            payload: dict[str, Any] = json.loads(envelope)
            version = int(payload["v"])
            key_id = int(payload.get("k", LEGACY_KEY_ID))
            nonce = b64decode(payload["n"], validate=True)
            ciphertext = b64decode(payload["c"], validate=True)
        except Exception as exc:  # noqa: BLE001
            raise CredentialDecryptError("exchange credentials could not be decrypted") from exc

        cipher = self._keys.get(key_id)
        if cipher is None:
            known = ", ".join(str(k) for k in sorted(self._keys))
            raise UnknownCredentialKey(
                f"credential envelope was encrypted with key id {key_id}, "
                f"which was not supplied (have: {known}). "
                "If this key was retired, add it back to XAUBY_CREDENTIAL_RETIRED_KEYS."
            )

        try:
            plaintext = cipher.decrypt(
                nonce, ciphertext, self._aad(tenant_id, target_id, version)
            )
            decoded = json.loads(plaintext)
        except Exception as exc:  # noqa: BLE001
            raise CredentialDecryptError("exchange credentials could not be decrypted") from exc
        if version != self.VERSION or not isinstance(decoded, dict):
            raise CredentialDecryptError("unsupported credential envelope")
        return {str(key): str(value) for key, value in decoded.items()}

    def rewrap(self, tenant_id: str, target_id: str, envelope: str) -> str | None:
        """Re-encrypt an envelope under the active key.

        Returns ``None`` when it is already under the active key, so callers can
        tell "nothing to do" from "rewrapped" without comparing ciphertexts.

        The new envelope is decrypted and compared before it is returned. The
        caller is about to overwrite the only copy of a tenant's exchange keys;
        the round trip is what makes that overwrite safe rather than hopeful.
        """
        if self.key_id_of(envelope) == self._active_key_id:
            return None
        plaintext = self.decrypt(tenant_id, target_id, envelope)
        rewrapped = self.encrypt(tenant_id, target_id, plaintext)
        if self.decrypt(tenant_id, target_id, rewrapped) != plaintext:
            raise CredentialDecryptError(
                "rewrapped envelope did not decrypt back to the original credentials"
            )
        return rewrapped
