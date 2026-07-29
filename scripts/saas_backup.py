from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
from contextlib import closing
from pathlib import Path

from xauby.saas.backup_crypto import BackupCipher
from xauby.saas.credentials import CredentialKeyring
from xauby.saas.settings import SaaSSettings

SCHEMA_VERSION = 2


def _sqlite_backup(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(destination)) as dst:
        src.execute("PRAGMA wal_checkpoint(PASSIVE)")
        src.backup(dst)
        result = dst.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"SQLite integrity check failed for {source}")


def _copy_tree(source: Path, destination: Path, *, include_secrets: bool) -> None:
    if not source.exists():
        return
    for item in source.rglob("*"):
        # Legacy plaintext credential files are never copied. Current credentials
        # are already encrypted inside control-plane.db.
        if not item.is_file() or item.name == "secrets.env":
            continue
        target = destination / item.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_checksums(root: Path) -> None:
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "checksums.sha256":
            rows.append(f"{_sha256(path)}  {path.relative_to(root).as_posix()}")
    (root / "checksums.sha256").write_text("\n".join(rows) + "\n", encoding="utf-8")


def _git_commit(project_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=project_root, capture_output=True,
        text=True, timeout=5, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _safe_extract(archive: tarfile.TarFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.getmembers():
        if member.issym() or member.islnk() or member.isdev():
            raise RuntimeError("backup contains an unsafe entry type")
        target = (destination / member.name).resolve()
        if root != target and root not in target.parents:
            raise RuntimeError("backup contains an unsafe path")
    try:
        archive.extractall(destination, filter="data")
    except TypeError:
        # Python 3.10 has no extraction filter. The explicit entry/path checks
        # above provide the equivalent safety policy for these backup bundles.
        archive.extractall(destination)


def _materialize_archive(path: Path, workspace: Path, encryption_key: str = "") -> Path:
    if not BackupCipher.is_encrypted(path):
        return path
    if not encryption_key:
        raise ValueError("encrypted backup requires XAUBY_BACKUP_ENCRYPTION_KEY")
    destination = workspace / "backup.tar.gz"
    return BackupCipher(encryption_key).decrypt_file(path, destination)


def verify_backup(path: Path, *, encryption_key: str = "") -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="xauby-verify-") as name:
        root = Path(name)
        archive_path = _materialize_archive(path, root, encryption_key)
        with tarfile.open(archive_path, "r:gz") as archive:
            _safe_extract(archive, root)
        bundle = root / "xauby-saas"
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        for line in (bundle / "checksums.sha256").read_text(encoding="utf-8").splitlines():
            expected, relative = line.split("  ", 1)
            target = bundle / relative
            if not target.is_file() or _sha256(target) != expected:
                raise RuntimeError(f"checksum verification failed: {relative}")
        checked = 0
        for database in bundle.rglob("*.db"):
            with closing(sqlite3.connect(database)) as conn:
                result = conn.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError(f"SQLite integrity check failed: {database.name}")
            checked += 1
        manifest["verified_databases"] = checked
        return manifest


def create_backup(settings: SaaSSettings, output: Path, *, kind: str, release_id: str,
                  retention_days: int, include_secrets: bool,
                  host_config: list[Path]) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    safe_release = "".join(ch for ch in release_id if ch.isalnum() or ch in "._-")[:80] or "unknown"
    final = output / f"xauby-saas-{kind}-{stamp}-{safe_release}.tar.gz"
    with tempfile.TemporaryDirectory(prefix="xauby-backup-") as temp_name:
        bundle = Path(temp_name) / "xauby-saas"
        databases = bundle / "databases"
        _sqlite_backup(settings.database_path, databases / "control-plane.db")
        for tenant_dir in settings.tenant_runtime_root.iterdir() if settings.tenant_runtime_root.exists() else []:
            if not tenant_dir.is_dir():
                continue
            for name in ("xauby.db", "manual_orders.db"):
                _sqlite_backup(tenant_dir / name, databases / "runtime" / tenant_dir.name / name)
            state = tenant_dir / "logs" / "xauby_bot_state.json"
            if state.is_file():
                target = bundle / "position-snapshots" / f"{tenant_dir.name}.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(state, target)
        _copy_tree(settings.tenant_config_root, bundle / "configs", include_secrets=include_secrets)
        for source in host_config:
            if source.is_file():
                target = bundle / "host-config" / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        manifest = {
            "format": SCHEMA_VERSION, "kind": kind, "release_id": safe_release,
            "created_at": time.time(), "git_commit": _git_commit(settings.project_root),
            "database_path": str(settings.database_path),
            "tenant_count": sum(1 for item in settings.tenant_config_root.iterdir() if item.is_dir())
            if settings.tenant_config_root.exists() else 0,
            "includes_secrets": False,
            "credentials_encrypted_in_database": True,
            "current_release": str(settings.project_root.resolve()),
        }
        (bundle / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        _write_checksums(bundle)
        with tarfile.open(final, "w:gz") as archive:
            archive.add(bundle, arcname="xauby-saas")
    os.chmod(final, 0o600)
    verify_backup(final)
    cutoff = time.time() - max(1, retention_days) * 86400
    candidates = sorted(output.glob(f"xauby-saas-{kind}-*.tar.gz"), key=lambda p: p.stat().st_mtime,
                        reverse=True)
    for old in candidates[1:]:  # never delete the newest/last-known-good archive
        if old.stat().st_mtime < cutoff:
            old.unlink()
    return final


def _rclone_destination(destination: str, filename: str) -> str:
    remote = str(destination or "").strip()
    remote_name, separator, remote_path = remote.partition(":")
    if (
        not remote
        or not separator
        or not remote_name
        or remote.startswith(("/", "./", "../"))
        or any(not (char.isalnum() or char in "_.-") for char in remote_name)
    ):
        raise ValueError("XAUBY_BACKUP_RCLONE_DESTINATION must be an rclone remote path")
    if ".." in remote_path.split("/"):
        raise ValueError("XAUBY_BACKUP_RCLONE_DESTINATION must not contain '..'")
    return f"{remote.rstrip('/')}/{filename}"


def _run_checked(command: list[str], *, input_data: bytes | None = None) -> None:
    result = subprocess.run(
        command, input=input_data, capture_output=True, timeout=300, check=False,
    )
    if result.returncode:
        # Do not include command output: rclone and GPG diagnostics can contain
        # configured remote paths or recipient identity.  The operator has the
        # unit journal for detailed local diagnosis.
        raise RuntimeError(f"backup off-site command failed ({Path(command[0]).name})")


def _recovery_payload(settings: SaaSSettings) -> bytes:
    keyring = CredentialKeyring(
        settings.credential_master_key,
        active_version=settings.credential_master_key_version,
        previous_keys=settings.credential_previous_keys,
    )
    payload = {
        "format": 1,
        "created_at": time.time(),
        "backup_encryption_key": settings.backup_encryption_key,
        "credential_active_key_version": keyring.active_version,
        "credential_keys": {str(version): value for version, value in keyring.keys_for_recovery().items()},
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _encrypt_recovery_bundle(settings: SaaSSettings, destination: Path) -> None:
    if not settings.backup_gpg_recipient:
        raise ValueError("XAUBY_BACKUP_GPG_RECIPIENT is required for off-site backup")
    homedir = settings.backup_gpg_homedir
    if homedir is None:
        raise ValueError("XAUBY_BACKUP_GPG_HOMEDIR is required for off-site backup")
    homedir.mkdir(parents=True, exist_ok=True)
    os.chmod(homedir, 0o700)
    _run_checked([
        "gpg", "--batch", "--yes", "--homedir", str(homedir), "--trust-model", "always",
        "--armor", "--output", str(destination), "--encrypt", "--recipient",
        settings.backup_gpg_recipient,
    ], input_data=_recovery_payload(settings))
    os.chmod(destination, 0o600)


def upload_offsite_backup(archive: Path, settings: SaaSSettings) -> dict[str, str]:
    """Encrypt and upload an archive plus a separately PGP-wrapped recovery key.

    The remote receives neither a plaintext archive nor a plaintext master key.
    The recovery key is decryptable only by the offline private key paired with
    ``XAUBY_BACKUP_GPG_RECIPIENT``.
    """

    if not settings.backup_rclone_destination:
        return {}
    if not settings.backup_encryption_key:
        raise ValueError("XAUBY_BACKUP_ENCRYPTION_KEY is required for off-site backup")
    if not shutil.which("rclone"):
        raise RuntimeError("rclone is required for off-site backup")
    if not shutil.which("gpg"):
        raise RuntimeError("gpg is required for off-site backup")
    config = settings.backup_rclone_config
    if config is None or not config.is_file():
        raise RuntimeError("XAUBY_BACKUP_RCLONE_CONFIG is missing")
    with tempfile.TemporaryDirectory(prefix="xauby-offsite-") as temp_name:
        temp = Path(temp_name)
        encrypted = BackupCipher(settings.backup_encryption_key).encrypt_file(
            archive, temp / f"{archive.name}.enc"
        )
        recovery = temp / f"{archive.name.replace('xauby-saas-', 'xauby-recovery-')}.asc"
        _encrypt_recovery_bundle(settings, recovery)
        encrypted_target = _rclone_destination(settings.backup_rclone_destination, encrypted.name)
        recovery_target = _rclone_destination(settings.backup_rclone_destination, recovery.name)
        base = ["rclone", "--config", str(config), "copyto"]
        _run_checked([*base, str(recovery), recovery_target])
        # Upload the recovery material first.  If the second upload fails, the
        # remote has no archive that cannot be recovered with its key bundle.
        _run_checked([*base, str(encrypted), encrypted_target])
    retention = max(1, int(settings.backup_offsite_retention_days))
    _run_checked([
        "rclone", "--config", str(config), "delete", settings.backup_rclone_destination,
        "--min-age", f"{retention}d", "--include", "xauby-saas-*.tar.gz.enc",
        "--include", "xauby-recovery-*.tar.gz.asc",
    ])
    return {"archive": encrypted_target, "recovery_bundle": recovery_target}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create and verify a consistent xAuby SaaS backup")
    parser.add_argument("--output", required=True)
    parser.add_argument("--retention-days", type=int, default=7)
    parser.add_argument("--kind", choices=("daily", "predeploy"), default="daily")
    parser.add_argument("--release-id", default="scheduled")
    parser.add_argument("--include-secrets", action="store_true")
    parser.add_argument("--host-config", action="append", default=[])
    parser.add_argument("--verify-only")
    args = parser.parse_args(argv)
    if args.verify_only:
        settings = SaaSSettings.from_env()
        print(json.dumps(
            verify_backup(Path(args.verify_only).resolve(), encryption_key=settings.backup_encryption_key),
            sort_keys=True,
        ))
        return 0
    settings = SaaSSettings.from_env()
    final = create_backup(
        settings, Path(args.output).resolve(), kind=args.kind, release_id=args.release_id,
        retention_days=args.retention_days, include_secrets=args.include_secrets,
        host_config=[Path(item) for item in args.host_config],
    )
    uploaded = upload_offsite_backup(final, settings)
    if uploaded:
        print(json.dumps({"local": str(final), "offsite": uploaded}, sort_keys=True))
    else:
        print(final)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
