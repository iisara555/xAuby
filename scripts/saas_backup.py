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


#: Env keys whose presence in a backup would put the master key beside the
#: ciphertext it opens. P2.2's decision was that the key stays offline, so this
#: is enforced rather than documented.
MASTER_KEY_MARKERS = ("XAUBY_CREDENTIAL_MASTER_KEY", "XAUBY_CREDENTIAL_RETIRED_KEYS")


def _copy_tree(source: Path, destination: Path) -> None:
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


def carries_master_key(path: Path) -> bool:
    """Does this file assign a real master key?

    Checks for an assignment with a value, so a commented example or an empty
    placeholder in `.env.saas.example` does not trip the guard.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip() in MASTER_KEY_MARKERS and value.strip():
            return True
    return False


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


def verify_backup(path: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="xauby-verify-") as name:
        root = Path(name)
        with tarfile.open(path, "r:gz") as archive:
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
                  retention_days: int, host_config: list[Path]) -> Path:
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
        _copy_tree(settings.tenant_config_root, bundle / "configs")
        for source in host_config:
            if source.is_file():
                # The backup goes off-host; the master key does not. Refusing
                # here rather than filtering, because a caller that asked for
                # control.env has a wrong expectation worth failing on.
                if carries_master_key(source):
                    raise RuntimeError(
                        f"refusing to back up {source}: it assigns the credential "
                        f"master key, which must be kept off the backup path "
                        f"(the backup already contains the ciphertext this key opens)"
                    )
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
            "master_key_excluded": True,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create and verify a consistent xAuby SaaS backup")
    parser.add_argument("--output", required=True)
    parser.add_argument("--retention-days", type=int, default=7)
    parser.add_argument("--kind", choices=("daily", "predeploy"), default="daily")
    parser.add_argument("--release-id", default="scheduled")
    parser.add_argument("--host-config", action="append", default=[])
    parser.add_argument("--verify-only")
    args = parser.parse_args(argv)
    if args.verify_only:
        print(json.dumps(verify_backup(Path(args.verify_only).resolve()), sort_keys=True))
        return 0
    settings = SaaSSettings.from_env()
    final = create_backup(
        settings, Path(args.output).resolve(), kind=args.kind, release_id=args.release_id,
        retention_days=args.retention_days,
        host_config=[Path(item) for item in args.host_config],
    )
    print(final)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
