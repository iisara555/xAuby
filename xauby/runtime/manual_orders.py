"""Fail-closed local IPC for confirmed manual trading requests."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any

from xauby.runtime.paths import manual_order_queue_path, manual_order_request_path
from xauby.utils.atomic_io import atomic_json_write

VALID_MANUAL_ACTIONS = {"BUY", "SELL"}
VALID_MANUAL_INTENTS = {
    "OPEN_LONG",
    "CLOSE_LONG",
    "OPEN_SHORT",
    "CLOSE_SHORT",
    "PARTIAL_CLOSE_LONG",
    "PARTIAL_CLOSE_SHORT",
}
VALID_MANAGEMENT_MODES = {"strategy", "manual"}
MANUAL_ORDER_MAX_AGE_SECONDS = 120.0


def _queue_connect(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS manual_order_commands (
            request_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            symbol TEXT NOT NULL,
            intent TEXT NOT NULL,
            fraction REAL,
            management_mode TEXT NOT NULL DEFAULT 'strategy',
            source TEXT NOT NULL,
            actor_user_id TEXT,
            expected_position_side TEXT,
            expected_quantity REAL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            claimed_at REAL,
            completed_at REAL,
            result_reason TEXT
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(manual_order_commands)")}
    if "expected_position_side" not in columns:
        conn.execute("ALTER TABLE manual_order_commands ADD COLUMN expected_position_side TEXT")
    if "expected_quantity" not in columns:
        conn.execute("ALTER TABLE manual_order_commands ADD COLUMN expected_quantity REAL")
    for shared_path in (path, f"{path}-wal", f"{path}-shm"):
        try:
            os.chmod(shared_path, 0o660)
        except OSError:
            pass
    return conn


@contextmanager
def _queue_connection(path: str):
    """Yield a transactional queue connection and always close it."""
    conn = _queue_connect(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def queue_manual_order_command(
    symbol: str,
    intent: str,
    *,
    idempotency_key: str,
    fraction: float | None = None,
    management_mode: str = "strategy",
    source: str = "saas",
    actor_user_id: str = "",
    expected_position_side: str = "",
    expected_quantity: float | None = None,
    queue_path: str | None = None,
    now: float | None = None,
    ttl_seconds: float = MANUAL_ORDER_MAX_AGE_SECONDS,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Insert one durable, idempotent manual command for a tenant engine."""
    sym = str(symbol or "").upper().replace("_", "")
    normalized_intent = str(intent or "").upper()
    idem = str(idempotency_key or "").strip()
    if not sym:
        raise ValueError("manual order symbol is required")
    if normalized_intent not in VALID_MANUAL_INTENTS:
        raise ValueError(f"manual order intent must be one of {sorted(VALID_MANUAL_INTENTS)}")
    if not idem or len(idem) > 128:
        raise ValueError("idempotency_key is required and must be at most 128 characters")
    mode = str(management_mode or "strategy").lower()
    if mode not in VALID_MANAGEMENT_MODES:
        raise ValueError(f"manual management mode must be one of {sorted(VALID_MANAGEMENT_MODES)}")
    if normalized_intent.startswith("PARTIAL_CLOSE"):
        try:
            fraction = float(fraction)
        except (TypeError, ValueError) as exc:
            raise ValueError("partial close fraction is required") from exc
        if not 0.0 < fraction < 1.0:
            raise ValueError("partial close fraction must be between 0 and 1")
    else:
        fraction = None
    created = float(time.time() if now is None else now)
    expires = created + max(1.0, min(float(ttl_seconds), 300.0))
    path = queue_path or manual_order_queue_path()
    request_id = str(request_id or uuid.uuid4().hex)
    if not re.fullmatch(r"[A-Fa-f0-9]{32}", request_id):
        raise ValueError("request_id must be a 32-character hexadecimal value")
    with _queue_connection(path) as conn:
        try:
            conn.execute(
                """
                INSERT INTO manual_order_commands
                (request_id, idempotency_key, symbol, intent, fraction,
                 management_mode, source, actor_user_id, expected_position_side,
                 expected_quantity, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id, idem, sym, normalized_intent, fraction, mode,
                    str(source or "saas"), str(actor_user_id or ""),
                    str(expected_position_side or "").upper(),
                    None if expected_quantity is None else float(expected_quantity), created, expires,
                ),
            )
        except sqlite3.IntegrityError:
            row = conn.execute(
                "SELECT * FROM manual_order_commands WHERE idempotency_key = ?", (idem,)
            ).fetchone()
            if row is None:
                raise
            same_command = (
                row["symbol"] == sym
                and row["intent"] == normalized_intent
                and (row["fraction"] is None and fraction is None
                     or row["fraction"] is not None and fraction is not None
                     and abs(float(row["fraction"]) - float(fraction)) < 1e-12)
            )
            if not same_command:
                raise ValueError(
                    "idempotency_key was already used for a different order"
                ) from None
            return dict(row)
        row = conn.execute(
            "SELECT * FROM manual_order_commands WHERE request_id = ?", (request_id,)
        ).fetchone()
    try:
        os.chmod(path, 0o660)
    except OSError:
        pass
    return dict(row)


def claim_queued_manual_order(
    symbol: str,
    *,
    queue_path: str | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Atomically claim the oldest valid command for ``symbol`` once."""
    path = queue_path or manual_order_queue_path()
    if not os.path.exists(path):
        return None
    current = float(time.time() if now is None else now)
    with _queue_connection(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE manual_order_commands SET status='ambiguous', completed_at=? "
            "WHERE status='claimed' AND claimed_at < ?",
            (current, current - MANUAL_ORDER_MAX_AGE_SECONDS),
        )
        conn.execute(
            "UPDATE manual_order_commands SET status='expired', completed_at=? "
            "WHERE status='pending' AND expires_at < ?",
            (current, current),
        )
        row = conn.execute(
            """
            SELECT * FROM manual_order_commands
            WHERE status='pending' AND symbol=? AND expires_at>=?
            ORDER BY created_at ASC LIMIT 1
            """,
            (str(symbol or "").upper().replace("_", ""), current),
        ).fetchone()
        if row is None:
            return None
        changed = conn.execute(
            "UPDATE manual_order_commands SET status='claimed', claimed_at=? "
            "WHERE request_id=? AND status='pending'",
            (current, row["request_id"]),
        ).rowcount
        if changed != 1:
            return None
        payload = dict(row)
        payload["status"] = "claimed"
        payload["action"] = payload["intent"]
        payload["_queue_db_path"] = path
        return payload


def complete_manual_order_command(
    request_id: str,
    *,
    success: bool,
    reason: str,
    queue_path: str | None = None,
    now: float | None = None,
) -> None:
    path = queue_path or manual_order_queue_path()
    if not request_id or not os.path.exists(path):
        return
    with _queue_connection(path) as conn:
        conn.execute(
            """
            UPDATE manual_order_commands
            SET status=?, completed_at=?, result_reason=?
            WHERE request_id=? AND status='claimed'
            """,
            (
                "executed" if success else "rejected",
                float(time.time() if now is None else now),
                str(reason or "")[:1000],
                request_id,
            ),
        )


def write_manual_order_request(
    symbol: str,
    action: str,
    *,
    management_mode: str = "strategy",
    source: str = "textual_tui",
    project_root: str = ".",
) -> dict[str, Any]:
    """Atomically queue one short-lived manual request for the engine."""
    sym = str(symbol or "").upper().replace("_", "")
    act = str(action or "").upper()
    if not sym:
        raise ValueError("manual order symbol is required")
    if act not in VALID_MANUAL_ACTIONS:
        raise ValueError(f"manual order action must be one of {sorted(VALID_MANUAL_ACTIONS)}")
    mode = str(management_mode or "strategy").lower()
    if mode not in VALID_MANAGEMENT_MODES:
        raise ValueError(
            f"manual management mode must be one of {sorted(VALID_MANAGEMENT_MODES)}"
        )
    payload = {
        "request_id": uuid.uuid4().hex,
        "symbol": sym,
        "action": act,
        "management_mode": mode,
        "source": str(source or "local"),
        "created_at": time.time(),
    }
    path = os.path.join(project_root, manual_order_request_path())
    atomic_json_write(path, payload, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return payload


def claim_manual_order_request(
    symbol: str,
    *,
    project_root: str = ".",
    now: float | None = None,
) -> dict[str, Any] | None:
    """Claim a matching request once; stale/invalid requests fail closed.

    A request for another active symbol is left in place so that symbol can
    claim it later in the same multi-pair engine tick.
    """
    queued = claim_queued_manual_order(symbol, now=now)
    if queued is not None:
        return queued

    path = os.path.join(project_root, manual_order_request_path())
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return None
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        return None

    requested_symbol = str(payload.get("symbol") or "").upper().replace("_", "")
    if requested_symbol != str(symbol or "").upper().replace("_", ""):
        return None

    # Remove before execution. A crash cannot replay a financial instruction.
    try:
        os.unlink(path)
    except FileNotFoundError:
        return None

    action = str(payload.get("action") or "").upper()
    try:
        created_at = float(payload.get("created_at") or 0.0)
    except (TypeError, ValueError):
        created_at = 0.0
    age = float(time.time() if now is None else now) - created_at
    if action not in VALID_MANUAL_ACTIONS or created_at <= 0 or age < -5 or age > MANUAL_ORDER_MAX_AGE_SECONDS:
        return None
    mode = str(payload.get("management_mode") or "strategy").lower()
    if mode not in VALID_MANAGEMENT_MODES:
        mode = "strategy"
    payload["action"] = action
    payload["symbol"] = requested_symbol
    payload["management_mode"] = mode
    return payload
