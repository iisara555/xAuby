from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from contextlib import closing
from pathlib import Path

from scripts.saas_backup import _materialize_archive, _safe_extract, verify_backup
from xauby.saas.credentials import CredentialKeyring
from xauby.saas.settings import SaaSSettings


def _restore_database(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".restore.tmp")
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(temporary)) as dst:
        src.backup(dst)
    os.replace(temporary, target)
    for suffix in ("-wal", "-shm"):
        Path(str(target) + suffix).unlink(missing_ok=True)


def restore_drill(archive_path: Path, settings: SaaSSettings) -> dict[str, object]:
    """Verify a backup can be extracted and its encrypted credentials decrypted.

    This never writes to the production DB, tenant config, or runtime state.
    It is the regular evidence that the off-site archive *and* the separately
    managed master-key lineage remain usable together.
    """

    manifest = verify_backup(archive_path, encryption_key=settings.backup_encryption_key)
    keyring = CredentialKeyring(
        settings.credential_master_key,
        active_version=settings.credential_master_key_version,
        previous_keys=settings.credential_previous_keys,
    )
    counts = {"exchange_credentials": 0, "telegram_credentials": 0}
    with tempfile.TemporaryDirectory(prefix="xauby-restore-drill-") as name:
        root = Path(name)
        archive = _materialize_archive(archive_path, root, settings.backup_encryption_key)
        with tarfile.open(archive, "r:gz") as handle:
            _safe_extract(handle, root)
        control = root / "xauby-saas" / "databases" / "control-plane.db"
        if control.is_file():
            with closing(sqlite3.connect(control)) as conn:
                conn.row_factory = sqlite3.Row
                try:
                    exchanges = conn.execute(
                        "SELECT tenant_id,target_id,credential_blob,key_version FROM exchange_connections "
                        "WHERE credential_blob IS NOT NULL"
                    ).fetchall()
                    telegram = conn.execute(
                        "SELECT tenant_id,credential_blob,key_version FROM telegram_connections "
                        "WHERE credential_blob IS NOT NULL"
                    ).fetchall()
                except sqlite3.OperationalError as exc:
                    raise RuntimeError("backup control database lacks credential schema") from exc
                for row in exchanges:
                    keyring.decrypt(
                        str(row["tenant_id"]), str(row["target_id"]), str(row["credential_blob"]),
                        key_version=int(row["key_version"] or 1),
                    )
                    counts["exchange_credentials"] += 1
                for row in telegram:
                    keyring.decrypt(
                        str(row["tenant_id"]), "telegram", str(row["credential_blob"]),
                        key_version=int(row["key_version"] or 1),
                    )
                    counts["telegram_credentials"] += 1
    return {"ok": True, "dry_run": True, "manifest": manifest, **counts}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify or restore an xAuby pre-deploy backup")
    parser.add_argument("archive")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--restore-drill", action="store_true")
    parser.add_argument("--confirm-engines-stopped", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and args.restore_drill:
        raise SystemExit("choose either --apply or --restore-drill")
    archive_path = Path(args.archive).resolve()
    settings = SaaSSettings.from_env()
    manifest = verify_backup(archive_path, encryption_key=settings.backup_encryption_key)
    if args.restore_drill:
        print(json.dumps(restore_drill(archive_path, settings), sort_keys=True))
        return 0
    if not args.apply:
        print(json.dumps({"ok": True, "dry_run": True, "manifest": manifest}, sort_keys=True))
        return 0
    if not args.confirm_engines_stopped:
        raise SystemExit("Refusing restore: pass --confirm-engines-stopped after stopping all engines")
    with tempfile.TemporaryDirectory(prefix="xauby-restore-") as name:
        root = Path(name)
        materialized = _materialize_archive(archive_path, root, settings.backup_encryption_key)
        with tarfile.open(materialized, "r:gz") as archive:
            _safe_extract(archive, root)
        bundle = root / "xauby-saas"
        control = bundle / "databases" / "control-plane.db"
        if control.exists():
            _restore_database(control, settings.database_path)
        runtime = bundle / "databases" / "runtime"
        for tenant in runtime.iterdir() if runtime.exists() else []:
            for database in tenant.glob("*.db"):
                _restore_database(database, settings.tenant_runtime_root / tenant.name / database.name)
        configs = bundle / "configs"
        if configs.exists():
            for source in configs.rglob("*"):
                if source.is_file():
                    target = settings.tenant_config_root / source.relative_to(configs)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    print(json.dumps({"ok": True, "restored_release": manifest.get("release_id")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
