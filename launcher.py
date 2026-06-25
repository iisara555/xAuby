"""Backwards-compatible shim.

The launcher implementation now lives in the ``xauby.launcher`` package. This
module stays at the repo root because the Textual TUI execs ``launcher.py`` on
restart and external callers/tests do ``import launcher``. It re-exports the
package's public surface.
"""
import os

# Preserve the historical side effect: run from the project root.
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir:
    os.chdir(_script_dir)

from xauby.launcher import *  # noqa: F401,F403
from xauby.launcher import (  # noqa: F401
    config_io,
    main,
    maintenance,
    process,
    quick_config,
)

if __name__ == "__main__":
    main()
