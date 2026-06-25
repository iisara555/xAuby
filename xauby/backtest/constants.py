"""Backtest system constants and defaults."""

from __future__ import annotations

import os

from xauby.runtime.paths import runtime_root

CACHE_DIR = runtime_root()
BEST_PARAMS_FILE = os.path.join(CACHE_DIR, "backtest_best_params.json")

# Default backtest simulation parameters
DEFAULT_INITIAL_BALANCE = 1000.0
DEFAULT_FEE_PCT = 0.001
DEFAULT_SL_ATR_MULT = 2.5
DEFAULT_TRAILING_ATR_MULT = 1.5
DEFAULT_RISK_PCT = 0.01
DEFAULT_BE_ACTIVATION_ATR_MULT = 1.5
DEFAULT_BE_BUFFER_ATR_MULT = 0.1

# Timeframe defaults
DEFAULT_PRIMARY_TF = "4h"
DEFAULT_REGIME_TF = "1d"

# Candle freshness threshold (seconds)
CACHE_FRESHNESS_SECONDS = 2 * 86400

# Grid optimization defaults
DEFAULT_COMPACT_GRID_RUNS = 16

# Phone layout trade display limit
PHONE_MAX_TRADES = 12
