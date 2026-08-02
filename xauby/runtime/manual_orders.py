"""Fail-closed local IPC for confirmed manual trading requests."""

from __future__ import annotations

import json
import logging
import math
import os
import time
import uuid
from typing import Any, Dict, Optional

from xauby.runtime.paths import manual_order_request_path
from xauby.utils.atomic_io import atomic_json_write

logger = logging.getLogger(__name__)

VALID_MANUAL_ACTIONS = {"BUY", "SELL"}
VALID_MANUAL_INTENTS = {"OPEN_LONG", "OPEN_SHORT", "CLOSE_POSITION"}
VALID_MANAGEMENT_MODES = {"strategy", "strategy_handoff", "manual"}
MANUAL_ORDER_MAX_AGE_SECONDS = 120.0


def write_manual_order_request(
    symbol: str,
    action: str,
    *,
    management_mode: str = "strategy",
    source: str = "textual_tui",
    project_root: str = ".",
    request_id: str | None = None,
    request_path: str | None = None,
) -> Dict[str, Any]:
    """Atomically queue one short-lived manual request for the engine."""
    sym = str(symbol or "").upper().replace("_", "")
    supplied = str(action or "").upper()
    if supplied in VALID_MANUAL_INTENTS:
        intent = supplied
        act = "BUY" if intent == "OPEN_LONG" else "SELL"
    else:
        act = supplied
        intent = "OPEN_LONG" if act == "BUY" else "CLOSE_POSITION"
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
        "version": 2,
        "request_id": str(request_id or uuid.uuid4().hex),
        "symbol": sym,
        "action": act,
        "intent": intent,
        "position_side": (
            "SHORT" if intent == "OPEN_SHORT"
            else "LONG" if intent == "OPEN_LONG" else None
        ),
        "management_mode": mode,
        "source": str(source or "local"),
        "created_at": time.time(),
    }
    path = request_path or os.path.join(project_root, manual_order_request_path())
    # Hosted control and engine services run as separate OS users. Tenant
    # runtime directories provide a named ACL for the isolated engine user;
    # mode 0640 keeps the file private while opening the ACL mask so that user
    # can claim it. The payload contains order intent only, never credentials.
    atomic_json_write(path, payload, indent=2, mode=0o640)
    return payload


def claim_manual_order_request(
    symbol: str,
    *,
    project_root: str = ".",
    now: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Claim a matching request once; stale/invalid requests fail closed.

    A request for another active symbol is left in place so that symbol can
    claim it later in the same multi-pair engine tick.
    """
    path = os.path.join(project_root, manual_order_request_path())
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.error(
            "Discarding unreadable manual order request at %s: %s",
            path,
            exc,
        )
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
    intent = str(payload.get("intent") or "").upper()
    if not intent:
        intent = "OPEN_LONG" if action == "BUY" else "CLOSE_POSITION"
    try:
        created_at = float(payload.get("created_at") or 0.0)
        current_time = float(time.time() if now is None else now)
    except (TypeError, ValueError, OverflowError):
        created_at = 0.0
        current_time = 0.0
    age = current_time - created_at
    if (
        action not in VALID_MANUAL_ACTIONS
        or intent not in VALID_MANUAL_INTENTS
        or not math.isfinite(created_at)
        or not math.isfinite(current_time)
        or not math.isfinite(age)
        or created_at <= 0
        or age < -5
        or age > MANUAL_ORDER_MAX_AGE_SECONDS
    ):
        return None
    mode = str(payload.get("management_mode") or "strategy").lower()
    if mode not in VALID_MANAGEMENT_MODES:
        mode = "strategy"
    payload["action"] = action
    payload["intent"] = intent
    payload["symbol"] = requested_symbol
    payload["management_mode"] = mode
    return payload
