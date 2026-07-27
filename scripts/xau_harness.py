"""Shared XAU research harness — one definition of the variants and the runner.

Every XAU study in `docs/research/` was produced by a script that re-declared its
own variant table, its own month-window loop and its own warmup slice. Two of
those re-declarations were wrong (see `xauby/backtest/walkforward.py`), and the
error reached published certificates. This module exists so there is exactly one
place to get any of it.

What it centralises:

* :data:`VARIANTS` — the six XAU configs the research talks about, each stating
  **every** key of the `d1_gate` control group. Partial specs inherit from the
  base config, which is how six distinct variants silently collapsed into two
  when `bot_config.yaml` gained `use_d1_regime_filter_long`.
* :func:`run_continuous` / :func:`run_windowed` — the two measurements, both
  routed through :mod:`xauby.backtest.walkforward` so a windowed run cannot
  trade its warmup lead-in.
* :func:`deployed_variant_name` — resolves which catalog entry the *live* config
  currently is, instead of hardcoding a label that rots the next time the
  operator changes production. `xau_phase_breakdown.py` used to label an empty
  override "deployed (long+short, D1 off)"; that stopped being true on
  2026-07-26 and nothing noticed.

Continuous and windowed runs are not interchangeable: a continuous replay
compounds one position series across the whole span, a windowed run restarts
flat each month. Use continuous for headline numbers, windowed for stability and
per-phase attribution.
"""
from __future__ import annotations

import os
import random
import statistics
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from xauby.backtest.service import (
    _prepare_backtest_config,
    resolve_strategy_name,
    run_replay_from_bundle,
)
from xauby.backtest.walkforward import (
    Window,
    WindowResult,
    month_windows,
    phase_label,
    resolve_variant,
    run_slice,
    slice_window,
)

PROXY = "XAUT-USDT"
"""Venue-native gold token. XAU-USDT-SWAP starts 2025-04 — too short for a
walk-forward protocol. Correlation 0.99/1.00, measured in
`docs/research/venue_data_revalidation_2026-07-26.md`."""

SYMBOL = "XAUUSDT"
WARMUP_BARS = 300
DRAWDOWN_FROM = "2026-03"  # gold peaked 2026-02
BOOTSTRAP_SAMPLES = 10000
BOOTSTRAP_SEED = 20260727

D1_KEYS = (
    "use_d1_regime_filter",
    "use_d1_regime_filter_long",
    "use_d1_regime_filter_short",
)


def d1_gate(shared: bool, long_gated: bool, short_gated: bool) -> Dict[str, Any]:
    """Every key of the `d1_gate` control group, always.

    `resolve_variant` rejects a spec that sets some of them and inherits the
    rest, so this helper is the only supported way to express a D1 setting here.
    """
    return dict(zip(D1_KEYS, (shared, long_gated, short_gated)))


# The six configs the XAU research compares. Names match the tables in
# docs/research/ verbatim — changing one here without changing the documents is
# how a figure ends up attributed to the wrong config.
VARIANTS: Dict[str, Dict[str, Any]] = {
    "long-only D1 off": {"enable_short": False, **d1_gate(False, False, False)},
    "long-only D1 on": {"enable_short": False, **d1_gate(True, True, True)},
    "long+short D1 off": {"enable_short": True, **d1_gate(False, False, False)},
    "long+short D1 on": {"enable_short": True, **d1_gate(True, True, True)},
    "L:D1on S:D1off": {"enable_short": True, **d1_gate(True, True, False)},
    "L:D1off S:D1on": {"enable_short": True, **d1_gate(True, False, True)},
}

# Which long-only baseline each short-bearing config must clear by
# `backtest.acceptance.min_profit_edge_pp`. The baseline is the config that
# gates LONGS the same way, because that is the only honest comparison: the
# edge attributed to shorts must not smuggle in a different long-side gate.
GATE_BASELINE: Dict[str, str] = {
    "long+short D1 off": "long-only D1 off",
    "long+short D1 on": "long-only D1 on",
    "L:D1on S:D1off": "long-only D1 on",
    "L:D1off S:D1on": "long-only D1 off",
}


def gated_sides(spec: Mapping[str, Any]) -> tuple[bool, bool]:
    """(long_gated, short_gated) for a spec, resolving `None` to the shared flag."""
    shared = bool(spec.get("use_d1_regime_filter"))
    long_side = spec.get("use_d1_regime_filter_long")
    short_side = spec.get("use_d1_regime_filter_short")
    return (shared if long_side is None else bool(long_side),
            shared if short_side is None else bool(short_side))


def _shape(spec: Mapping[str, Any]) -> tuple:
    """What distinguishes one variant from another, behaviourally.

    A config that never shorts is unaffected by its short-side gate, so that key
    is excluded from the comparison rather than being allowed to split one
    variant into two that trade identically.
    """
    shorts = bool(spec.get("enable_short"))
    long_gated, short_gated = gated_sides(spec)
    return (shorts, long_gated, short_gated if shorts else None)


def deployed_variant_name(
    base_strategy_config: Mapping[str, Any],
    catalog: Mapping[str, Mapping[str, Any]] = None,
) -> Optional[str]:
    """Which :data:`VARIANTS` entry the resolved live config actually is.

    Returns ``None`` when production has moved to a config the catalog does not
    describe — which is a result worth printing, not a crash. The alternative,
    hardcoding "deployed" next to one row, is what let a document keep calling
    `long+short D1 off` "the deployed config" for a day after it was replaced.
    """
    entries = VARIANTS if catalog is None else catalog
    live = _shape(base_strategy_config)
    for name, spec in entries.items():
        if _shape(spec) == live:
            return name
    return None


@dataclass(frozen=True)
class Prepared:
    """Everything a run needs, resolved once through the deployed-config resolver."""

    strategy_name: str
    engine_config: Dict[str, Any]
    base_strategy_config: Dict[str, Any]
    primary_timeframe: str

    def variant_config(self, overrides: Mapping[str, Any]) -> Dict[str, Any]:
        return resolve_variant(self.base_strategy_config, overrides)


def prepare(engine_config: Mapping[str, Any], symbol: str = SYMBOL) -> Prepared:
    """Resolve the strategy + engine config exactly as live and replay do."""
    name = resolve_strategy_name(symbol, dict(engine_config), None)
    merged, strat, primary_tf, _regime_tf, _use_d1, _used = _prepare_backtest_config(
        symbol, name, dict(engine_config), None
    )
    return Prepared(strategy_name=name, engine_config=dict(merged),
                    base_strategy_config=dict(strat), primary_timeframe=primary_tf)


def load_frames(proxy: str = PROXY) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The 4h series and the 1d series the D1 gate reads, straight from OKX."""
    from scripts.validate_on_venue_data import _okx_frame

    return _okx_frame(proxy, "4h"), _okx_frame(proxy, "1d")


def run_continuous(
    prep: Prepared,
    df4: pd.DataFrame,
    df1: pd.DataFrame,
    overrides: Mapping[str, Any],
    *,
    engine_config: Mapping[str, Any],
    label: str = PROXY,
) -> Dict[str, Any]:
    """One uninterrupted replay of the whole series. Headline numbers.

    Goes through the bundle path (not `run_slice`) because there is no warmup to
    skip: the full frame is the run. The variant is resolved through
    `resolve_variant` first, so the control-group check applies here too — the
    continuous stage inherits from the base config just as eagerly as the
    windowed one does.
    """
    from scripts.validate_on_venue_data import _bundle_for

    strat = prep.variant_config(overrides)
    bundle = _bundle_for(SYMBOL, df4, engine_config=dict(engine_config), label=label)
    if any(gated_sides(strat)):
        bundle.df_regime = df1.reset_index(drop=True)
        bundle.regime_tf = "1d"
        bundle.use_d1 = True
    result = run_replay_from_bundle(bundle, strat_cfg_override=strat)
    stats = result.stats or {}
    if not stats:
        raise RuntimeError(
            f"continuous replay failed: {getattr(result.meta, 'error', '') or 'unknown'}"
        )
    return stats


def run_windowed(
    prep: Prepared,
    df4: pd.DataFrame,
    df1: pd.DataFrame,
    overrides: Mapping[str, Any],
    *,
    windows: Optional[Sequence[Window]] = None,
    warmup_bars: int = WARMUP_BARS,
) -> List[WindowResult]:
    """Replay a frozen variant month by month, never trading the warmup lead-in.

    Windows whose phase cannot be labelled are kept and marked ``unknown``. The
    earlier harnesses dropped them, which quietly turned a 48-month record into
    a 40-month one and removed the earliest months from every total.
    """
    strat = prep.variant_config(overrides)
    needs_d1 = any(gated_sides(strat))
    selected = list(windows) if windows is not None else month_windows(df4)
    results: List[WindowResult] = []
    for window in selected:
        sliced = slice_window(df4, window, warmup_bars=warmup_bars)
        if sliced is None or sliced.traded_bars <= 0:
            continue
        stats = run_slice(
            sliced,
            strategy_name=prep.strategy_name,
            strategy_config=strat,
            engine_config=prep.engine_config,
            symbol=SYMBOL,
            primary_timeframe=prep.primary_timeframe,
            df_regime=df1 if needs_d1 else None,
            regime_timeframe="1d" if needs_d1 else None,
        )
        results.append(WindowResult(window=window, stats=stats,
                                    phase=phase_label(df1, window.start_ms)))
    return results


def bootstrap(nets: Sequence[float], samples: int = BOOTSTRAP_SAMPLES) -> Dict[str, Any]:
    """Resample window returns i.i.d.; CI on the compounded result.

    i.i.d. by construction, so it discards autocorrelation and drawdown path and
    it cannot see a regime the sample does not contain. Read it as sampling
    variability *within this regime mix*, never as a probability of safety.
    """
    values = list(nets)
    if len(values) < 5:
        return {"samples": 0, "note": "too few windows to bootstrap"}
    rng = random.Random(BOOTSTRAP_SEED)
    compounded: List[float] = []
    for _ in range(samples):
        total = 1.0
        for _ in range(len(values)):
            total *= 1.0 + rng.choice(values) / 100.0
        compounded.append((total - 1.0) * 100.0)
    compounded.sort()
    return {
        "samples": samples,
        "windows_resampled": len(values),
        "median_pct": round(statistics.median(compounded), 2),
        "p05_pct": round(compounded[int(0.05 * samples)], 2),
        "p95_pct": round(compounded[int(0.95 * samples)], 2),
        "prob_profitable": round(sum(1 for c in compounded if c > 0) / samples, 4),
    }


def buy_and_hold(df_daily: pd.DataFrame, start: str, end: Optional[str] = None) -> Dict[str, float]:
    """Always-invested benchmark on 1d closes. Not a matched-exposure comparison."""
    stamps = pd.to_datetime(df_daily["open_time"], unit="ms")
    selected = df_daily[stamps >= pd.Timestamp(start)]
    if end:
        selected = selected[
            pd.to_datetime(selected["open_time"], unit="ms") < pd.Timestamp(end)
        ]
    close = selected["close"].astype(float)
    peak = close.cummax()
    return {
        "return_pct": round(float(close.iloc[-1] / close.iloc[0] - 1) * 100, 2),
        "max_dd_pct": round(float(((close - peak) / peak * 100).min()), 2),
    }


def drawdown_slice(results: Sequence[WindowResult],
                   since: str = DRAWDOWN_FROM) -> List[WindowResult]:
    """Windows from the gold peak onward — the live-relevant, n=4 slice."""
    return [r for r in results if r.window.label >= since]


def print_variant_banner(prep: Prepared) -> None:
    """State what production currently is, resolved rather than asserted."""
    live = deployed_variant_name(prep.base_strategy_config)
    if live:
        print(f"live XAU config resolves to variant: {live!r}")
    else:
        print("live XAU config matches NO catalog variant — "
              "labels below describe experiments, not production")
