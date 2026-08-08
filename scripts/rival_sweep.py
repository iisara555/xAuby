#!/usr/bin/env python3
"""Replay every registered strategy plugin over one symbol's candles.

The "which plugin should hold this slot" question is the same on gold and on
BTC, and this repo has already paid for re-declaring shared research machinery
twice (see the module docstring of `scripts/xau_harness.py`). So it lives here
once, parameterised by symbol, and the per-pair scripts supply data, costs and
an incumbent to beat.

Two rules the callers must not quietly break:

* **A rival runs on its OWN default config.** `_bundle_for` resolves each
  plugin's `strat_cfg` through the production resolver, so a challenger is
  judged on its own stop, trailing and sizing model. Handing it the incumbent's
  exits would measure the incumbent wearing a different entry signal.
* **Net is therefore not comparable across rows; profit factor is.** A plugin
  sizing at `risk_pct` 2% of equity per SL distance cannot produce the net of
  one sizing at `position_pct` 0.95, and the gap says nothing about edge.
  :func:`format_row` prints exposure next to net so the difference is visible
  rather than inferred.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from scripts.validate_on_venue_data import _bundle_for
from xauby.backtest.service import run_replay_from_bundle
from xauby.strategies.registry import available_strategies, strategy_manifest


# Short-only research plugins cannot hold a slot on their own, so they are never
# candidates. The incumbent is excluded by the caller, which runs it as the
# benchmark instead of as one row among the challengers.
SHORT_ONLY = {"donchian_short", "rsi2_short", "supertrend_short"}

METRIC_KEYS = (
    "net_profit_pct",
    "win_rate",
    "profit_factor",
    "max_drawdown_pct",
    "total_trades",
    "sharpe",
    "sortino",
    "calmar",
    "cagr_pct",
    "exposure_pct",
    "avg_holding_bars",
    "max_consecutive_losses",
)


def rival_items(incumbent: str, *, skip: Sequence[str] = ()) -> List[Dict[str, Any]]:
    """Every plugin that could plausibly take the slot, long-only and long+short.

    ``enable_short`` is set on both arms even for plugins that never emit a
    short: an identical pair of rows is the evidence that the key is inert for
    that plugin, which is worth seeing rather than assuming.
    """
    excluded = {incumbent, *SHORT_ONLY, *skip}
    items: List[Dict[str, Any]] = []
    for name in available_strategies():
        if name in excluded:
            continue
        manifest = strategy_manifest(name)
        for shorts in (False, True):
            items.append(
                {
                    "id": f"{name}__{'ls' if shorts else 'long'}",
                    "strategy": name,
                    "enable_short": shorts,
                    "maturity": manifest.get("maturity"),
                    "native_tf": manifest.get("required_timeframes"),
                    "override": {"enable_short": shorts},
                }
            )
    return items


def metrics(stats: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: stats.get(key) for key in METRIC_KEYS}


def run_rival(
    frame: pd.DataFrame,
    item: Mapping[str, Any],
    *,
    symbol: str,
    engine_config: Mapping[str, Any],
    skip_bars: int,
    label: str,
    df_regime: Optional[pd.DataFrame] = None,
    regime_tf: Optional[str] = None,
) -> Dict[str, Any]:
    """Replay one rival on ``frame``, using that plugin's own resolved config."""
    bundle = _bundle_for(
        symbol, frame,
        engine_config=dict(engine_config),
        strategy_name=item["strategy"],
        label=label,
    )
    if df_regime is not None:
        bundle.df_regime = df_regime.reset_index(drop=True)
        bundle.regime_tf = regime_tf
        bundle.use_d1 = True
    strat = {**dict(bundle.strat_cfg), **dict(item["override"])}
    result = run_replay_from_bundle(bundle, strat_cfg_override=strat,
                                    min_bars_override=skip_bars)
    if not result.meta.run_ok:
        raise RuntimeError(result.meta.error or "replay failed")
    return metrics(result.stats or {})


def pf(row: Mapping[str, Any], side: str) -> float:
    return float((row.get(side) or {}).get("profit_factor") or 0.0)


def net(row: Mapping[str, Any], side: str) -> float:
    return float((row.get(side) or {}).get("net_profit_pct") or 0.0)


def passes(row: Mapping[str, Any], *, min_is: int, min_oos: int) -> bool:
    """Pre-declared validity gate: enough trades, and both windows profitable."""
    if row.get("error"):
        return False
    ins, oos = row.get("is") or {}, row.get("oos") or {}
    return bool(
        int(ins.get("total_trades") or 0) >= min_is
        and int(oos.get("total_trades") or 0) >= min_oos
        and float(ins.get("net_profit_pct") or 0.0) > 0.0
        and float(oos.get("net_profit_pct") or 0.0) > 0.0
    )


def format_row(row: Mapping[str, Any], side: str) -> str:
    block = row.get(side) or {}
    if not block:
        return "—"
    return (f"PF {float(block.get('profit_factor') or 0):5.3f} "
            f"net {float(block.get('net_profit_pct') or 0):+8.2f}% "
            f"MDD {float(block.get('max_drawdown_pct') or 0):5.2f}% "
            f"exp {float(block.get('exposure_pct') or 0):4.1f}% "
            f"n={int(block.get('total_trades') or 0):4d}")


def collect(pool: Any, fn: Any, items: Sequence[Mapping[str, Any]],
            *, label: str) -> List[Dict[str, Any]]:
    """Collect results, leaving progress breadcrumbs for long unattended runs."""
    total = len(items)
    rows: List[Dict[str, Any]] = []
    stream = (pool.imap_unordered(fn, items, chunksize=1) if pool
              else (fn(item) for item in items))
    interval = max(1, min(20, total // 10 or 1))
    for done, row in enumerate(stream, start=1):
        rows.append(row)
        if done == total or done % interval == 0:
            print(f"{label}: {done}/{total}", flush=True)
    return rows


def fold_slices(df: pd.DataFrame, *, n_folds: int, warmup_bars: int):
    """Chronological folds, each with a non-traded lead-in where history exists."""
    segment = len(df) // n_folds
    for fold in range(n_folds):
        traded_start = fold * segment
        start = max(0, traded_start - warmup_bars)
        end = len(df) if fold == n_folds - 1 else (fold + 1) * segment
        yield df.iloc[start:end].reset_index(drop=True), traded_start - start


def buy_and_hold(df: pd.DataFrame) -> Dict[str, float]:
    """Always-invested benchmark on the frame's closes. Not matched exposure."""
    close = df["close"].astype(float)
    peak = close.cummax()
    return {
        "return_pct": round(float(close.iloc[-1] / close.iloc[0] - 1) * 100, 2),
        "max_dd_pct": round(float(((close - peak) / peak * 100).min()), 2),
    }
