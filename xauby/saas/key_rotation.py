"""Safe credential-master-key rotation primitives.

The environment is staged with both the new active key and the old keyring
before database rows are changed.  That ordering deliberately makes an
interrupted run recoverable: the next process can decrypt either version.
"""

from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path
from typing import Any

from xauby.saas.credentials import CredentialKeyring
from xauby.saas.store import ControlPlaneStore


def new_master_key() -> str:
    return base64.b64encode(os.urandom(32)).decode("ascii")


def previous_keys_value(keys: dict[int, str]) -> str:
    return ",".join(f"{version}:{keys[version]}" for version in sorted(keys))


def _credential_rows(store: ControlPlaneStore) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with store.connection() as conn:
        exchanges = [
            dict(row) for row in conn.execute(
                "SELECT tenant_id,target_id,credential_blob,key_version "
                "FROM exchange_connections WHERE credential_blob IS NOT NULL"
            )
        ]
        telegram = [
            dict(row) for row in conn.execute(
                "SELECT tenant_id,credential_blob,key_version "
                "FROM telegram_connections WHERE credential_blob IS NOT NULL"
            )
        ]
    return exchanges, telegram


def validate_credential_keyring(store: ControlPlaneStore, keyring: CredentialKeyring) -> dict[str, int]:
    """Decrypt every stored secret without returning plaintext to the caller."""

    exchanges, telegram = _credential_rows(store)
    for row in exchanges:
        keyring.decrypt(
            str(row["tenant_id"]), str(row["target_id"]), str(row["credential_blob"]),
            key_version=int(row["key_version"] or 1),
        )
    for row in telegram:
        keyring.decrypt(
            str(row["tenant_id"]), "telegram", str(row["credential_blob"]),
            key_version=int(row["key_version"] or 1),
        )
    return {"exchange_connections": len(exchanges), "telegram_connections": len(telegram)}


def rotate_credential_keyring(
    store: ControlPlaneStore,
    *,
    source: CredentialKeyring,
    destination: CredentialKeyring,
) -> dict[str, int]:
    """Re-encrypt all credential blobs in one SQLite transaction.

    ``destination`` must retain every source key until the transaction and a
    restore drill have succeeded.  Callers should update the control env first
    so an interruption before this transaction remains readable.
    """

    if destination.active_version <= source.active_version:
        raise ValueError("new credential key version must be greater than the active version")
    counts = {"exchange_connections": 0, "telegram_connections": 0}
    with store.connection() as conn:
        exchanges = conn.execute(
            "SELECT tenant_id,target_id,credential_blob,key_version FROM exchange_connections "
            "WHERE credential_blob IS NOT NULL"
        ).fetchall()
        for row in exchanges:
            tenant_id = str(row["tenant_id"])
            target_id = str(row["target_id"])
            plaintext = source.decrypt(
                tenant_id, target_id, str(row["credential_blob"]),
                key_version=int(row["key_version"] or 1),
            )
            conn.execute(
                "UPDATE exchange_connections SET credential_blob=?,key_version=?,updated_at=strftime('%s','now') "
                "WHERE tenant_id=?",
                (destination.encrypt(tenant_id, target_id, plaintext), destination.active_version, tenant_id),
            )
            counts["exchange_connections"] += 1
        telegram = conn.execute(
            "SELECT tenant_id,credential_blob,key_version FROM telegram_connections "
            "WHERE credential_blob IS NOT NULL"
        ).fetchall()
        for row in telegram:
            tenant_id = str(row["tenant_id"])
            plaintext = source.decrypt(
                tenant_id, "telegram", str(row["credential_blob"]),
                key_version=int(row["key_version"] or 1),
            )
            conn.execute(
                "UPDATE telegram_connections SET credential_blob=?,key_version=?,updated_at=strftime('%s','now') "
                "WHERE tenant_id=?",
                (destination.encrypt(tenant_id, "telegram", plaintext), destination.active_version, tenant_id),
            )
            counts["telegram_connections"] += 1
    store.audit(
        "credential_master_key_rotated",
        payload={"active_key_version": destination.active_version, **counts},
    )
    return counts


def update_environment(path: Path, values: dict[str, str]) -> None:
    """Replace selected dotenv values atomically, preserving owner and mode."""

    stat = path.stat()
    existing = path.read_text(encoding="utf-8").splitlines()
    pending = dict(values)
    rendered: list[str] = []
    for line in existing:
        name, separator, _value = line.partition("=")
        if separator and name in pending:
            rendered.append(f"{name}={pending.pop(name)}")
        else:
            rendered.append(line)
    rendered.extend(f"{name}={value}" for name, value in pending.items())
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("\n".join(rendered) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.st_mode & 0o777)
        os.chown(temporary, stat.st_uid, stat.st_gid)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
