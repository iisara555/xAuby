from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tarfile
import tempfile
import time
from contextlib import closing
from pathlib import Path


RUNTIME_FILES = (
    "sentiment_guard_state.json",
    "equity_peak.json",
    "dashboard_focus.json",
)
CONFIG_FILES = ("bot_config.yaml", "coin_whitelist.json")
CREDENTIAL_KEYS = ("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
            result = dst.execute("PRAGMA integrity_check").fetchone()
    if not result or result[0] != "ok":
        raise RuntimeError("legacy SQLite backup failed integrity_check")


def _copy(source: Path, destination: Path) -> None:
    if not source.is_file():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())
    os.chmod(destination, 0o600)


def _env_presence(path: Path) -> dict[str, bool]:
    present = {key: False for key in CREDENTIAL_KEYS}
    if not path.is_file():
        return present
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in present:
            present[key.strip()] = bool(value.strip().strip('"').strip("'"))
    return present


def _position_summary(path: Path) -> dict[str, object]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {"available": False}
    position = state.get("position") if isinstance(state, dict) else None
    if not isinstance(position, dict):
        return {"available": True, "open": False}
    return {
        "available": True,
        "open": position.get("state") == "bought",
        "symbol": state.get("symbol") or "XAUUSDT",
        "side": position.get("position_side"),
        "quantity": position.get("quantity"),
        "entry_price": position.get("entry_price"),
        "leverage": position.get("leverage"),
        "margin_mode": position.get("margin_mode"),
        "stop_loss_order_present": bool(position.get("stop_loss_order_id")),
    }


def create_backup(project_root: Path, runtime_root: Path, output: Path, slug: str) -> Path:
    database = runtime_root / "xauby.db"
    state = runtime_root / "logs" / "xauby_bot_state.json"
    if not database.is_file():
        raise FileNotFoundError(f"legacy database not found: {database}")
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    output.mkdir(parents=True, exist_ok=True)
    final = output / f"xauby-legacy-handoff-{stamp}-{slug}.tar.gz"
    with tempfile.TemporaryDirectory(prefix="xauby-legacy-handoff-") as name:
        bundle = Path(name) / "xauby-legacy-handoff"
        tenant_runtime = bundle / "runtime" / slug
        tenant_config = bundle / "configs" / slug
        _sqlite_backup(database, tenant_runtime / "xauby.db")
        _copy(state, tenant_runtime / "logs" / state.name)
        for filename in RUNTIME_FILES:
            _copy(runtime_root / filename, tenant_runtime / filename)
        for path in runtime_root.glob("simulated_balance*.json"):
            _copy(path, tenant_runtime / path.name)
        for filename in CONFIG_FILES:
            _copy(project_root / filename, tenant_config / filename)

        manifest = {
            "format": 1,
            "kind": "legacy_live_handoff",
            "created_at": time.time(),
            "tenant_slug": slug,
            "source_database": str(database),
            "database_integrity": "ok",
            "position": _position_summary(state),
            "credential_presence": _env_presence(project_root / ".env"),
            "contains_plaintext_credentials": False,
            "requires_manual_exchange_protection": True,
        }
        (bundle / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        rows = []
        for path in sorted(bundle.rglob("*")):
            if path.is_file() and path.name != "checksums.sha256":
                rows.append(f"{_sha256(path)}  {path.relative_to(bundle).as_posix()}")
        (bundle / "checksums.sha256").write_text("\n".join(rows) + "\n", encoding="utf-8")
        with tarfile.open(final, "w:gz") as archive:
            archive.add(bundle, arcname=bundle.name)
    os.chmod(final, 0o600)
    return final


def verify_backup(path: Path) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="xauby-legacy-verify-") as name:
        root = Path(name).resolve()
        with tarfile.open(path, "r:gz") as archive:
            for member in archive.getmembers():
                target = (root / member.name).resolve()
                if root != target and root not in target.parents:
                    raise RuntimeError("unsafe backup member")
            archive.extractall(root, filter="data")
        bundle = root / "xauby-legacy-handoff"
        for line in (bundle / "checksums.sha256").read_text(encoding="utf-8").splitlines():
            expected, relative = line.split("  ", 1)
            target = bundle / relative
            if not target.is_file() or _sha256(target) != expected:
                raise RuntimeError(f"checksum verification failed: {relative}")
        for database in bundle.rglob("*.db"):
            with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as conn:
                result = conn.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError(f"database verification failed: {database.name}")
        return json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a credential-free legacy handoff backup")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--runtime-root", default="core")
    parser.add_argument("--output", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--verify-only")
    args = parser.parse_args(argv)
    if args.verify_only:
        print(json.dumps(verify_backup(Path(args.verify_only).resolve()), sort_keys=True))
        return 0
    archive = create_backup(
        Path(args.project_root).resolve(), Path(args.runtime_root).resolve(),
        Path(args.output).resolve(), args.tenant,
    )
    manifest = verify_backup(archive)
    print(json.dumps({"ok": True, "archive": str(archive), "manifest": manifest}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
