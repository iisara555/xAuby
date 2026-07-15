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

from scripts.saas_backup import _safe_extract, verify_backup
from xauby.saas.settings import SaaSSettings


def _restore_database(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".restore.tmp")
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(temporary)) as dst:
        src.backup(dst)
    os.replace(temporary, target)
    for suffix in ("-wal", "-shm"):
        Path(str(target) + suffix).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify or restore an xAuby pre-deploy backup")
    parser.add_argument("archive")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-engines-stopped", action="store_true")
    args = parser.parse_args(argv)
    archive_path = Path(args.archive).resolve()
    manifest = verify_backup(archive_path)
    if not args.apply:
        print(json.dumps({"ok": True, "dry_run": True, "manifest": manifest}, sort_keys=True))
        return 0
    if not args.confirm_engines_stopped:
        raise SystemExit("Refusing restore: pass --confirm-engines-stopped after stopping all engines")
    settings = SaaSSettings.from_env()
    with tempfile.TemporaryDirectory(prefix="xauby-restore-") as name:
        root = Path(name)
        with tarfile.open(archive_path, "r:gz") as archive:
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
