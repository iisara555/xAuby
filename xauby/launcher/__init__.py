"""Interactive launcher package.

Split out of the former monolithic ``launcher.py`` (which now re-exports this
package). Importing this package loads ``.env`` once, like the old module did.
"""
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from xauby.launcher import config_io, process, maintenance, quick_config  # noqa: F401
from xauby.launcher.config_io import *  # noqa: F401,F403
from xauby.launcher.process import *  # noqa: F401,F403
from xauby.launcher.maintenance import *  # noqa: F401,F403
from xauby.launcher.quick_config import *  # noqa: F401,F403

__all__ = (
    list(config_io.__all__)
    + list(process.__all__)
    + list(maintenance.__all__)
    + list(quick_config.__all__)
    + ["main", "config_io", "process", "maintenance", "quick_config"]
)


def main():
    """Entry point: Textual launcher menu (classic ANSI menu removed)."""
    if sys.platform != "win32":
        import signal

        def handle_sigwinch(signum, frame):
            raise InterruptedError("Terminal resized")

        signal.signal(signal.SIGWINCH, handle_sigwinch)

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    os.environ["FROM_LAUNCHER"] = "true"
    os.environ.setdefault("XAUBY_START_SCREEN", "menu")
    os.environ.pop("NO_COLOR", None)
    os.environ.setdefault("COLORTERM", "truecolor")

    run_textual_tui(start_screen=os.environ.get("XAUBY_START_SCREEN", "menu"))
