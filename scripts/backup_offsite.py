#!/usr/bin/env python3
"""Copy a verified backup to off-host object storage. Roadmap P2.2.

`/etc/xauby/control.env` (master key), the encrypted control database and
`/var/lib/xauby/backups` all sit on one VPS. Losing the host loses every
tenant's exchange connection permanently. This closes the second half of P2.2:
the database bundle goes to S3-compatible object storage, and the master key
stays offline, held by the operator alone.

That split is the design, not an accident of configuration:

* An attacker who takes the bucket gets ciphertext they cannot open.
* An attacker who takes the VPS gets the key, but the backups are already
  elsewhere and this tool has **no delete verb**, so they cannot erase them.
* The cost is real and worth stating plainly: **lose the offline key and the
  backups are unrecoverable.** There is no escrow. Store it the way you would
  store a seed phrase.

## Configuration

In `/etc/xauby/control.env`, read by the backup unit:

    XAUBY_BACKUP_S3_ENDPOINT=https://s3.us-west-004.backblazeb2.com
    XAUBY_BACKUP_S3_BUCKET=xauby-backups
    XAUBY_BACKUP_S3_REGION=us-west-004
    XAUBY_BACKUP_S3_ACCESS_KEY_ID=...
    XAUBY_BACKUP_S3_SECRET_ACCESS_KEY=...
    XAUBY_BACKUP_S3_PREFIX=control-plane        # optional

Use a bucket key scoped to **write only** if the provider supports it, and set
retention with the provider's lifecycle rules — this tool cannot delete, by
design.

## Usage

    python -m scripts.backup_offsite --latest /var/lib/xauby/backups
    python -m scripts.backup_offsite --archive /var/lib/xauby/backups/xauby-saas-daily-....tar.gz
    python -m scripts.backup_offsite --check        # config only, no upload

Every path exits non-zero on failure, including "not configured". A backup
task that silently does nothing is the failure mode this whole roadmap phase
keeps finding.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.saas_backup import MASTER_KEY_MARKERS, verify_backup  # noqa: E402
from xauby.saas.objectstore import ObjectStoreError, S3Target, put_object  # noqa: E402


def target_from_env(env: dict[str, str] | None = None) -> S3Target:
    source = env if env is not None else os.environ
    return S3Target(
        endpoint=source.get("XAUBY_BACKUP_S3_ENDPOINT", "").strip(),
        bucket=source.get("XAUBY_BACKUP_S3_BUCKET", "").strip(),
        region=source.get("XAUBY_BACKUP_S3_REGION", "us-east-1").strip() or "us-east-1",
        access_key_id=source.get("XAUBY_BACKUP_S3_ACCESS_KEY_ID", "").strip(),
        secret_access_key=source.get("XAUBY_BACKUP_S3_SECRET_ACCESS_KEY", "").strip(),
        prefix=source.get("XAUBY_BACKUP_S3_PREFIX", "").strip(),
    )


def master_key_members(archive_path: Path) -> list[str]:
    """Members of the archive that assign a master key.

    `saas_backup.create_backup` already refuses to include such a file, so this
    is the second of two independent checks. It is here because this is the
    step that moves bytes off the host: the guard belongs closest to the
    irreversible action, not only at the point that is easy to bypass.
    """
    offenders: list[str] = []
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile() or member.size > 1_000_000:
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            try:
                text = handle.read().decode("utf-8", errors="ignore")
            except OSError:
                continue
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or "=" not in stripped:
                    continue
                name, value = stripped.split("=", 1)
                if name.strip() in MASTER_KEY_MARKERS and value.strip():
                    offenders.append(member.name)
                    break
    return offenders


def latest_archive(directory: Path) -> Path:
    candidates = sorted(directory.glob("xauby-saas-*.tar.gz"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"no backup archives in {directory}")
    return candidates[0]


def upload(archive_path: Path, target: S3Target, *, session=None) -> dict:
    # Verify before sending, not after. An archive that fails its own checksum
    # or SQLite integrity check is not a backup, and uploading it would replace
    # a real one in the operator's mind.
    manifest = verify_backup(archive_path)

    offenders = master_key_members(archive_path)
    if offenders:
        raise ObjectStoreError(
            "refusing to upload: the archive carries the credential master key in "
            + ", ".join(offenders)
            + ". The key must stay offline and off the backup path."
        )

    body = archive_path.read_bytes()
    result = put_object(target, target.key_for(archive_path.name), body, session=session)
    result["manifest"] = manifest
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Upload a verified xAuby SaaS backup to off-host object storage.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--archive", help="path to a specific backup archive")
    group.add_argument("--latest", help="directory to take the newest archive from")
    group.add_argument("--check", action="store_true",
                       help="report whether object storage is configured, upload nothing")
    args = parser.parse_args(argv)

    target = target_from_env()
    if args.check:
        if not target.configured:
            print("off-host backup is NOT configured; missing: "
                  + ", ".join(target.missing()))
            return 1
        print(f"off-host backup configured: {target.bucket} at {target.endpoint}")
        return 0

    if not target.configured:
        print("off-host backup is not configured; missing: " + ", ".join(target.missing()),
              file=sys.stderr)
        return 1

    try:
        archive_path = (Path(args.archive) if args.archive
                        else latest_archive(Path(args.latest))).resolve()
        result = upload(archive_path, target)
    except (ObjectStoreError, FileNotFoundError, RuntimeError) as exc:
        print(f"off-host backup failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({
        "uploaded": result["key"], "bytes": result["bytes"],
        "sha256": result["sha256"], "etag_verified": result["etag_verified"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
