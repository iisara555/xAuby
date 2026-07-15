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


def _run(project: Path, *command: str) -> str:
    result = subprocess.run(command, cwd=project, capture_output=True, text=True, check=False)
    return result.stdout + result.stderr


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_legacy_backup(project: Path, output: Path, release_id: str) -> Path:
    project, output = project.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    final = output / f"xauby-legacy-predeploy-{stamp}-{release_id}.tar.gz"
    with tempfile.TemporaryDirectory(prefix="xauby-legacy-backup-") as name:
        bundle = Path(name) / "xauby-legacy"
        bundle.mkdir()
        database = project / "core" / "xauby.db"
        if database.exists():
            target = bundle / "runtime" / "xauby.db"
            target.parent.mkdir(parents=True)
            with closing(sqlite3.connect(database)) as src, closing(sqlite3.connect(target)) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("legacy database integrity check failed")
        for relative in (
            "bot_config.yaml", "coin_whitelist.json", ".env", "core/logs/xauby_bot_state.json",
            "core/sentiment_guard_state.json",
        ):
            source = project / relative
            if source.is_file():
                target = bundle / "files" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        (bundle / "git-status.txt").write_text(_run(project, "git", "status", "--short"), encoding="utf-8")
        (bundle / "worktree.patch").write_text(
            _run(project, "git", "diff", "--binary", "HEAD"), encoding="utf-8"
        )
        manifest = {
            "created_at": time.time(), "release_id": release_id,
            "git_commit": _run(project, "git", "rev-parse", "HEAD").strip(),
            "database_source": str(database), "online_backup": True,
        }
        (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        rows = [
            f"{_hash(path)}  {path.relative_to(bundle).as_posix()}"
            for path in sorted(bundle.rglob("*")) if path.is_file()
        ]
        (bundle / "checksums.sha256").write_text("\n".join(rows) + "\n", encoding="utf-8")
        with tarfile.open(final, "w:gz") as archive:
            archive.add(bundle, arcname="xauby-legacy")
    os.chmod(final, 0o600)
    with tarfile.open(final, "r:gz") as archive:
        names = set(archive.getnames())
    if "xauby-legacy/runtime/xauby.db" not in names or "xauby-legacy/checksums.sha256" not in names:
        final.unlink(missing_ok=True)
        raise RuntimeError("legacy backup verification failed")
    return final


def main() -> int:
    parser = argparse.ArgumentParser(description="Online backup of the legacy single-engine xAuby host")
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--release-id", required=True)
    args = parser.parse_args()
    print(create_legacy_backup(Path(args.project), Path(args.output), args.release_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
