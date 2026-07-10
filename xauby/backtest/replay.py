"""Backtest replay bridge using real strategy plugins."""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from xauby.backtest.constants import (
    DEFAULT_INITIAL_BALANCE,
    DEFAULT_RISK_PCT,
    DEFAULT_SL_ATR_MULT,
    DEFAULT_TRAILING_ATR_MULT,
)
from xauby.backtest.data import normalize_ohlcv_df, resolve_backtest_timeframes
from xauby.backtest.metrics import build_metrics_from_replay
from xauby.backtest.runtime_config import extract_checklist_config
from xauby.runtime.exchange_config import resolve_fee_pct
from xauby.runtime.exits import (
    resolve_fixed_tp_pct,
    resolve_minimal_roi,
    resolve_partial_tp,
)


def run_plugin_replay(
    df: pd.DataFrame,
    strategy_config: Dict[str, Any],
    engine_config: Optional[Dict[str, Any]] = None,
    symbol: str = "XAUTUSDT",
    strategy_name: str = "xauby_actionzone",
    initial_balance: float = DEFAULT_INITIAL_BALANCE,
    df_regime: Optional[pd.DataFrame] = None,
    primary_timeframe: Optional[str] = None,
    regime_timeframe: Optional[str] = None,
) -> Dict[str, Any]:
    # TODO: make strategy_name a required parameter in a future breaking release
    """Run bar-by-bar replay using the real strategy plugin (ReplayEngine).

    Uses the same StrategyRunner + PositionSimulator as the observability
    replay foundation — preferred over the legacy CDC simulation for fidelity.
    """
    from xauby.strategies import load_strategy
    from xauby.strategies.sandbox import StrategyRunner
    from xauby.observability.replay import PositionSimulator, ReplayEngine

    cfg = engine_config or {}
    trading_cfg = cfg.get("trading") or {}
    strat_cfg = dict(strategy_config or {})

    sl_mult = float(strat_cfg.get("sl_atr_mult") or DEFAULT_SL_ATR_MULT)
    trailing = float(strat_cfg.get("trailing_atr_mult", DEFAULT_TRAILING_ATR_MULT))
    be_enabled = bool(strat_cfg.get("breakeven_sl_enabled", False))
    be_act = float(strat_cfg.get("breakeven_activation_atr_mult", 1.5))
    be_buf = float(strat_cfg.get("breakeven_buffer_atr_mult", 0.1))
    risk_pct = float(
        trading_cfg.get("risk_pct")
        or strat_cfg.get("risk_pct")
        or DEFAULT_RISK_PCT
    )
    fee_pct = resolve_fee_pct(cfg)
    slippage_bps = float(cfg.get("backtest", {}).get("slippage_bps", 2.0))
    funding_rate_8h = float(cfg.get("backtest", {}).get("funding_rate_8h", 0.0))
    max_position_pct = float(trading_cfg.get("max_position_per_trade_pct", 0.0)) / 100.0
    sl_confirm_ticks = int(trading_cfg.get("sl_confirm_ticks", 3))
    fixed_tp_pct = resolve_fixed_tp_pct(strategy_name, strat_cfg)
    minimal_roi = resolve_minimal_roi(strat_cfg)
    partial_tp_pct, partial_tp_fraction = resolve_partial_tp(strat_cfg)
    disable_stop_loss = bool(strat_cfg.get("disable_stop_loss", False))
    position_pct = float(strat_cfg.get("position_pct", 1.0) or 1.0)

    primary_tf, regime_tf = resolve_backtest_timeframes(
        symbol,
        strategy_name,
        cfg,
        primary_override=primary_timeframe,
        regime_override=regime_timeframe,
    )
    use_regime_filter = bool(strat_cfg.get("use_d1_regime_filter", False))
    if not use_regime_filter:
        regime_tf = None
        df_regime = None

    from xauby.runtime.candle_utils import timeframe_seconds

    tf_seconds = timeframe_seconds(primary_tf)
    periods_per_year = (365.0 * 24.0 * 3600.0) / tf_seconds if tf_seconds > 0 else 0.0
    bar_hours = tf_seconds / 3600.0 if tf_seconds > 0 else 0.0

    strategy = load_strategy(strategy_name, strat_cfg)
    runner = StrategyRunner(strategy, timeout=30.0, check_imports=False)
    simulator = PositionSimulator(
        initial_balance=initial_balance,
        fee_pct=fee_pct,
        sl_atr_mult=sl_mult,
        trailing_atr_mult=trailing,
        be_enabled=be_enabled,
        be_activation_atr_mult=be_act,
        be_buffer_atr_mult=be_buf,
        risk_pct=risk_pct,
        max_position_pct=max_position_pct,
        sl_confirm_ticks=sl_confirm_ticks,
        slippage_bps=slippage_bps,
        fixed_tp_pct=fixed_tp_pct,
        minimal_roi=minimal_roi,
        partial_tp_pct=partial_tp_pct,
        partial_tp_fraction=partial_tp_fraction,
        disable_stop_loss=disable_stop_loss,
        position_pct=position_pct,
        funding_rate_8h=funding_rate_8h,
        bar_hours=bar_hours,
    )
    replay = ReplayEngine(
        runner=runner,
        simulator=simulator,
        symbol=symbol,
        timeframe_primary=primary_tf,
        timeframe_regime=regime_tf,
        strategy_config=strat_cfg,
        engine_config=cfg,
    )
    regime_df = normalize_ohlcv_df(df_regime) if df_regime is not None else None
    trades, _events, final_bal, last_checklist = replay.replay_bars(
        df,
        df_regime=regime_df,
        min_bars=int(getattr(strategy, "min_bars", 100) or 100),
    )

    metrics = build_metrics_from_replay(
        trades=trades,
        initial_balance=initial_balance,
        final_balance=final_bal,
        bars=len(df),
        checklist_config=extract_checklist_config(strat_cfg),
        last_checklist=last_checklist,
        equity_curve=simulator._equity_curve,
        periods_per_year=periods_per_year,
        bars_in_position=simulator.bars_in_position,
        total_bars=len(simulator._equity_curve),
        tf_seconds=tf_seconds,
    )
    return metrics.to_dict()
