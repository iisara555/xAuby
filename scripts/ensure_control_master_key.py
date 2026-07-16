#!/usr/bin/env python3
"""Atomically provision the SaaS credential master key without printing it."""

from __future__ import annotations

import argparse
import base64
import os
import secrets
import tempfile
from pathlib import Path


KEY = "XAUBY_CREDENTIAL_MASTER_KEY"


def ensure_master_key(path: Path) -> bool:
    stat = path.stat()
    lines = path.read_text(encoding="utf-8").splitlines()
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
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("\n".join(output) + "\n")
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
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default="/etc/xauby/control.env")
    args = parser.parse_args()
    changed = ensure_master_key(Path(args.path))
    print("credential master key installed" if changed else "credential master key already valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
