"""User-facing copy for TUI empty / monitoring states (English)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from xauby.utils.colors import C_BOLD, C_GEMINI_CYAN, C_MUTED, C_RESET

MONITORING_LABEL = "◉ Monitoring for signals"
MONITORING_SUBLINE = "Waiting for entry · no open position"
HISTORY_EMPTY = "No closed trade history yet"
PAIR_WATCHING = "Watching"

# Backward-compatible aliases (deprecated)
MONITORING_LABEL_TH = MONITORING_LABEL
MONITORING_LABEL_EN = MONITORING_LABEL
MONITORING_SUBLINE_TH = MONITORING_SUBLINE
HISTORY_EMPTY_TH = HISTORY_EMPTY
PAIR_WATCHING_TH = PAIR_WATCHING


def has_open_position(state: Dict[str, Any]) -> bool:
    pos = state.get("position") or {}
    return str(pos.get("state", "idle")).lower() == "bought"


def has_any_open_position(envelope: Dict[str, Any]) -> bool:
    by_symbol = envelope.get("by_symbol") or {}
    for snap in by_symbol.values():
        if isinstance(snap, dict) and has_open_position(snap):
            return True
    return False


def is_monitoring_empty_state(
    state: Dict[str, Any],
    envelope: Optional[Dict[str, Any]] = None,
) -> bool:
    """Engine is up, no open positions across known pairs."""
    if not state or not state.get("symbol"):
        return False
    if has_open_position(state):
        return False
    if envelope and has_any_open_position(envelope):
        return False
    return True


def format_monitoring_empty(*, compact: bool = False, with_subline: bool = True) -> str:
    if compact:
        return f"  {C_GEMINI_CYAN}{MONITORING_LABEL}{C_RESET}"
    if with_subline:
        return (
            f"  {C_BOLD}{C_GEMINI_CYAN}{MONITORING_LABEL}{C_RESET}\n"
            f"  {C_MUTED}{MONITORING_SUBLINE}{C_RESET}"
        )
    return f"  {C_BOLD}{C_GEMINI_CYAN}{MONITORING_LABEL}{C_RESET}"


def format_history_empty(*, compact: bool = False) -> str:
    if compact:
        return f"{C_MUTED}{HISTORY_EMPTY}{C_RESET}"
    return f"  {C_MUTED}{HISTORY_EMPTY}{C_RESET}"


def format_tradelog_empty(*, engine_running: bool = True) -> str:
    if engine_running:
        return f"{C_GEMINI_CYAN}{MONITORING_LABEL}{C_RESET} · {C_MUTED}{HISTORY_EMPTY}{C_RESET}"
    return format_history_empty(compact=True)
