"""Runtime operator controls driven by Telegram commands."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict

from xauby.runtime.paths import runtime_path


def control_path() -> str:
    return runtime_path("telegram_control.json")


def load_control_state() -> Dict[str, Any]:
    path = control_path()
    if not os.path.exists(path):
        return {"trading_paused": False}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        return {
            "trading_paused": bool(data.get("trading_paused", False)),
            "reason": str(data.get("reason") or ""),
            "updated_at": str(data.get("updated_at") or ""),
            "updated_by": str(data.get("updated_by") or ""),
        }
    except Exception:
        return {"trading_paused": False, "reason": "control state unreadable"}


def set_trading_paused(paused: bool, *, reason: str = "", actor: str = "telegram") -> Dict[str, Any]:
    state = {
        "trading_paused": bool(paused),
        "reason": str(reason or ("telegram pause" if paused else "telegram resume")),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": str(actor or "telegram"),
    }
    path = control_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    return state


def trading_pause_reason() -> tuple[bool, str]:
    state = load_control_state()
    if not bool(state.get("trading_paused", False)):
        return False, ""
    reason = str(state.get("reason") or "paused by Telegram")
    updated_at = str(state.get("updated_at") or "")
    if updated_at:
        reason = f"{reason} at {updated_at[:19]}"
    return True, reason
