"""Single source of truth for the runtime data root.

All mutable runtime artifacts (SQLite DB, engine lock, sim balances, logs, state
JSON) live under one root — historically the literal ``core/`` directory. This
module makes that root relocatable and per-instance so multiple engines can run
on one host without colliding:

* ``XAUBY_HOME``        — base directory for runtime data (default ``core``).
* ``XAUBY_INSTANCE_ID`` — optional sub-namespace, e.g. a tenant/account id.

With the defaults unset, every path resolves exactly as before (``core/...``),
so this is behaviour-preserving for single-instance deployments.
"""

from __future__ import annotations

import os
from typing import Optional

DEFAULT_RUNTIME_ROOT = "core"


def runtime_root() -> str:
    """Return the runtime data root, honoring XAUBY_HOME / XAUBY_INSTANCE_ID."""
    base = os.environ.get("XAUBY_HOME") or DEFAULT_RUNTIME_ROOT
    instance = os.environ.get("XAUBY_INSTANCE_ID")
    return os.path.join(base, instance) if instance else base


def config_root() -> str:
    """Return the directory holding this instance's CONFIG files.

    Covers ``bot_config.yaml``, ``coin_whitelist.json`` and ``.env``.
    ``XAUBY_CONFIG_DIR`` overrides; with it unset this is the current working
    directory, exactly as before (behaviour-preserving for single-instance).

    This is the config counterpart to :func:`runtime_root` (mutable data). A
    full multi-instance deployment sets both ``XAUBY_CONFIG_DIR`` (e.g.
    ``instances/acct1``) and ``XAUBY_INSTANCE_ID`` (e.g. ``acct1``).
    """
    return os.environ.get("XAUBY_CONFIG_DIR") or os.getcwd()


def config_file(name: str) -> str:
    """Resolve a config file ``name`` under :func:`config_root`."""
    return os.path.join(config_root(), name)


def env_file() -> str:
    """Path to this instance's ``.env`` (under :func:`config_root`)."""
    return config_file(".env")


def runtime_path(*parts: str) -> str:
    """Join ``parts`` onto the runtime root (e.g. ``runtime_path('logs')``)."""
    return os.path.join(runtime_root(), *parts)


def ensure_runtime_dir(*parts: str) -> str:
    """Like :func:`runtime_path` but also creates the directory."""
    path = runtime_path(*parts)
    os.makedirs(path, exist_ok=True)
    return path


# --- named artifacts (single source of truth for each runtime file) ---------

def db_path() -> str:
    """Default SQLite path under the runtime root.

    Note: ``database.db.resolve_db_path()`` is the canonical resolver and also
    honors ``SQLITE_DB_PATH``; use this only where that env override is not
    relevant (e.g. UI candidate probing).
    """
    return runtime_path("xauby.db")


def logs_dir() -> str:
    return runtime_path("logs")


def log_path(name: str) -> str:
    return runtime_path("logs", name)


def events_dir() -> str:
    return runtime_path("logs", "events")


def bot_state_path() -> str:
    return runtime_path("logs", "xauby_bot_state.json")


def dashboard_focus_path() -> str:
    return runtime_path("dashboard_focus.json")


def manual_order_request_path() -> str:
    """Single-slot local IPC request used by the dashboard manual controls."""
    return runtime_path("manual_order_request.json")


def sentiment_guard_state_path() -> str:
    return runtime_path("sentiment_guard_state.json")


def usd_thb_rate_path() -> str:
    return runtime_path("usd_thb_rate.json")


def equity_peak_path() -> str:
    return runtime_path("equity_peak.json")


def sim_balance_path(config_name: Optional[str] = None) -> str:
    name = (
        f"simulated_balance_{config_name}.json" if config_name else "simulated_balance.json"
    )
    return runtime_path(name)


def account_lock_dir() -> str:
    """Shared (cross-instance) directory for per-account live trading locks.

    Unlike :func:`runtime_root`, this is deliberately NOT scoped per instance:
    multiple engines on one host must see the *same* lock so a second live
    instance on the same exchange account is detected. ``XAUBY_ACCOUNT_LOCK_DIR``
    overrides (used by tests); default is ``~/.xauby/account_locks``.
    """
    override = os.environ.get("XAUBY_ACCOUNT_LOCK_DIR")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".xauby", "account_locks")


def account_lock_path(fingerprint: str) -> str:
    """Path to the live lock for an exchange account ``fingerprint``."""
    return os.path.join(account_lock_dir(), f"account_{fingerprint}.lock")
