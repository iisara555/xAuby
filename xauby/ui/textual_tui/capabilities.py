"""Runtime capability gates for the Textual operator console.

The hosted tenant attach flow is intentionally observation-only.  Keep the
gate in a tiny dependency-free module so every screen/action can fail closed
without importing the SaaS control plane or launcher.
"""

from __future__ import annotations

import os


READ_ONLY_ENV = "XAUBY_TUI_READ_ONLY"
TENANT_ENV = "XAUBY_TUI_TENANT"


def is_read_only_mode() -> bool:
    return os.environ.get(READ_ONLY_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def attached_tenant() -> str:
    return os.environ.get(TENANT_ENV, "").strip().lower()
