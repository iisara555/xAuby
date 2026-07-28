#!/usr/bin/env python3
"""Rotate the credential master key. Roadmap P2.2.

The `key_version` column existed from the start and nothing rotated. It was not
merely unused: both credential upserts wrote it as a SQL literal `1`, so the
schema could not have recorded a second key. This is the tool that makes the
column mean what its name always claimed.

## The runbook

Four commands, in order, on the control-plane host. Every one is safe to re-run.

    # 1. See where you are. Read-only; the default when no flag is given.
    python scripts/rotate_credential_key.py

    # 2. Mint a new active key and demote the current one to decrypt-only.
    #    Nothing is printed: the key goes straight into the env file, atomically,
    #    keeping its owner and mode.
    sudo python scripts/rotate_credential_key.py --stage-key
    sudo systemctl restart xauby-control.service

    # 3. Rewrap every stored envelope under the new key.
    sudo python scripts/rotate_credential_key.py --apply

    # 4. Once step 3 reports nothing left under the old key, drop it.
    sudo python scripts/rotate_credential_key.py --retire-complete
    sudo systemctl restart xauby-control.service

The restart after step 2 matters: the running control plane holds the old key
set in memory, and until it reloads it will keep writing new connections under
the old active key — which step 3 would then miss.

## What this tool will not do

* **It will not print or log key material or plaintext credentials.** Not on
  success, not in an error, not in the audit row. `--stage-key` writes the new
  key into the env file rather than to your terminal, because a key in
  scrollback is a key on disk somewhere you did not choose.
* **It will not overwrite an envelope it could not verify.** Each rewrap is
  decrypted back and compared before the write; the row it is about to replace
  is the only copy of that tenant's exchange keys.
* **It will not delete a key that blobs still need.** `--retire-complete`
  refuses while anything is still under a retired key, which is the whole
  reason the retired list exists.
* **It will not touch `status`, `tested_at` or `capabilities`.** Re-encrypting
  is not re-testing, and a rotation that reset live-activation state would be
  worse than the gap it closes.

Exit codes: 0 = nothing needs attention, 1 = something does (unreadable blob,
work still outstanding, a refused precondition).
"""
from __future__ import annotations

import argparse
import base64
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xauby.saas.credentials import (  # noqa: E402
    CredentialCipher,
    CredentialDecryptError,
    UnknownCredentialKey,
    parse_key_set,
)
from xauby.saas.keyfile import read_env, write_env  # noqa: E402
from xauby.saas.settings import SaaSSettings  # noqa: E402
from xauby.saas.store import ControlPlaneStore  # noqa: E402

ACTIVE_KEY = "XAUBY_CREDENTIAL_MASTER_KEY"
ACTIVE_KEY_ID = "XAUBY_CREDENTIAL_MASTER_KEY_ID"
RETIRED_KEYS = "XAUBY_CREDENTIAL_RETIRED_KEYS"


def _cipher(settings: SaaSSettings) -> CredentialCipher:
    return CredentialCipher(
        settings.credential_master_key,
        fallback_secret=settings.session_secret if settings.dev_login_enabled else "",
        active_key_id=settings.credential_master_key_id,
        retired_keys=settings.credential_retired_keys,
    )


def _survey(store: ControlPlaneStore, cipher: CredentialCipher) -> dict[str, list[dict]]:
    """Classify every blob without changing anything."""
    current: list[dict] = []
    stale: list[dict] = []
    unreadable: list[dict] = []
    for row in store.credential_blobs():
        try:
            key_id = CredentialCipher.key_id_of(row["blob"])
        except ValueError as exc:
            unreadable.append({**row, "reason": str(exc)})
            continue
        entry = {**row, "envelope_key_id": key_id}
        if key_id != row["key_version"]:
            # Not fatal — the envelope is self-describing and wins — but it
            # means some write path set the column without the blob.
            entry["column_disagrees"] = True
        if key_id == cipher.active_key_id:
            current.append(entry)
        else:
            stale.append(entry)
    return {"current": current, "stale": stale, "unreadable": unreadable}


def _describe(row: dict) -> str:
    return f"{row['kind']}/{row['tenant_id']}"


def cmd_status(store: ControlPlaneStore, cipher: CredentialCipher) -> int:
    survey = _survey(store, cipher)
    total = sum(len(v) for v in survey.values())
    print(f"active key id: {cipher.active_key_id}")
    print(f"retired key ids: {', '.join(map(str, cipher.retired_key_ids)) or '(none)'}")
    print(f"stored envelopes: {total}")
    print(f"  under active key: {len(survey['current'])}")
    print(f"  awaiting rewrap:  {len(survey['stale'])}")
    for row in survey["stale"]:
        print(f"    - {_describe(row)} (key id {row['envelope_key_id']})")
    for row in survey["current"]:
        if row.get("column_disagrees"):
            print(f"    ! {_describe(row)} key_version column says "
                  f"{row['key_version']}, envelope says {row['envelope_key_id']}")
    if survey["unreadable"]:
        print(f"  unreadable:       {len(survey['unreadable'])}")
        for row in survey["unreadable"]:
            print(f"    ! {_describe(row)}: {row['reason']}")
    if survey["stale"]:
        print("\nrun with --apply to rewrap")
    return 1 if (survey["stale"] or survey["unreadable"]) else 0


def cmd_apply(store: ControlPlaneStore, cipher: CredentialCipher) -> int:
    survey = _survey(store, cipher)
    if survey["unreadable"]:
        for row in survey["unreadable"]:
            print(f"SKIP {_describe(row)}: {row['reason']}")
    rewrapped = 0
    failed = 0
    for row in survey["stale"]:
        label = _describe(row)
        try:
            envelope = cipher.rewrap(row["tenant_id"], row["target_id"], row["blob"])
        except UnknownCredentialKey as exc:
            # The recoverable failure: put the key back and re-run.
            print(f"SKIP {label}: {exc}")
            failed += 1
            continue
        except CredentialDecryptError as exc:
            # The unrecoverable one. Say so plainly rather than implying a retry
            # will help — this tenant has to reconnect their keys.
            print(f"FAIL {label}: {exc}. This envelope cannot be recovered; "
                  f"the tenant must reconnect their credentials.")
            failed += 1
            continue
        if envelope is None:
            continue
        store.update_credential_blob(row["kind"], row["tenant_id"],
                                     envelope, cipher.active_key_id)
        print(f"rewrapped {label}: key {row['envelope_key_id']} -> {cipher.active_key_id}")
        rewrapped += 1

    # Envelopes already current whose column drifted: fix the column alone.
    relabelled = 0
    for row in survey["current"]:
        if row.get("column_disagrees"):
            store.update_credential_blob(row["kind"], row["tenant_id"],
                                         row["blob"], row["envelope_key_id"])
            relabelled += 1
    if relabelled:
        print(f"corrected {relabelled} key_version column(s) to match their envelope")

    print(f"\nrewrapped {rewrapped}, failed {failed}, "
          f"already current {len(survey['current'])}")
    if failed or survey["unreadable"]:
        return 1
    print("every envelope is under the active key; "
          "--retire-complete can now drop the old key")
    return 0


def cmd_stage_key(env_path: Path) -> int:
    """Mint a new active key, demote the current one to decrypt-only."""
    values = read_env(env_path)
    current = values.get(ACTIVE_KEY, "").strip()
    if not current:
        print(f"{env_path} has no {ACTIVE_KEY}; run ensure_control_master_key.py first")
        return 1
    try:
        if len(base64.b64decode(current, validate=True)) != 32:
            raise ValueError
    except ValueError:
        print(f"{ACTIVE_KEY} in {env_path} is not a valid 32-byte base64 key")
        return 1

    current_id = int(values.get(ACTIVE_KEY_ID, "1") or "1")
    retired = parse_key_set(values.get(RETIRED_KEYS, ""))
    if current_id in retired:
        print(f"key id {current_id} is already in {RETIRED_KEYS}; "
              "a previous --stage-key did not complete cleanly")
        return 1

    new_id = max([current_id, *retired]) + 1
    retired_text = ",".join(
        f"{key_id}:{base64.b64encode(material).decode('ascii')}"
        for key_id, material in sorted({**retired, current_id: base64.b64decode(current)}.items())
    )
    write_env(env_path, {
        ACTIVE_KEY: base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        ACTIVE_KEY_ID: str(new_id),
        RETIRED_KEYS: retired_text,
    })
    print(f"staged new active key id {new_id}; key id {current_id} is now decrypt-only")
    print("restart xauby-control.service, then run --apply")
    return 0


def cmd_retire_complete(store: ControlPlaneStore, cipher: CredentialCipher,
                        env_path: Path) -> int:
    survey = _survey(store, cipher)
    if survey["stale"] or survey["unreadable"]:
        print(f"refusing: {len(survey['stale'])} envelope(s) still under a retired key, "
              f"{len(survey['unreadable'])} unreadable. Run --apply first.")
        for row in survey["stale"] + survey["unreadable"]:
            print(f"  - {_describe(row)}")
        return 1
    if not read_env(env_path).get(RETIRED_KEYS, "").strip():
        print("no retired keys to drop; rotation is already complete")
        return 0
    write_env(env_path, {RETIRED_KEYS: None})
    print(f"dropped {RETIRED_KEYS}; restart xauby-control.service to release the old key")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rotate the SaaS credential master key.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(__doc__ or "").split("## The runbook", 1)[-1],
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true",
                       help="rewrap every envelope under the active key")
    group.add_argument("--stage-key", action="store_true",
                       help="mint a new active key, demote the current one to decrypt-only")
    group.add_argument("--retire-complete", action="store_true",
                       help="drop retired keys once nothing depends on them")
    parser.add_argument("--env-path", default=os.environ.get(
        "XAUBY_CONTROL_ENV", "/etc/xauby/control.env"))
    args = parser.parse_args()

    env_path = Path(args.env_path)
    if args.stage_key:
        return cmd_stage_key(env_path)

    settings = SaaSSettings.from_env()
    store = ControlPlaneStore(settings.database_path)
    store.migrate()
    cipher = _cipher(settings)

    if args.apply:
        return cmd_apply(store, cipher)
    if args.retire_complete:
        return cmd_retire_complete(store, cipher, env_path)
    return cmd_status(store, cipher)


if __name__ == "__main__":
    raise SystemExit(main())
