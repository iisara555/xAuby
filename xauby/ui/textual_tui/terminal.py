"""Terminal restore helpers after Textual exits to a classic ANSI submenu."""

from __future__ import annotations

import os
import sys


def restore_terminal() -> None:
    """Return stdin/stdout to a sane state for input() and a follow-up Textual run."""
    if sys.platform == "win32":
        return
    try:
        from xauby.ui.dashboard import restore_terminal as _dash_restore

        _dash_restore()
    except Exception:
        pass
    try:
        os.system("stty sane 2>/dev/null")
    except Exception:
        pass
