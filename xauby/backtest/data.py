"""Backtest data layer: candle download, cache, and proxy resolution."""

from __future__ import annotations

import logging
import os
import time
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

import pandas as pd
import requests

from xauby.backtest.constants import CACHE_DIR, CACHE_FRESHNESS_SECONDS
from xauby.backtest.symbols import resolve_data_proxy
from xauby.runtime.candle_utils import drop_forming_bar, timeframe_seconds
from xauby.runtime.pair_registry import _normalize_tf


def _source_tag_from_url(base_url: str) -> str:
    """Short identifier for the data source used in cache filenames."""
    return "global" if "binance.com" in str(base_url) else "th"


def _finalize_candles(df: pd.DataFrame, timeframe: str, symbol: str) -> pd.DataFrame:
    """Make a freshly loaded OHLCV frame safe for backtesting.

    Two integrity steps that every consumer (replay, optimizer, R&D scripts)
    needs but should never have to repeat:
      * drop the still-forming last candle — its OHLC keeps changing, so
        replaying it (or closing a position on its close) leaks future data;
      * warn (do NOT silently patch) when the bar spacing is irregular, which
        means missing candles that would distort indicator math.
    """
    if df is None or df.empty or "timestamp" not in df.columns:
        return df
    trimmed, dropped = drop_forming_bar(df, timeframe)
    if dropped:
        df = trimmed.reset_index(drop=True)
    _warn_on_gaps(df, timeframe, symbol)
    return df


def _warn_on_gaps(df: pd.DataFrame, timeframe: str, symbol: str) -> None:
    """Log a warning when consecutive candles are not one timeframe apart."""
    step = timeframe_seconds(timeframe)
    if step <= 0 or len(df) < 3:
        return
    diffs = df["timestamp"].astype("int64").diff().iloc[1:]
    gaps = int((diffs != step).sum())
    if gaps:
        logger.warning(
            "BACKTEST DATA GAPS: %s %s has %d irregular bar interval(s) "
            "(expected %ds spacing). Indicators across the gap may be skewed.",
            symbol, timeframe, gaps, step,
        )


def download_klines(
    symbol: str,
    timeframe: str,
    limit: int = 3000,
    *,
    base_url: str = "",
) -> pd.DataFrame:
    """Download historical candles from a Binance public klines endpoint.

    ``base_url`` selects the exchange:
    - Global Binance (``https://api.binance.com``) → ``/api/v3/klines``  — data since 2017
    - Binance Thailand (``https://api.binance.th``) → ``/api/v1/klines`` — live trading exchange

    When ``base_url`` is empty the ``BINANCE_BASE_URL`` environment variable is
    used (defaults to ``https://api.binance.th``).
    """
    if not base_url:
        base_url = os.environ.get("BINANCE_BASE_URL", "https://api.binance.th").rstrip("/")
    else:
        base_url = base_url.rstrip("/")

    # Global Binance uses /api/v3; Thailand and other regional exchanges use /api/v1.
    if "binance.com" in base_url:
        url = f"{base_url}/api/v3/klines"
    else:
        url = f"{base_url}/api/v1/klines"

    sym = symbol.upper().replace("_", "")
    all_klines: List[List[Any]] = []

    interval_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
    api_interval = interval_map.get(timeframe.lower(), timeframe)

    end_time = int(time.time() * 1000)
    chunk_size = 1000

    while len(all_klines) < limit:
        params = {
            "symbol": sym,
            "interval": api_interval,
            "limit": chunk_size,
            "endTime": end_time,
        }
        try:
            r = requests.get(url, params=params, timeout=10)
            if r.status_code != 200:
                break
            data = r.json()
            if not data or not isinstance(data, list):
                break

            existing_ts = {k[0] for k in all_klines}
            new_data = [k for k in data if k[0] not in existing_ts]
            if not new_data:
                break

            all_klines.extend(new_data)
            all_klines.sort(key=lambda x: x[0], reverse=True)

            oldest_ts = all_klines[-1][0]
            end_time = oldest_ts - 1

            if len(data) < chunk_size:
                break
        except Exception:
            break

    all_klines.sort(key=lambda x: x[0])
    if len(all_klines) > limit:
        all_klines = all_klines[-limit:]

    if not all_klines:
        return pd.DataFrame()

    df = pd.DataFrame(
        all_klines,
        columns=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "count",
            "taker_buy_base",
            "taker_buy_quote",
            "ignore",
        ],
    )

    cols = ["open", "high", "low", "close", "volume"]
    for c in cols:
        df[c] = df[c].astype(float)

    df["timestamp"] = df["open_time"] // 1000
    return df


def _cache_path(symbol: str, timeframe: str, source_tag: str = "th") -> str:
    sym = symbol.lower().replace("_", "")
    tf = str(timeframe or "4h").lower().replace("_", "")
    return os.path.join(CACHE_DIR, f"backtest_candles_{source_tag}_{tf}_{sym}.csv")


def default_candle_limit(timeframe: str) -> int:
    """Bar download limit tuned per timeframe for useful backtest history."""
    tf = str(timeframe or "4h").lower()
    if tf in ("1m", "3m", "5m", "15m", "30m"):
        return 3000
    if tf in ("1h", "2h"):
        return 9000  # ~12 months of 1h bars; enough for 120-bar Donchian + EMA200
    if tf in ("4h", "6h", "8h", "12h"):
        return 3000
    return 1000


def get_six_months_candle_count(timeframe: str) -> int:
    """Calculate candle count for a 12-month backtest window (+ warm-up buffer).

    Extended from 6 months to 12 months so trend-following strategies with
    long entry windows (e.g. 120-bar Donchian on 1h) produce enough trades
    for statistically meaningful results.
    """
    tf = str(timeframe or "4h").lower()
    unit = tf[-1]
    try:
        val = int(tf[:-1])
    except ValueError:
        val = 1

    if unit == "m":
        seconds = val * 60
    elif unit == "h":
        seconds = val * 3600
    elif unit == "d":
        seconds = val * 86400
    elif unit == "w":
        seconds = val * 86400 * 7
    else:
        seconds = 3600 * 4

    twelve_months_seconds = 12 * 30 * 24 * 3600
    calculated = (twelve_months_seconds // seconds) + 100
    return min(calculated, 10000)  # Cap at 10000 to keep VPS RAM and HTTP requests manageable


def load_or_download_candles(
    symbol: str,
    timeframe: str,
    *,
    limit: Optional[int] = None,
    force_download: bool = False,
    base_url: str = "",
) -> pd.DataFrame:
    """Load cached OHLCV for ``symbol``/``timeframe`` or download from Binance.

    ``base_url`` is forwarded to :func:`download_klines`.  Cache files are
    namespaced by data source (``global`` vs ``th``) so switching sources
    never serves stale data from the other exchange.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    tf = str(timeframe or "4h").lower()
    source_tag = _source_tag_from_url(base_url) if base_url else "th"
    cache_file = _cache_path(symbol, tf, source_tag)
    bar_limit = int(limit if limit is not None else default_candle_limit(tf))

    cache_ok = False
    df_cached = pd.DataFrame()
    if os.path.exists(cache_file):
        try:
            df_cached = pd.read_csv(cache_file)
            if len(df_cached) >= bar_limit:
                last_ts = int(df_cached["open_time"].iloc[-1]) / 1000
                if (time.time() - last_ts) < CACHE_FRESHNESS_SECONDS:
                    cache_ok = True
        except Exception:
            pass

    if not force_download and cache_ok:
        return _finalize_candles(df_cached, tf, symbol)

    df = download_klines(symbol, tf, limit=bar_limit, base_url=base_url)
    if not df.empty:
        df.to_csv(cache_file, index=False)
    return _finalize_candles(df, tf, symbol)


def _whitelist_timeframes_for_symbol(symbol: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (primary, confirm) from whitelist or backtest-only registry."""
    try:
        from xauby.backtest.symbols import backtest_timeframes_for_symbol
        from xauby.runtime.pair_config import load_whitelist

        wl = load_whitelist(".")
        quote = str(wl.get("quote_asset", "USDT") or "USDT")
        sym = symbol.upper().replace("_", "")
        base = sym[: -len(quote)] if sym.endswith(quote) else sym
        for asset in wl.get("assets") or []:
            if str(asset.get("symbol", "")).upper() == base:
                primary = asset.get("primary_timeframe")
                confirm = asset.get("confirm_timeframe")
                return (
                    str(primary) if primary else None,
                    str(confirm) if confirm else None,
                )
        return backtest_timeframes_for_symbol(symbol, quote)
    except Exception:
        pass
    return None, None


def resolve_backtest_timeframes(
    symbol: str,
    strategy_name: str,
    engine_config: Dict[str, Any],
    *,
    primary_override: Optional[str] = None,
    regime_override: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """Resolve primary and confirm timeframes for backtest (mirrors live pair config)."""
    cfg = engine_config or {}
    strat_cfg = (cfg.get("strategies") or {}).get(strategy_name) or {}
    mode_block = cfg.get("strategy_mode") or {}
    active_mode = mode_block.get("active", "trend_only")
    mode_cfg = mode_block.get(active_mode, {}) if isinstance(mode_block.get(active_mode), dict) else {}

    wl_primary, wl_confirm = _whitelist_timeframes_for_symbol(symbol)

    primary = (
        primary_override
        or wl_primary
        or strat_cfg.get("timeframe")
        or mode_cfg.get("primary_timeframe")
        or "4h"
    )
    regime = (
        regime_override
        if regime_override is not None
        else wl_confirm
        or strat_cfg.get("confirm_timeframe")
        or mode_cfg.get("confirm_timeframe")
        or "1d"
    )

    primary_tf = _normalize_tf(str(primary))
    regime_tf = _normalize_tf(str(regime)) if regime else None
    return primary_tf, regime_tf


def _load_candle_pair(
    symbol: str,
    primary_timeframe: str,
    regime_timeframe: Optional[str],
    *,
    force_download: bool = False,
    base_url: str = "",
) -> Tuple[pd.DataFrame, Optional[pd.DataFrame]]:
    primary_tf = str(primary_timeframe or "4h").lower()
    limit_primary = get_six_months_candle_count(primary_tf)
    df_primary = load_or_download_candles(
        symbol,
        primary_tf,
        limit=limit_primary,
        force_download=force_download,
        base_url=base_url,
    )

    regime_tf = str(regime_timeframe).lower() if regime_timeframe else ""
    df_regime: Optional[pd.DataFrame] = None
    if regime_tf:
        limit_regime = get_six_months_candle_count(regime_tf)
        df_regime = load_or_download_candles(
            symbol,
            regime_tf,
            limit=limit_regime,
            force_download=force_download,
            base_url=base_url,
        )
    return df_primary, df_regime


def get_or_load_data(
    symbol: str,
    primary_timeframe: str = "4h",
    regime_timeframe: Optional[str] = "1d",
    force_download: bool = False,
    base_url: str = "",
) -> Tuple[pd.DataFrame, Optional[pd.DataFrame], str, bool]:
    """Load candles for backtest; may proxy to PAXGUSDT when XAUTUSDT history is too short.

    ``base_url`` selects the data source.  When empty the ``BINANCE_BACKTEST_URL``
    environment variable is checked, then the default ``https://api.binance.com``
    (Global Binance) is used so backtests draw on the deepest available history.

    Returns (df_primary, df_regime, data_symbol, used_proxy).
    """
    from xauby.runtime.strategy_pair_config import backtest_data_proxy_for_symbol

    if not base_url:
        base_url = os.environ.get(
            "BINANCE_BACKTEST_URL",
            "https://api.binance.com",  # Global: BTCUSDT since 2017, deep history
        )

    requested = str(symbol or "XAUTUSDT").upper().replace("_", "")
    force_proxy = backtest_data_proxy_for_symbol(requested) or None
    df_primary, df_regime = _load_candle_pair(
        requested,
        primary_timeframe,
        regime_timeframe,
        force_download=force_download,
        base_url=base_url,
    )

    data_symbol = requested
    used_proxy = False
    proxy = resolve_data_proxy(
        requested,
        len(df_primary),
        primary_timeframe=primary_timeframe,
        force_proxy=force_proxy,
    )
    if proxy and proxy != requested:
        data_symbol = proxy
        used_proxy = True
        logger.warning(
            "BACKTEST PROXY: %s has insufficient history (%d bars) — "
            "substituting %s data. Results reflect %s price action, NOT %s. "
            "Treat this backtest as indicative only.",
            requested, len(df_primary), proxy, proxy, requested,
        )
        df_primary, df_regime = _load_candle_pair(
            proxy,
            primary_timeframe,
            regime_timeframe,
            force_download=force_download,
            base_url=base_url,
        )

    return df_primary, df_regime, data_symbol, used_proxy


def normalize_ohlcv_df(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure OHLCV dataframe has a ``timestamp`` column (seconds) for replay."""
    out = df.copy()
    if "timestamp" not in out.columns and "open_time" in out.columns:
        out["timestamp"] = out["open_time"] // 1000
    return out


def prepare_raw_dataframe(df_4h: pd.DataFrame, df_1d: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Validate and return raw OHLCV dataframe for generic strategy backtesting.

    Strategies are expected to calculate their own indicators inside ``analyze()``
    from the raw ``MarketContext.df_primary``.
    """
    return normalize_ohlcv_df(df_4h)
