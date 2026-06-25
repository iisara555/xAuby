"""Config-driven data retention: logs, events, backups."""
from __future__ import annotations

import glob
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("xauby.retention")

_JSONL_NAME = re.compile(r"^events-(\d{8})\.jsonl$")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_cutoff(days: int) -> str:
    return (_utc_now() - timedelta(days=max(0, int(days)))).replace(tzinfo=None).isoformat()


def cleanup_rotated_logs(
    cfg: Optional[Dict[str, Any]] = None,
    log_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Delete rotated log fragments older than debug_retention_days (by mtime)."""
    from xauby.runtime.paths import logs_dir
    log_dir = log_dir or logs_dir()
    cfg = cfg or {}
    stats = {"deleted": 0, "skipped": False}
    if not cfg.get("cleanup_on_startup", True):
        stats["skipped"] = True
        return stats

    retention_days = int(cfg.get("debug_retention_days", 30))
    cutoff = _utc_now().timestamp() - retention_days * 86400
    if not os.path.isdir(log_dir):
        return stats

    for path in glob.glob(os.path.join(log_dir, "*.log.*")):
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                stats["deleted"] += 1
        except OSError as e:
            logger.debug("Could not remove old log %s: %s", path, e)
    return stats


def prune_jsonl_events(
    cfg: Optional[Dict[str, Any]] = None,
    jsonl_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Remove JSONL event files older than jsonl_retention_days."""
    from xauby.runtime.paths import events_dir
    jsonl_dir = jsonl_dir or events_dir()
    cfg = cfg or {}
    stats = {"deleted": 0, "skipped": False}
    if not cfg.get("enabled", True):
        stats["skipped"] = True
        return stats

    retention_days = int(cfg.get("jsonl_retention_days", 30))
    cutoff_date = (_utc_now() - timedelta(days=retention_days)).strftime("%Y%m%d")
    if not os.path.isdir(jsonl_dir):
        return stats

    for name in os.listdir(jsonl_dir):
        m = _JSONL_NAME.match(name)
        if not m:
            continue
        if m.group(1) < cutoff_date:
            path = os.path.join(jsonl_dir, name)
            try:
                os.remove(path)
                stats["deleted"] += 1
            except OSError as e:
                logger.debug("Could not remove JSONL %s: %s", path, e)
    return stats


def prune_sqlite_events(db, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Delete SQLite events rows older than sqlite_retention_days."""
    cfg = cfg or {}
    stats = {"deleted": 0, "skipped": False}
    if not cfg.get("enabled", True):
        stats["skipped"] = True
        return stats

    days = int(cfg.get("sqlite_retention_days", 90))
    cutoff_ts = _iso_cutoff(days)
    try:
        stats["deleted"] = db.delete_events_before(cutoff_ts)
    except Exception as e:
        logger.error("Failed to prune SQLite events: %s", e)
    return stats


def prune_backups(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Trim backup .bak files by age and count."""
    cfg = cfg or {}
    stats = {"deleted": 0, "remaining": 0, "skipped": False}
    if not cfg.get("enabled", True):
        stats["skipped"] = True
        return stats

    backup_dir = str(cfg.get("backup_dir", "backups"))
    max_backups = max(1, int(cfg.get("max_backups", 10)))
    max_age_days = int(cfg.get("max_age_days", 30))
    age_cutoff = _utc_now().timestamp() - max_age_days * 86400

    if not os.path.isdir(backup_dir):
        return stats

    files: List[str] = [
        os.path.join(backup_dir, f)
        for f in os.listdir(backup_dir)
        if f.endswith(".bak")
    ]
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)

    to_delete: List[str] = []
    for path in files:
        try:
            if os.path.getmtime(path) < age_cutoff:
                to_delete.append(path)
        except OSError:
            continue

    remaining = [p for p in files if p not in to_delete]
    if len(remaining) > max_backups:
        to_delete.extend(remaining[max_backups:])
        remaining = remaining[:max_backups]

    # Safety: never delete the only backup left
    if len(files) == 1 and to_delete:
        stats["remaining"] = 1
        return stats

    for path in to_delete:
        if len(files) - stats["deleted"] <= 1:
            break
        try:
            os.remove(path)
            stats["deleted"] += 1
        except OSError as e:
            logger.debug("Could not remove backup %s: %s", path, e)

    stats["remaining"] = max(0, len(files) - stats["deleted"])
    return stats


def run_event_retention(db, config: Dict[str, Any]) -> Dict[str, Any]:
    """Run JSONL + SQLite event pruning from full bot config."""
    cfg = config.get("event_retention") or {}
    if not cfg.get("enabled", True):
        return {"jsonl": {"skipped": True}, "sqlite": {"skipped": True}}

    jsonl_stats = prune_jsonl_events(cfg)
    sqlite_stats = prune_sqlite_events(db, cfg)
    if jsonl_stats.get("deleted") or sqlite_stats.get("deleted"):
        logger.info(
            "Event retention: deleted %d JSONL file(s), %d SQLite row(s)",
            jsonl_stats.get("deleted", 0),
            sqlite_stats.get("deleted", 0),
        )
    return {"jsonl": jsonl_stats, "sqlite": sqlite_stats}


def run_backup_retention(config: Dict[str, Any]) -> Dict[str, Any]:
    """Run backup directory pruning from full bot config."""
    cfg = config.get("backup_retention") or {}
    stats = prune_backups(cfg)
    if stats.get("deleted"):
        logger.info(
            "Backup retention: deleted %d file(s), %d remaining",
            stats["deleted"],
            stats.get("remaining", 0),
        )
    return stats
