"""Create a compact, credential-free candle snapshot for the shadow worker.

SQLite WAL readers may need to update the source database's ``-shm`` file even
when opened with ``mode=ro``. The isolated shadow worker deliberately cannot do
that. This helper runs as the already-trusted control-plane identity, copies
only the bounded OHLCV rows named by the prepared spec, and atomically publishes
a clean DELETE-journal SQLite database under ``shadow/``. The worker then never
sees the live runtime database or any non-candle tables.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from xauby.saas.security import validate_tenant_slug
from xauby.shadow_evaluator import _validate_spec

SNAPSHOT_VERSION = 1
SNAPSHOT_NAME = "candles.db"


def _selected_spec(runtime_dir: Path) -> tuple[dict[str, Any], Path]:
    specs = sorted((runtime_dir / "shadow").glob("*/spec.json"))
    if len(specs) != 1:
        raise ValueError("snapshot requires exactly one prepared shadow pair")
    spec_path = specs[0].resolve()
    spec_path.relative_to(runtime_dir)
    raw = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("shadow spec must be an object")
    return raw, spec_path


def _source_rows(
    source: sqlite3.Connection,
    *,
    symbol: str,
    timeframe: str,
    limit: int,
) -> list[tuple[Any, ...]]:
    if not timeframe:
        return []
    rows = source.execute(
        "SELECT symbol,timeframe,timestamp,open,high,low,close,volume "
        "FROM prices WHERE symbol=? AND timeframe=? "
        "ORDER BY timestamp DESC LIMIT ?",
        (symbol, timeframe, limit),
    ).fetchall()
    return list(reversed(rows))


def create_snapshot(
    tenant: str,
    *,
    runtime_root: Path,
    source_db: Path | None = None,
) -> dict[str, Any]:
    slug = validate_tenant_slug(tenant)
    root = Path(runtime_root).resolve()
    runtime_dir = (root / slug).resolve()
    if runtime_dir.parent != root or not runtime_dir.is_dir():
        raise ValueError("tenant runtime directory is unavailable")
    raw, _spec_path = _selected_spec(runtime_dir)
    spec = _validate_spec(raw, slug)
    selected_source = (Path(source_db) if source_db else runtime_dir / "xauby.db").resolve()
    selected_source.relative_to(runtime_dir)
    if not selected_source.is_file():
        raise FileNotFoundError(f"tenant candle database not found: {selected_source}")

    symbol = str(spec["symbol"])
    primary_tf = str(spec["timeframe"])
    regime_tf = str(spec.get("regime_timeframe") or "")
    limit = int(spec["max_bars"])
    source = sqlite3.connect(
        f"file:{selected_source}?mode=ro",
        uri=True,
        timeout=5.0,
    )
    try:
        source.execute("PRAGMA query_only=ON")
        primary = _source_rows(
            source,
            symbol=symbol,
            timeframe=primary_tf,
            limit=limit,
        )
        regime = _source_rows(
            source,
            symbol=symbol,
            timeframe=regime_tf,
            limit=limit,
        )
    finally:
        source.close()
    if not primary:
        raise ValueError(f"no {symbol} {primary_tf} candles available for shadow snapshot")
    if regime_tf and not regime:
        raise ValueError(f"no {symbol} {regime_tf} candles available for shadow snapshot")

    shadow_root = (runtime_dir / "shadow").resolve()
    shadow_root.relative_to(runtime_dir)
    destination = shadow_root / SNAPSHOT_NAME
    # Do not stage in the xsh-writable shadow directory. A compromised worker
    # could race a predictable temporary path with a symlink into tenant state.
    # This control-only directory is on the same filesystem, so publication is
    # still one atomic os.replace rather than a cross-device copy.
    staging = runtime_dir / ".shadow-snapshot"
    if staging.is_symlink():
        raise ValueError("shadow snapshot staging path must not be a symlink")
    staging.mkdir(mode=0o700, exist_ok=True)
    if not staging.is_dir():
        raise ValueError("shadow snapshot staging path is not a directory")
    os.chmod(staging, 0o700)
    temporary = staging / f".{SNAPSHOT_NAME}.tmp"
    temporary.unlink(missing_ok=True)
    snapshot = sqlite3.connect(temporary)
    try:
        snapshot.execute("PRAGMA journal_mode=DELETE")
        snapshot.execute(
            "CREATE TABLE prices ("
            "symbol TEXT NOT NULL,timeframe TEXT NOT NULL,timestamp INTEGER NOT NULL,"
            "open REAL NOT NULL,high REAL NOT NULL,low REAL NOT NULL,close REAL NOT NULL,"
            "volume REAL NOT NULL,PRIMARY KEY(symbol,timeframe,timestamp))"
        )
        snapshot.executemany(
            "INSERT INTO prices(symbol,timeframe,timestamp,open,high,low,close,volume) "
            "VALUES(?,?,?,?,?,?,?,?)",
            primary + regime,
        )
        snapshot.execute(f"PRAGMA user_version={SNAPSHOT_VERSION}")
        snapshot.commit()
    finally:
        snapshot.close()
    os.chmod(temporary, 0o640)
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, destination)
    directory_fd = os.open(shadow_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return {
        "schema_version": SNAPSHOT_VERSION,
        "tenant": slug,
        "symbol": symbol,
        "timeframes": {
            primary_tf: len(primary),
            **({regime_tf: len(regime)} if regime_tf else {}),
        },
        "rows": len(primary) + len(regime),
        "destination": str(destination),
        "source_mode": "sqlite_mode_ro_query_only",
        "contains": "ohlcv_only",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", required=True)
    parser.add_argument(
        "--runtime-root",
        default=os.environ.get("XAUBY_TENANT_RUNTIME_ROOT", "/var/lib/xauby/runtime"),
    )
    args = parser.parse_args(argv)
    try:
        result = create_snapshot(args.tenant, runtime_root=Path(args.runtime_root))
    except Exception as exc:
        print(f"[ERR] shadow candle snapshot failed closed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
