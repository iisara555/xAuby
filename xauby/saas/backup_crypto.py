"""Streaming encryption for portable SaaS backup archives."""

from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


MAGIC = b"XAUBY-BACKUP-ENC-1\0"
NONCE_BYTES = 12
TAG_BYTES = 16
AAD = b"xauby:saas-backup:v1"
CHUNK_BYTES = 1024 * 1024


class BackupCipher:
    """AES-256-GCM archive encryption with a small self-identifying header."""

    def __init__(self, encoded_key: str) -> None:
        try:
            key = base64.b64decode(str(encoded_key or "").strip(), validate=True)
        except ValueError as exc:
            raise ValueError("XAUBY_BACKUP_ENCRYPTION_KEY must be valid base64") from exc
        if len(key) != 32:
            raise ValueError("XAUBY_BACKUP_ENCRYPTION_KEY must decode to 32 bytes")
        self._key = key

    @staticmethod
    def is_encrypted(path: Path) -> bool:
        with path.open("rb") as handle:
            return handle.read(len(MAGIC)) == MAGIC

    def encrypt_file(self, source: Path, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        try:
            nonce = os.urandom(NONCE_BYTES)
            encryptor = Cipher(algorithms.AES(self._key), modes.GCM(nonce)).encryptor()
            encryptor.authenticate_additional_data(AAD)
            with source.open("rb") as src, os.fdopen(descriptor, "wb") as dst:
                dst.write(MAGIC)
                dst.write(nonce)
                for chunk in iter(lambda: src.read(CHUNK_BYTES), b""):
                    dst.write(encryptor.update(chunk))
                dst.write(encryptor.finalize())
                dst.write(encryptor.tag)
                dst.flush()
                os.fsync(dst.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return destination

    def decrypt_file(self, source: Path, destination: Path) -> Path:
        size = source.stat().st_size
        minimum = len(MAGIC) + NONCE_BYTES + TAG_BYTES
        if size < minimum:
            raise ValueError("encrypted backup is truncated")
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        try:
            with source.open("rb") as src:
                if src.read(len(MAGIC)) != MAGIC:
                    raise ValueError("not an xAuby encrypted backup")
                nonce = src.read(NONCE_BYTES)
                src.seek(size - TAG_BYTES)
                tag = src.read(TAG_BYTES)
                src.seek(len(MAGIC) + NONCE_BYTES)
                remaining = size - len(MAGIC) - NONCE_BYTES - TAG_BYTES
                decryptor = Cipher(algorithms.AES(self._key), modes.GCM(nonce, tag)).decryptor()
                decryptor.authenticate_additional_data(AAD)
                with os.fdopen(descriptor, "wb") as dst:
                    while remaining:
                        chunk = src.read(min(CHUNK_BYTES, remaining))
                        if not chunk:
                            raise ValueError("encrypted backup is truncated")
                        remaining -= len(chunk)
                        dst.write(decryptor.update(chunk))
                    dst.write(decryptor.finalize())
                    dst.flush()
                    os.fsync(dst.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        except (InvalidTag, OSError, ValueError) as exc:
            raise ValueError("encrypted backup could not be decrypted") from exc
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return destination
