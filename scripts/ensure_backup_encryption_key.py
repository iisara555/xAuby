#!/usr/bin/env python3
"""Atomically provision the distinct AES-256 key used for off-site backups."""

from __future__ import annotations

import argparse
import base64
import os
import secrets
import tempfile
from pathlib import Path


KEY = "XAUBY_BACKUP_ENCRYPTION_KEY"


def ensure_backup_key(path: Path) -> bool:
    if path.exists():
        stat = path.stat()
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        stat = None
        lines = []
    generated = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    output: list[str] = []
    found = False
    changed = False
    for line in lines:
        if not line.startswith(f"{KEY}="):
            output.append(line)
            continue
        found = True
        value = line.split("=", 1)[1].strip()
        try:
            valid = len(base64.b64decode(value, validate=True)) == 32
        except ValueError:
            valid = False
        if valid:
            output.append(line)
        else:
            output.append(f"{KEY}={generated}")
            changed = True
    if not found:
        output.append(f"{KEY}={generated}")
        changed = True
    if not changed:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("\n".join(output) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if stat is not None:
            os.chmod(temporary, stat.st_mode & 0o777)
            os.chown(temporary, stat.st_uid, stat.st_gid)
        else:
            os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default="/etc/xauby/backup.env")
    args = parser.parse_args()
    changed = ensure_backup_key(Path(args.path))
    print("backup encryption key installed" if changed else "backup encryption key already valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
