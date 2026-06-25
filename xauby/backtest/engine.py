"""Backtest engine — backward-compatible re-export shim.

The implementation has been split into focused modules:
  - data.py      → download, cache, timeframe resolution
  - replay.py    → plugin-aware bar-by-bar replay
  - optimizer.py → grid-search parameter optimization
  - metrics.py   → typed result metrics and extraction helpers
  - constants.py → shared defaults and paths
"""

from __future__ import annotations

# Re-export constants
from xauby.backtest.constants import BEST_PARAMS_FILE, CACHE_DIR  # noqa: F401

# Re-export data layer
from xauby.backtest.data import (  # noqa: F401
    _cache_path,
    _load_candle_pair,
    _whitelist_timeframes_for_symbol,
    default_candle_limit,
    download_klines,
    get_or_load_data,
    get_six_months_candle_count,
    load_or_download_candles,
    normalize_ohlcv_df,
    prepare_raw_dataframe,
    resolve_backtest_timeframes,
)

# Re-export optimizer
from xauby.backtest.optimizer import (  # noqa: F401
    PAIR_OPTIMIZATION_PRESETS,
    _optimization_grid,
    optimize_grid_for_symbol,
    optimize_pair_strategy_extended,
)

# Re-export replay bridge
from xauby.backtest.replay import run_plugin_replay  # noqa: F401

# Re-export best-params helper (moved to best_params module)
from xauby.backtest.best_params import save_best_parameters  # noqa: F401
