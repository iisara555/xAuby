"""Backtest parameter optimization via grid search."""

from __future__ import annotations

from itertools import product
from typing import Any, Dict, List, Optional

from xauby.backtest.best_params import save_best_parameters as _save_best
from xauby.backtest.constants import DEFAULT_COMPACT_GRID_RUNS
from xauby.backtest.metrics import extract_optimizer_entry
from xauby.backtest.service import load_backtest_replay_bundle, run_replay_from_bundle


PAIR_OPTIMIZATION_PRESETS: Dict[str, Dict[str, Any]] = {}


def _unique(values: List[Any]) -> List[Any]:
    out: List[Any] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out


def _float_variants(value: Any, *, compact: bool, floor: float = 0.0) -> List[float]:
    base = max(floor, float(value or 0.0))
    factors = (0.9, 1.0, 1.1) if compact else (0.75, 0.9, 1.0, 1.1, 1.25)
    return _unique([round(max(floor, base * f), 4) for f in factors])


def _optimizer_cfg(engine_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cfg = engine_config or {}
    return dict((cfg.get("backtest") or {}).get("optimizer") or {})


def _max_grid_runs(engine_config: Optional[Dict[str, Any]], *, compact: bool) -> int:
    opt = _optimizer_cfg(engine_config)
    key = "compact_max_runs" if compact else "max_runs"
    default = DEFAULT_COMPACT_GRID_RUNS if compact else 4
    try:
        return max(1, int(opt.get(key, default)))
    except (TypeError, ValueError):
        return default


def _optimizer_max_bars(engine_config: Optional[Dict[str, Any]]) -> int:
    opt = _optimizer_cfg(engine_config)
    try:
        return max(0, int(opt.get("max_bars", 300)))
    except (TypeError, ValueError):
        return 300


def _oos_settings(engine_config: Optional[Dict[str, Any]]) -> tuple[float, int]:
    """Return (split_ratio, warmup_bars) for in-sample / out-of-sample tuning."""
    opt = _optimizer_cfg(engine_config)
    try:
        ratio = float(opt.get("oos_split_ratio", 0.7))
    except (TypeError, ValueError):
        ratio = 0.7
    if not (0.3 <= ratio <= 0.9):
        ratio = 0.7
    try:
        warmup = max(0, int(opt.get("oos_warmup_bars", 100)))
    except (TypeError, ValueError):
        warmup = 100
    return ratio, warmup


def _evaluate_grid(
    bundle: Any,
    keys: List[str],
    combos: List[tuple],
    *,
    progress_callback: Optional[Any] = None,
) -> tuple[List[Dict[str, Any]], bool]:
    """Run every combo and return (results, used_oos).

    When the bundle carries enough candles, each combo is scored on an
    out-of-sample holdout: tuned on the first ``oos_split_ratio`` of the data,
    validated on the remainder (with a warmup lead-in). The headline metrics on
    each entry are the OOS ones, plus ``is_net_profit_pct`` so selection can
    reward parameters that survive both periods. Falls back to a single
    full-window run when there are too few bars (or in unit tests with a stub
    bundle that has no DataFrame).
    """
    ratio, warmup = _oos_settings(bundle.merged_cfg)
    df = getattr(bundle, "df", None)
    n = len(df) if df is not None and hasattr(df, "iloc") else 0
    # Need both windows to clear warmup and still leave room for trades.
    use_oos = n >= (2 * warmup + 80)

    is_df = oos_df = None
    if use_oos:
        split = int(n * ratio)
        is_df = df.iloc[:split].reset_index(drop=True)
        oos_df = df.iloc[max(0, split - warmup):].reset_index(drop=True)

    results: List[Dict[str, Any]] = []
    total = len(combos)
    for run_idx, values in enumerate(combos, start=1):
        if progress_callback:
            progress_callback(run_idx, total)
        override = dict(zip(keys, values))
        if float(override.get("rsi_min", 0.0)) > float(override.get("rsi_max", 100.0)):
            continue
        if int(override.get("ema_fast", 1)) >= int(override.get("ema_slow", 9999)):
            continue

        if not use_oos:
            res = run_replay_from_bundle(bundle, strat_cfg_override=override)
            if not res.meta.run_ok:
                continue
            results.append(extract_optimizer_entry(res, override))
            continue

        is_res = run_replay_from_bundle(bundle, strat_cfg_override=override, df_override=is_df)
        if not is_res.meta.run_ok:
            continue
        oos_res = run_replay_from_bundle(bundle, strat_cfg_override=override, df_override=oos_df)
        if not oos_res.meta.run_ok:
            continue
        entry = extract_optimizer_entry(oos_res, override)  # headline = OOS
        entry["is_net_profit_pct"] = float(is_res.stats.get("net_profit_pct", 0.0) or 0.0)
        entry["oos_net_profit_pct"] = entry["net_profit_pct"]
        entry["oos_profit_factor"] = entry["profit_factor"]
        entry["oos_sharpe"] = float(oos_res.stats.get("sharpe", 0.0) or 0.0)
        results.append(entry)

    return results, use_oos


def _select_best(results: List[Dict[str, Any]], used_oos: bool) -> Dict[str, Any]:
    """Pick the winning parameter set.

    With OOS scoring, prefer combos profitable in BOTH windows (robustness),
    ranked by risk-adjusted OOS Sharpe then OOS net profit. Without OOS, fall
    back to the legacy net-profit / profit-factor ranking.
    """
    if not results:
        return {}
    if used_oos:
        robust = [
            e for e in results
            if e.get("is_net_profit_pct", 0.0) > 0.0 and e.get("oos_net_profit_pct", 0.0) > 0.0
        ]
        pool = robust or results
        pool.sort(
            key=lambda e: (e.get("oos_sharpe", 0.0), e.get("oos_net_profit_pct", 0.0)),
            reverse=True,
        )
        return pool[0]
    results.sort(key=lambda x: (x["net_profit_pct"], x.get("profit_factor", 0.0)), reverse=True)
    return results[0]


def _limit_bundle_bars(bundle: Any) -> Any:
    """Limit optimization replay bars only; normal backtest remains full history."""
    max_bars = _optimizer_max_bars(bundle.merged_cfg)
    if max_bars <= 0:
        return bundle
    try:
        if len(bundle.df) > max_bars:
            bundle.df = bundle.df.tail(max_bars).copy()
            bundle.bars = len(bundle.df)
            if "timestamp" in bundle.df.columns and len(bundle.df):
                bundle.period_start = str(bundle.df["timestamp"].iloc[0])
                bundle.period_end = str(bundle.df["timestamp"].iloc[-1])
    except Exception:
        pass
    try:
        if bundle.df_regime is not None and len(bundle.df_regime) > max_bars:
            bundle.df_regime = bundle.df_regime.tail(max_bars).copy()
            bundle.regime_bars = len(bundle.df_regime)
    except Exception:
        pass
    return bundle


def _combo_distance(keys: List[str], values: tuple, baseline: Dict[str, Any]) -> float:
    """Rank candidate tuples by closeness to current live config."""
    dist = 0.0
    for key, value in zip(keys, values):
        base = baseline.get(key)
        if isinstance(value, bool):
            dist += 0.0 if bool(base) == value else 10.0
            continue
        try:
            fv = float(value)
            fb = float(base)
            scale = max(abs(fb), 1.0)
            dist += abs(fv - fb) / scale
        except (TypeError, ValueError):
            dist += 0.0 if value == base else 1.0
    return dist


def _budgeted_combos(
    keys: List[str],
    grid: Dict[str, List[Any]],
    baseline: Dict[str, Any],
    max_runs: int,
) -> List[tuple]:
    combos = list(product(*[grid[k] for k in keys]))
    combos.sort(key=lambda vals: _combo_distance(keys, vals, baseline))
    return combos[:max_runs]


def _optimization_grid(
    strat_cfg: Optional[Dict[str, Any]] = None,
    *,
    compact: bool = False,
) -> Dict[str, List[Any]]:
    """Build optimizer candidates from the live-merged strategy config.

    Disabled checklist filters stay disabled. That keeps optimization from
    silently reintroducing RSI/volume/fresh gates that live is not using.
    """
    cfg = dict(strat_cfg or {})
    grid: Dict[str, List[Any]] = {
        "sl_atr_mult": _float_variants(cfg.get("sl_atr_mult", 2.5), compact=compact, floor=0.1),
        "trailing_atr_mult": _float_variants(
            cfg.get("trailing_atr_mult", 1.5), compact=compact, floor=0.1
        ),
        "ap_smoothing": [int(cfg.get("ap_smoothing", 1) or 1)],
        "require_fresh_zone": [bool(cfg.get("require_fresh_zone", True))],
        "fresh_zone_window": [int(cfg.get("fresh_zone_window", 3) or 3)],
    }

    rsi_min = float(cfg.get("rsi_min", 45.0))
    rsi_max = float(cfg.get("rsi_max", 70.0))
    if rsi_min <= 0.0 and rsi_max >= 100.0:
        grid["rsi_min"] = [0.0]
        grid["rsi_max"] = [100.0]
    else:
        grid["rsi_min"] = _unique(
            [max(0.0, round(v, 2)) for v in (rsi_min - 5.0, rsi_min, rsi_min + 5.0)]
        )
        grid["rsi_max"] = _unique(
            [min(100.0, round(v, 2)) for v in (rsi_max - 5.0, rsi_max, rsi_max + 5.0)]
        )

    vol_min = float(cfg.get("vol_min_ratio", 1.0))
    if vol_min <= 0.0:
        grid["vol_min_ratio"] = [0.0]
    else:
        grid["vol_min_ratio"] = _float_variants(vol_min, compact=compact, floor=0.0)

    if bool(cfg.get("breakeven_sl_enabled", False)):
        grid["breakeven_sl_enabled"] = [True]
        grid["breakeven_activation_atr_mult"] = _float_variants(
            cfg.get("breakeven_activation_atr_mult", 1.5), compact=compact, floor=0.1
        )
        grid["breakeven_buffer_atr_mult"] = _float_variants(
            cfg.get("breakeven_buffer_atr_mult", 0.1), compact=compact, floor=0.0
        )

    return grid


def _int_variants(value: Any, values: List[int]) -> List[int]:
    try:
        base = int(value)
    except (TypeError, ValueError):
        base = values[0]
    return _unique([base] + [int(v) for v in values])


def _ict_optimization_grid(strat_cfg: Optional[Dict[str, Any]] = None, *, compact: bool = False) -> Dict[str, List[Any]]:
    """Build candidates for ICT Lite using only parameters the strategy consumes."""
    cfg = dict(strat_cfg or {})
    if compact:
        return {
            "ema_fast": _int_variants(cfg.get("ema_fast", 20), [12, 20, 30]),
            "ema_slow": _int_variants(cfg.get("ema_slow", 50), [34, 50, 89]),
            "swing_lookback": _int_variants(cfg.get("swing_lookback", 20), [12, 20, 34]),
            "reclaim_window": _int_variants(cfg.get("reclaim_window", 3), [2, 3, 5]),
            "mss_lookback": _int_variants(cfg.get("mss_lookback", 5), [3, 5, 8]),
            "require_mss": [bool(cfg.get("require_mss", True)), False, True],
            "min_body_atr": _unique([float(cfg.get("min_body_atr", 0.15)), 0.08, 0.12, 0.2]),
            "sl_atr_mult": _float_variants(cfg.get("sl_atr_mult", 2.5), compact=True, floor=0.1),
            "trailing_atr_mult": _float_variants(cfg.get("trailing_atr_mult", 1.5), compact=True, floor=0.1),
            "rsi_min": _unique([float(cfg.get("rsi_min", 45.0)), 0.0, 35.0, 40.0]),
            "rsi_max": _unique([float(cfg.get("rsi_max", 70.0)), 100.0, 75.0, 80.0]),
            "vol_min_ratio": _unique([float(cfg.get("vol_min_ratio", 1.0)), 0.0, 0.8, 1.0]),
        }
    return {
        "ema_fast": _int_variants(cfg.get("ema_fast", 20), [10, 12, 20, 30]),
        "ema_slow": _int_variants(cfg.get("ema_slow", 50), [34, 50, 89]),
        "swing_lookback": _int_variants(cfg.get("swing_lookback", 20), [10, 12, 20, 34]),
        "reclaim_window": _int_variants(cfg.get("reclaim_window", 3), [2, 3, 5, 8]),
        "mss_lookback": _int_variants(cfg.get("mss_lookback", 5), [3, 5, 8, 13]),
        "require_mss": [bool(cfg.get("require_mss", True)), False, True],
        "min_body_atr": _unique([float(cfg.get("min_body_atr", 0.15)), 0.05, 0.08, 0.12, 0.2]),
        "sl_atr_mult": _float_variants(cfg.get("sl_atr_mult", 2.5), compact=False, floor=0.1),
        "trailing_atr_mult": _float_variants(cfg.get("trailing_atr_mult", 1.5), compact=False, floor=0.1),
        "rsi_min": _unique([float(cfg.get("rsi_min", 45.0)), 0.0, 30.0, 35.0, 40.0, 45.0]),
        "rsi_max": _unique([float(cfg.get("rsi_max", 70.0)), 70.0, 75.0, 80.0, 100.0]),
        "vol_min_ratio": _unique([float(cfg.get("vol_min_ratio", 1.0)), 0.0, 0.6, 0.8, 1.0]),
    }


def optimize_grid_for_symbol(
    symbol: str,
    *,
    strategy_name: Optional[str] = None,
    engine_config: Optional[Dict[str, Any]] = None,
    bot_state: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[Any] = None,
    compact: bool = False,
) -> Dict[str, Any]:
    """Grid-search key strategy params via plugin replay for one symbol."""
    sym = str(symbol or "XAUTUSDT").upper().replace("_", "")
    bundle = load_backtest_replay_bundle(
        sym,
        strategy_name=strategy_name,
        engine_config=engine_config,
        bot_state=bot_state,
    )
    bundle = _limit_bundle_bars(bundle)

    try:
        bundle_strategy_name = bundle.strategy_name
    except AttributeError:
        bundle_strategy_name = strategy_name
    if bundle_strategy_name == "ict_lite_strategy":
        grid = _ict_optimization_grid(bundle.strat_cfg, compact=compact)
    else:
        grid = _optimization_grid(bundle.strat_cfg, compact=compact)
    keys = list(grid.keys())
    max_runs = _max_grid_runs(bundle.merged_cfg, compact=compact)
    combos = _budgeted_combos(keys, grid, bundle.strat_cfg, max_runs)

    results, used_oos = _evaluate_grid(
        bundle, keys, combos, progress_callback=progress_callback
    )
    best = _select_best(results, used_oos)
    if best:
        _save_best(sym, best)
    return best


def optimize_pair_strategy_extended(
    symbol: str,
    *,
    strategy_name: Optional[str] = None,
    engine_config: Optional[Dict[str, Any]] = None,
    bot_state: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[Any] = None,
    save: bool = True,
) -> Dict[str, Any]:
    """Extended compatibility entrypoint; now uses the live-derived grid."""
    sym = str(symbol or "XAUTUSDT").upper().replace("_", "")
    bundle = load_backtest_replay_bundle(
        sym,
        strategy_name=strategy_name,
        engine_config=engine_config,
        bot_state=bot_state,
    )
    bundle = _limit_bundle_bars(bundle)

    try:
        bundle_strategy_name = bundle.strategy_name
    except AttributeError:
        bundle_strategy_name = strategy_name
    if bundle_strategy_name == "ict_lite_strategy":
        grid = _ict_optimization_grid(bundle.strat_cfg, compact=False)
    else:
        grid = _optimization_grid(bundle.strat_cfg, compact=False)
    keys = list(grid.keys())
    max_runs = _max_grid_runs(bundle.merged_cfg, compact=False)
    combos = _budgeted_combos(keys, grid, bundle.strat_cfg, max_runs)

    results, used_oos = _evaluate_grid(
        bundle, keys, combos, progress_callback=progress_callback
    )
    best = _select_best(results, used_oos)
    if best and save:
        _save_best(sym, best)
    return best
