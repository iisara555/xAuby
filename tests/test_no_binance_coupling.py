"""Regression guard: the engine, observability, and ops scripts must talk to the
exchange only through the IExchangeGateway factory, never the Binance client.

This locks in the multi-exchange decoupling (item 1) so a future edit cannot
silently re-couple a layer to LiteBinanceClient.
"""

import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Directories that must stay exchange-agnostic.
GUARDED_DIRS = [
    os.path.join("xauby", "engine"),
    os.path.join("xauby", "observability"),
    # backtest was missing from this list, which is how the data layer stayed
    # Binance-only through the OKX swap migration: bot_config.yaml pointed
    # backtest.data_base_url at OKX while download_klines could only build
    # Binance URLs, and the failure was swallowed into an empty frame.
    os.path.join("xauby", "backtest"),
    os.path.join("scripts"),
]

# Allowed to reference the Binance client directly.
ALLOWED = {
    os.path.join("xauby", "api", "client.py"),
    os.path.join("xauby", "api", "__init__.py"),
    os.path.join("xauby", "__init__.py"),  # back-compat re-export
}

_PATTERN = re.compile(r"\bLiteBinanceClient\b")


def _iter_py_files(rel_dir):
    base = os.path.join(REPO_ROOT, rel_dir)
    for root, _dirs, files in os.walk(base):
        if "__pycache__" in root:
            continue
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(root, f)


class TestNoBinanceClientCoupling(unittest.TestCase):
    def test_guarded_dirs_do_not_reference_binance_client(self):
        offenders = []
        for rel_dir in GUARDED_DIRS:
            for path in _iter_py_files(rel_dir):
                rel = os.path.relpath(path, REPO_ROOT)
                if rel in ALLOWED:
                    continue
                with open(path, "r", encoding="utf-8") as fh:
                    if _PATTERN.search(fh.read()):
                        offenders.append(rel)
        self.assertEqual(
            offenders,
            [],
            f"These modules reference LiteBinanceClient directly; route through "
            f"create_exchange_client / IExchangeGateway instead: {offenders}",
        )


if __name__ == "__main__":
    unittest.main()
