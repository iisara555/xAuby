#!/usr/bin/env python3
"""Rotate SaaS credential encryption without exposing any key material.

Run only after a verified backup and after stopping the control-plane and all
tenant engines.  The old key remains in the keyring so historical encrypted
backups stay restorable until their retention window has elapsed.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from xauby.saas.credentials import CredentialKeyring
from xauby.saas.key_rotation import (
    new_master_key,
    previous_keys_value,
    rotate_credential_keyring,
    update_environment,
    validate_credential_keyring,
)
from xauby.saas.settings import SaaSSettings
from xauby.saas.store import ControlPlaneStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rotate the SaaS credential master key")
    parser.add_argument("--control-env", default="/etc/xauby/control.env")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-services-stopped", action="store_true")
    args = parser.parse_args(argv)
    control_env = Path(args.control_env).resolve()
    prior_env = os.environ.get("XAUBY_CONTROL_ENV")
    os.environ["XAUBY_CONTROL_ENV"] = str(control_env)
    try:
        settings = SaaSSettings.from_env()
    finally:
        if prior_env is None:
            os.environ.pop("XAUBY_CONTROL_ENV", None)
        else:
            os.environ["XAUBY_CONTROL_ENV"] = prior_env
    store = ControlPlaneStore(settings.database_path)
    store.migrate()
    source = CredentialKeyring(
        settings.credential_master_key,
        active_version=settings.credential_master_key_version,
        previous_keys=settings.credential_previous_keys,
    )
    before = validate_credential_keyring(store, source)
    next_version = source.active_version + 1
    next_key = new_master_key()
    previous = source.keys_for_recovery()
    destination = CredentialKeyring(
        next_key,
        active_version=next_version,
        previous_keys=previous_keys_value(previous),
    )
    preview = {"ok": True, "dry_run": not args.apply, "next_key_version": next_version, **before}
    if not args.apply:
        print(json.dumps(preview, sort_keys=True))
        return 0
    if not args.confirm_services_stopped:
        raise SystemExit("Refusing rotation: pass --confirm-services-stopped after stopping all services")
    # Stage both the new key and the old keyring first.  A power loss here is
    # safe because the deployed keyring can read the old row versions.
    update_environment(control_env, {
        "XAUBY_CREDENTIAL_MASTER_KEY": next_key,
        "XAUBY_CREDENTIAL_MASTER_KEY_VERSION": str(next_version),
        "XAUBY_CREDENTIAL_PREVIOUS_KEYS": destination.previous_keys_value(),
    })
    counts = rotate_credential_keyring(store, source=source, destination=destination)
    validate_credential_keyring(store, destination)
    print(json.dumps({"ok": True, "next_key_version": next_version, **counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
