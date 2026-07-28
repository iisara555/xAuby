#!/usr/bin/env python3
"""Atomically provision the SaaS credential master key without printing it."""

from __future__ import annotations

import argparse
import base64
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xauby.saas.keyfile import read_env, write_env  # noqa: E402

KEY = "XAUBY_CREDENTIAL_MASTER_KEY"


def ensure_master_key(path: Path) -> bool:
    current = read_env(path).get(KEY, "")
    try:
        valid = len(base64.b64decode(current, validate=True)) == 32
    except ValueError:
        valid = False
    if valid:
        return False
    generated = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    return write_env(path, {KEY: generated})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default="/etc/xauby/control.env")
    args = parser.parse_args()
    changed = ensure_master_key(Path(args.path))
    print("credential master key installed" if changed else "credential master key already valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
