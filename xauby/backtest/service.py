"""Backtest orchestration for TUI focused-pair replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import pandas as pd

from xauby.backtest.data import (
    get_or_load_data,
    normalize_ohlcv_df,
    prepare_raw_dataframe,
    resolve_backtest_timeframes,
)
from xauby.backtest.replay import run_plugin_replay
from xauby.runtime.exchange_config import resolve_fee_pct
from xauby.backtest.runtime_config import (
    build_config_used_snapshot,
    merge_runtime_into_backtest_config,
)
from xauby.runtime.trading_config import split_legacy_runtime_config, strategy_name_for_symbol
from xauby.observability.replay_validation import load_bot_config


@dataclass
class BacktestRunMeta:
    symbol: str
    strategy_name: str
    primary_tf: str
    regime_tf: Optional[str]
    period_start: str
    period_end: str
    bars: int
    use_d1_regime_filter: bool
    fee_pct: float
    regime_bars: int = 0
    run_ok: bool = True
    error: str = ""
    config_used: Dict[str, Any] = field(default_factory=dict)
    data_symbol: str = ""
    used_data_proxy: bool = False


@dataclass
class BacktestReplayBundle:
    """Pre-loaded candle data and merged config for repeated replays."""

    symbol: str
    strategy_name: str
    merged_cfg: Dict[str, Any]
    strat_cfg: Dict[str, Any]
    primary_tf: str
    regime_tf: Optional[str]
    use_d1: bool
    fee_pct: float
    config_used: Dict[str, Any]
    df: pd.DataFrame
    df_regime: Optional[pd.DataFrame]
    period_start: str
    period_end: str
    bars: int
    regime_bars: int
    data_symbol: str = ""
    used_data_proxy: bool = False


@dataclass
class BacktestRunResult:
    stats: Dict[str, Any] = field(default_factory=dict)
    meta: BacktestRunMeta = field(
        default_factory=lambda: BacktestRunMeta(
            symbol="",
            strategy_name="",
            primary_tf="4h",
            regime_tf=None,
            period_start="",
            period_end="",
            bars=0,
            use_d1_regime_filter=False,
            fee_pct=0.0,
        )
    )


def _period_label(df: pd.DataFrame) -> tuple[str, str]:
    if df is None or df.empty or "open_time" not in df.columns:
        return ("", "")
    try:
        start = pd.to_datetime(int(df["open_time"].iloc[0]), unit="ms").strftime("%Y-%m")
        end = pd.to_datetime(int(df["open_time"].iloc[-1]), unit="ms").strftime("%Y-%m")
        return start, end
    except Exception:
        return ("", "")


def resolve_strategy_name(
    symbol: str,
    engine_config: Optional[Dict[str, Any]] = None,
    bot_state: Optional[Dict[str, Any]] = None,
) -> str:
    cfg = engine_config or load_bot_config()
    configured = strategy_name_for_symbol(cfg, symbol)
    if configured:
        return configured
    if bot_state:
        name = str(bot_state.get("strategy_name") or "").strip()
        if name:
            return name
    from xauby.strategies.registry import normalize_strategy_name

    return normalize_strategy_name((cfg.get("strategy") or {}).get("active") or "xauby_actionzone")


def _prepare_backtest_config(
    sym: str,
    strat_name: str,
    cfg: Dict[str, Any],
    bot_state: Optional[Dict[str, Any]],
) -> tuple[Dict[str, Any], Dict[str, Any], str, Optional[str], bool, Dict[str, Any]]:
    """Return merged engine cfg, strat cfg, timeframes, and d1 flag."""
    strat_cfg, trading_cfg = split_legacy_runtime_config(
        cfg,
        strat_name,
        symbol=sym,
        for_live=False,
    )

    strat_cfg, trading_cfg, primary_override, regime_override = merge_runtime_into_backtest_config(
        bot_state, strat_cfg, trading_cfg
    )

    primary_tf, regime_tf = resolve_backtest_timeframes(
        sym,
        strat_name,
        cfg,
        primary_override=primary_override,
        regime_override=regime_override,
    )
    use_d1 = bool(strat_cfg.get("use_d1_regime_filter", False))
    if not use_d1:
        regime_tf = None

    merged_cfg = dict(cfg)
    merged_cfg["trading"] = trading_cfg
    config_used = build_config_used_snapshot(
        strat_cfg,
        trading_cfg,
        primary_tf=primary_tf,
        regime_tf=regime_tf,
    )
    return merged_cfg, strat_cfg, primary_tf, regime_tf, use_d1, config_used


def _resolve_fee_pct(cfg: Dict[str, Any], trading_cfg: Dict[str, Any]) -> float:
    return resolve_fee_pct(cfg)


def _meta_from_bundle(
    bundle: BacktestReplayBundle,
    *,
    run_ok: bool = True,
    error: str = "",
    config_used: Optional[Dict[str, Any]] = None,
) -> BacktestRunMeta:
    """Build BacktestRunMeta from a bundle, avoiding repetitive field lists."""
    return BacktestRunMeta(
        symbol=bundle.symbol,
        strategy_name=bundle.strategy_name,
        primary_tf=bundle.primary_tf,
        regime_tf=bundle.regime_tf,
        period_start=bundle.period_start,
        period_end=bundle.period_end,
        bars=bundle.bars,
        use_d1_regime_filter=bundle.use_d1,
        fee_pct=bundle.fee_pct,
        regime_bars=bundle.regime_bars,
        run_ok=run_ok,
        error=error,
        config_used=dict(config_used) if config_used is not None else dict(bundle.config_used),
        data_symbol=bundle.data_symbol or bundle.symbol,
        used_data_proxy=bundle.used_data_proxy,
    )


def load_backtest_replay_bundle(
    symbol: str,
    *,
    strategy_name: Optional[str] = None,
    engine_config: Optional[Dict[str, Any]] = None,
    bot_state: Optional[Dict[str, Any]] = None,
    force_download: bool = False,
) -> BacktestReplayBundle:
    """Load candles once for reuse across grid-search or batch replays."""
    from xauby.runtime.symbol_resolver import focus_symbol_from_config

    default_sym = focus_symbol_from_config()
    sym = str(symbol or default_sym).upper().replace("_", "")
    cfg = engine_config or load_bot_config()
    strat_name = strategy_name or resolve_strategy_name(sym, cfg, bot_state)

    merged_cfg, strat_cfg, primary_tf, regime_tf, use_d1, config_used = _prepare_backtest_config(
        sym, strat_name, cfg, bot_state
    )
    fee_pct = _resolve_fee_pct(cfg, merged_cfg.get("trading") or {})

    df_primary, df_regime_raw, data_symbol, used_proxy = get_or_load_data(
        sym,
        primary_timeframe=primary_tf,
        regime_timeframe=regime_tf,
        force_download=force_download,
        base_url=str((cfg.get("backtest") or {}).get("data_base_url") or ""),
    )
    if df_primary.empty:
        raise ValueError(f"No candle data for {sym}")

    period_start, period_end = _period_label(df_primary)
    bars = len(df_primary)
    regime_bars = 0
    if df_regime_raw is not None and not df_regime_raw.empty:
        regime_bars = len(df_regime_raw)

    if use_d1 and regime_tf and regime_bars == 0:
        raise ValueError(f"D1 regime filter enabled but no {regime_tf} candles for {sym}")

    df = prepare_raw_dataframe(df_primary)
    df_regime = normalize_ohlcv_df(df_regime_raw) if df_regime_raw is not None else None

    return BacktestReplayBundle(
        symbol=sym,
        strategy_name=strat_name,
        merged_cfg=merged_cfg,
        strat_cfg=dict(strat_cfg),
        primary_tf=primary_tf,
        regime_tf=regime_tf,
        use_d1=use_d1,
        fee_pct=fee_pct,
        config_used=dict(config_used),
        df=df,
        df_regime=df_regime,
        period_start=period_start,
        period_end=period_end,
        bars=bars,
        regime_bars=regime_bars,
        data_symbol=data_symbol,
        used_data_proxy=used_proxy,
    )


def run_replay_from_bundle(
    bundle: BacktestReplayBundle,
    *,
    strat_cfg_override: Optional[Dict[str, Any]] = None,
    df_override: Optional[pd.DataFrame] = None,
    min_bars_override: Optional[int] = None,
) -> BacktestRunResult:
    """Run plugin replay using a pre-loaded bundle (no candle reload).

    ``df_override`` replays a slice of the bundle's candles (e.g. an in-sample
    or out-of-sample window) without reloading data; defaults to the full
    bundle frame.

    ``min_bars_override`` is the count of leading bars in that slice to warm
    indicators on WITHOUT trading. A slice prepended with a warmup lead-in needs
    it, or the replay skips only the strategy's own ``min_bars`` and trades the
    rest of the lead-in — bars that belong to the previous window. See
    xauby/backtest/walkforward.py for the same mistake made three times.
    """
    strat_cfg = dict(bundle.strat_cfg)
    if strat_cfg_override:
        strat_cfg = {**strat_cfg, **strat_cfg_override}

    df = df_override if df_override is not None else bundle.df

    config_used = dict(bundle.config_used)
    if strat_cfg_override:
        config_used = build_config_used_snapshot(
            strat_cfg,
            bundle.merged_cfg.get("trading") or {},
            primary_tf=bundle.primary_tf,
            regime_tf=bundle.regime_tf,
        )

    try:
        stats = run_plugin_replay(
            df,
            strategy_config=strat_cfg,
            engine_config=bundle.merged_cfg,
            symbol=bundle.symbol,
            strategy_name=bundle.strategy_name,
            df_regime=bundle.df_regime,
            primary_timeframe=bundle.primary_tf,
            regime_timeframe=bundle.regime_tf,
            min_bars_override=min_bars_override,
        )
        config_used["net_profit_pct"] = float(stats.get("net_profit_pct", 0.0) or 0.0)
        config_used["profit_factor"] = float(stats.get("profit_factor", 0.0) or 0.0)
        if stats.get("checklist_config"):
            config_used["checklist_gates"] = dict(stats["checklist_config"])
        if stats.get("last_checklist") is not None:
            config_used["last_checklist"] = list(stats["last_checklist"])

        meta = _meta_from_bundle(bundle, run_ok=True, config_used=config_used)
        return BacktestRunResult(stats=stats, meta=meta)
    except Exception as exc:
        meta = _meta_from_bundle(bundle, run_ok=False, error=str(exc), config_used=config_used)
        return BacktestRunResult(stats={}, meta=meta)


def run_focused_backtest(
    symbol: str,
    *,
    strategy_name: Optional[str] = None,
    engine_config: Optional[Dict[str, Any]] = None,
    bot_state: Optional[Dict[str, Any]] = None,
    force_download: bool = False,
    strat_cfg_override: Optional[Dict[str, Any]] = None,
) -> BacktestRunResult:
    """Run plugin replay for a single focused symbol using runtime-merged config."""
    from xauby.runtime.symbol_resolver import focus_symbol_from_config

    default_sym = focus_symbol_from_config()
    sym = str(symbol or default_sym).upper().replace("_", "")
    cfg = engine_config or load_bot_config()
    strat_name = strategy_name or resolve_strategy_name(sym, cfg, bot_state)

    merged_cfg, strat_cfg, primary_tf, regime_tf, use_d1, config_used = _prepare_backtest_config(
        sym, strat_name, cfg, bot_state
    )
    if strat_cfg_override:
        strat_cfg = {**strat_cfg, **strat_cfg_override}
        config_used = build_config_used_snapshot(
            strat_cfg,
            merged_cfg.get("trading") or {},
            primary_tf=primary_tf,
            regime_tf=regime_tf,
        )

    fee_pct = _resolve_fee_pct(cfg, merged_cfg.get("trading") or {})

    period_start, period_end = "", ""
    bars = 0
    regime_bars = 0

    try:
        df_primary, df_regime_raw, data_symbol, used_proxy = get_or_load_data(
            sym,
            primary_timeframe=primary_tf,
            regime_timeframe=regime_tf,
            force_download=force_download,
            base_url=str((cfg.get("backtest") or {}).get("data_base_url") or ""),
        )
        if df_primary.empty:
            raise ValueError(f"No candle data for {sym}")

        period_start, period_end = _period_label(df_primary)
        bars = len(df_primary)
        if df_regime_raw is not None and not df_regime_raw.empty:
            regime_bars = len(df_regime_raw)

        if use_d1 and regime_tf and regime_bars == 0:
            raise ValueError(f"D1 regime filter enabled but no {regime_tf} candles for {sym}")

        bundle = BacktestReplayBundle(
            symbol=sym,
            strategy_name=strat_name,
            merged_cfg=merged_cfg,
            strat_cfg=strat_cfg,
            primary_tf=primary_tf,
            regime_tf=regime_tf,
            use_d1=use_d1,
            fee_pct=fee_pct,
            config_used=config_used,
            df=prepare_raw_dataframe(df_primary),
            df_regime=normalize_ohlcv_df(df_regime_raw) if df_regime_raw is not None else None,
            period_start=period_start,
            period_end=period_end,
            bars=bars,
            regime_bars=regime_bars,
            data_symbol=data_symbol,
            used_data_proxy=used_proxy,
        )
        # Release raw downloads immediately — bundle holds the processed copies.
        del df_primary, df_regime_raw
        result = run_replay_from_bundle(bundle)
        # Bundle DataFrames are no longer needed; free before returning so
        # the asyncio.to_thread caller gets memory back promptly on low-RAM VPS.
        del bundle
        import gc; gc.collect()
        return result
    except Exception as exc:
        meta = BacktestRunMeta(
            symbol=sym,
            strategy_name=strat_name,
            primary_tf=primary_tf,
            regime_tf=regime_tf,
            period_start=period_start,
            period_end=period_end,
            bars=bars,
            use_d1_regime_filter=use_d1,
            fee_pct=fee_pct,
            regime_bars=regime_bars,
            run_ok=False,
            error=str(exc),
            config_used=config_used,
        )
        return BacktestRunResult(stats={}, meta=meta)
