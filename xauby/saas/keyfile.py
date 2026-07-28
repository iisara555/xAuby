"""Atomic, ownership-preserving edits to the control-plane env file.

Roadmap P2.2. `/etc/xauby/control.env` holds the credential master key and is
mode 0640 `root:xauby-control`. Two things have to be true of every write to
it: it is atomic (a half-written file means the control plane cannot decrypt
anything on next start), and it keeps its owner and mode (CLAUDE.md records
that overwriting a privileged file with a fresh copy is how ownership gets
destroyed).

`scripts/ensure_control_master_key.py` already got this right. This module is
that logic lifted out so the rotation runbook shares it rather than carrying a
second copy — the repeated lesson in this codebase is that two copies of one
thing drift, and a drifted copy here corrupts the file that opens every
tenant's exchange keys.
"""
from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    """Parse ``KEY=value`` lines. Comments, blanks and malformed lines ignored."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def write_env(path: Path, updates: Mapping[str, str | None]) -> bool:
    """Apply ``updates`` in place. A ``None`` value removes the key.

    Returns True when the file changed. Unrelated lines, including comments,
    are preserved in their original order — this file is hand-edited by
    operators and silently reformatting it would lose their notes.
    """
    stat = path.stat()
    lines = path.read_text(encoding="utf-8").splitlines()
    output: list[str] = []
    seen: set[str] = set()
    changed = False

    for line in lines:
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
        if not key or key.startswith("#") or key not in updates:
            output.append(line)
            continue
        # Every occurrence is rewritten, not just the first. A duplicate line
        # left behind would shadow the new value depending on which one the
        # loader reads last — on the file that holds the master key.
        seen.add(key)
        value = updates[key]
        if value is None:
            changed = True          # dropped
            continue
        replacement = f"{key}={value}"
        changed = changed or replacement != line
        output.append(replacement)

    for key, value in updates.items():
        if key not in seen and value is not None:
            output.append(f"{key}={value}")
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
        try:
            os.chown(temporary, stat.st_uid, stat.st_gid)
        except PermissionError:
            # Non-root edit of a file we already own: the mode carried over and
            # the owner is unchanged, so there is nothing to restore.
            if stat.st_uid != os.getuid():
                raise
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return True
