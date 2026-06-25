"""Structured logging setup with run_id / tick_id context."""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from contextvars import ContextVar
from typing import Any, Dict, Optional

from xauby.observability.events import new_run_id
from xauby.utils.retention import cleanup_rotated_logs

_run_id: ContextVar[str] = ContextVar("run_id", default="")
_tick_id: ContextVar[str] = ContextVar("tick_id", default="")


def get_run_id() -> str:
    rid = _run_id.get()
    if not rid:
        rid = new_run_id()
        _run_id.set(rid)
    return rid


def set_run_id(run_id: str) -> None:
    _run_id.set(run_id)


def set_tick_id(tick_id: str) -> None:
    _tick_id.set(tick_id)


def clear_tick_id() -> None:
    _tick_id.set("")


class StructuredFormatter(logging.Formatter):
    """Human-readable console + structured file lines.

    Preserves ``[LEVEL]`` tokens so health_check log scanning keeps working.
    """

    def __init__(self, structured: bool = False):
        super().__init__()
        self.structured = structured

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        level = record.levelname
        name = record.name
        msg = record.getMessage()
        rid = _run_id.get() or "-"
        tid = _tick_id.get() or "-"

        if self.structured:
            return (
                f"{ts} [{level}] {name} run={rid} tick={tid} | {msg}"
            )
        return f"{ts} [{level}] {name}: {msg}"


def _resolve_log_level(cfg: Dict[str, Any], fallback: int) -> int:
    name = str(cfg.get("log_level", "INFO")).upper()
    return getattr(logging, name, fallback)


def setup_logging(
    log_path: Optional[str] = None,
    level: int = logging.INFO,
    run_id: Optional[str] = None,
    logging_cfg: Optional[Dict[str, Any]] = None,
) -> str:
    """Configure root + lite_bot loggers. Returns the active run_id."""
    from xauby.runtime.paths import log_path as _log_path, logs_dir

    cfg = logging_cfg or {}
    rid = run_id or new_run_id()
    set_run_id(rid)

    log_path = log_path or _log_path("xauby_bot.log")
    os.makedirs(os.path.dirname(log_path) or logs_dir(), exist_ok=True)
    if cfg.get("cleanup_on_startup", True):
        cleanup_rotated_logs(cfg, log_dir=os.path.dirname(log_path) or logs_dir())

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(_resolve_log_level(cfg, level))

    if cfg.get("enable_console", True):
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(StructuredFormatter(structured=False))
        root.addHandler(console)

    if cfg.get("enable_files", True):
        max_bytes = int(cfg.get("max_log_size_mb", 100)) * 1024 * 1024
        backup_count = int(cfg.get("backup_count", 10))
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=max(1024, max_bytes),
            backupCount=max(1, backup_count),
            encoding="utf-8",
        )
        file_handler.setFormatter(StructuredFormatter(structured=True))
        root.addHandler(file_handler)

    return rid


class TickContext:
    """Context manager: bind tick_id for the duration of one engine tick."""

    def __init__(self, tick_id: str):
        self.tick_id = tick_id
        self._token = None

    def __enter__(self):
        self._token = _tick_id.set(self.tick_id)
        return self

    def __exit__(self, *args):
        if self._token is not None:
            _tick_id.reset(self._token)
