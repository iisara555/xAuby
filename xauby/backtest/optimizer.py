"""Backtest parameter optimization via grid search."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any, Dict, List, Optional, Tuple

from xauby.backtest.best_params import save_best_parameters as _save_best
from xauby.backtest.constants import DEFAULT_COMPACT_GRID_RUNS
from xauby.backtest.metrics import extract_optimizer_entry
from xauby.backtest.service import load_backtest_replay_bundle, run_replay_from_bundle


PAIR_OPTIMIZATION_PRESETS: Dict[str, Dict[str, Any]] = {}

# The repo's own pre-registered admission thresholds, taken verbatim from
# scripts/actionzone_wfa_sweep.py rather than invented here. A parameter set
# chosen on fewer trades than this is chosen on noise.
MIN_IS_TRADES = 40
MIN_OOS_TRADES = 18


@dataclass
class OptimizerVerdict:
    """Whether the sample can support a selection at all, and why not.

    Roadmap P1.2. Measured on the shipped config (``max_bars: 300``,
    ``oos_split_ratio: 0.7``, ``oos_warmup_bars: 100``), neither live pair could:

    * ``supertrend_ema200`` needs 240 bars before it emits anything, and both
      windows are shorter than that (210 in-sample, 190 out-of-sample). Every
      combination scored exactly 0.0 on every metric, ``robust`` was empty, and
      the winner was whichever tuple happened to sort first — a coin flip
      returned as an optimization result.
    * ``xauby_actionzone`` produced **one** trade per window, and the selection
      ranked candidates on an annualised Sharpe computed from that single trade.

    Neither failure was visible: both paths returned a populated dict and saved
    it as the pair's best parameters. So the optimizer now reports why it
    declined instead of answering anyway.
    """

    admissible: bool
    reason: str = ""
    total_bars: int = 0
    is_bars: int = 0
    oos_bars: int = 0
    strategy_min_bars: int = 0
    best_is_trades: int = 0
    best_oos_trades: int = 0
    trials: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "admissible": self.admissible,
            "reason": self.reason,
            "total_bars": self.total_bars,
            "is_bars": self.is_bars,
            "oos_bars": self.oos_bars,
            "strategy_min_bars": self.strategy_min_bars,
            "best_is_trades": self.best_is_trades,
            "best_oos_trades": self.best_oos_trades,
            "trials": self.trials,
            "min_is_trades": MIN_IS_TRADES,
            "min_oos_trades": MIN_OOS_TRADES,
        }


def _strategy_min_bars(bundle: Any) -> int:
    try:
        from xauby.strategies import load_strategy

        return int(getattr(load_strategy(bundle.strategy_name, {}), "min_bars", 0) or 0)
    except Exception:
        return 0


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
    oos_skip = 0
    if use_oos:
        split = int(n * ratio)
        lo = max(0, split - warmup)
        is_df = df.iloc[:split].reset_index(drop=True)
        oos_df = df.iloc[lo:].reset_index(drop=True)
        # Bars of the slice that are lead-in, not out-of-sample. Passed as
        # min_bars_override so trading starts exactly at the split whatever the
        # strategy's own min_bars is. Without it the replay skips min_bars
        # instead: 100 for xauby_actionzone (equal to oos_warmup_bars purely by
        # coincidence), 240 for supertrend_ema200 — longer than the whole slice,
        # so it never traded and every combo scored 0.0.
        oos_skip = split - lo

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
        oos_res = run_replay_from_bundle(bundle, strat_cfg_override=override,
                                         df_override=oos_df,
                                         min_bars_override=oos_skip)
        if not oos_res.meta.run_ok:
            continue
        entry = extract_optimizer_entry(oos_res, override)  # headline = OOS
        entry["is_net_profit_pct"] = float(is_res.stats.get("net_profit_pct", 0.0) or 0.0)
        entry["is_trades"] = int(is_res.stats.get("total_trades", 0) or 0)
        entry["oos_net_profit_pct"] = entry["net_profit_pct"]
        entry["oos_profit_factor"] = entry["profit_factor"]
        entry["oos_sharpe"] = float(oos_res.stats.get("sharpe", 0.0) or 0.0)
        entry["oos_trades"] = int(oos_res.stats.get("total_trades", 0) or 0)
        results.append(entry)

    return results, use_oos


def _admissibility(
    bundle: Any,
    results: List[Dict[str, Any]],
    used_oos: bool,
    trials: int,
) -> OptimizerVerdict:
    """Can this sample support a selection at all?

    Applies the repo's own pre-registered minimums rather than reporting
    whichever combination happened to score highest on a handful of trades. A
    ranking over four candidates that each traded once is not an optimization,
    and calling it one is how a coin flip gets saved as a pair's best parameters.
    """
    ratio, warmup = _oos_settings(bundle.merged_cfg)
    df = getattr(bundle, "df", None)
    n = len(df) if df is not None and hasattr(df, "iloc") else 0
    split = int(n * ratio) if n else 0
    verdict = OptimizerVerdict(
        admissible=False,
        total_bars=n,
        is_bars=split,
        oos_bars=max(0, n - split),
        strategy_min_bars=_strategy_min_bars(bundle),
        trials=trials,
    )

    if not results:
        verdict.reason = "no combination completed a replay"
        return verdict

    if not used_oos:
        # A full-window run is a probe, not a validation: the parameters are
        # scored on the same bars they were chosen from.
        verdict.reason = (
            f"only {n} bars, below the {2 * warmup + 80} needed to hold out an "
            "out-of-sample window; a single full-window ranking selects on the "
            "data it was tuned on"
        )
        return verdict

    verdict.best_is_trades = max(int(e.get("is_trades", 0) or 0) for e in results)
    verdict.best_oos_trades = max(int(e.get("oos_trades", 0) or 0) for e in results)
    if verdict.strategy_min_bars > split:
        verdict.reason = (
            f"{bundle.strategy_name} needs {verdict.strategy_min_bars} bars "
            f"before it emits a signal and the in-sample window is {split}; "
            "every combination scores zero and the winner would be whichever "
            "sorted first"
        )
        return verdict
    if verdict.best_is_trades < MIN_IS_TRADES or verdict.best_oos_trades < MIN_OOS_TRADES:
        verdict.reason = (
            f"best combination traded {verdict.best_is_trades}x in-sample "
            f"and {verdict.best_oos_trades}x out-of-sample, against the "
            f"pre-registered minimum of {MIN_IS_TRADES}/{MIN_OOS_TRADES}; "
            "raise backtest.optimizer.max_bars and re-run off the trading host"
        )
        return verdict

    verdict.admissible = True
    verdict.reason = (
        f"best of {trials} trials, {verdict.best_is_trades} in-sample and "
        f"{verdict.best_oos_trades} out-of-sample trades"
    )
    return verdict


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
    verdict = _admissibility(bundle, results, used_oos, len(combos))
    if not verdict.admissible:
        # Declining is the result. Returning the top row of an unusable ranking
        # would be indistinguishable from a real optimization to every caller.
        return {"admissible": False, "verdict": verdict.to_dict()}
    best = _select_best(results, used_oos)
    if best:
        best["verdict"] = verdict.to_dict()
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
    verdict = _admissibility(bundle, results, used_oos, len(combos))
    if not verdict.admissible:
        return {"admissible": False, "verdict": verdict.to_dict()}
    best = _select_best(results, used_oos)
    if best:
        best["verdict"] = verdict.to_dict()
        if save:
            _save_best(sym, best)
    return best
