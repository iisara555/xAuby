"""
Atomic file I/O utilities.

Provides `atomic_json_write()` which writes JSON data to a file atomically
using a temp-file + os.replace() strategy to prevent torn reads when a
separate process (e.g. the TUI dashboard) reads the same file concurrently.
"""

import json
import os
import tempfile
import logging
from typing import Optional

logger = logging.getLogger("lite_io")


def atomic_json_write(filepath: str, data, indent: Optional[int] = 2) -> None:
    """Write JSON data to *filepath* atomically.

    Strategy:
      1. Write to a temporary file in the **same directory** as *filepath*
         (same filesystem is required for ``os.replace`` to be atomic).
      2. ``os.replace(tmp, filepath)`` — atomic on POSIX; on Windows it is
         atomic only if both paths are on the same volume (which they are
         because we use ``dir=dir_name``).

    If an exception occurs during writing, the temp file is cleaned up
    and the original *filepath* is left untouched.
    """
    dir_name = os.path.dirname(filepath) or "."
    os.makedirs(dir_name, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent)
        os.replace(tmp_path, filepath)  # Atomic on POSIX
    except Exception:
        # Clean up the temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
