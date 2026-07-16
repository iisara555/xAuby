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
