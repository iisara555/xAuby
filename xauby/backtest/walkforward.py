"""Walk-forward evaluation as a library, not as copy-paste in throwaway scripts.

Roadmap P1.1. The repo's best research came out of two untested scripts
(`scripts/btc_wfa_multi_strategy.py`, `scripts/actionzone_wfa_sweep.py`), and
`grep -rn walk_forward xauby/` found nothing. Everyone who needed walk-forward
re-implemented it — and two of those re-implementations were wrong in ways that
changed published numbers.

Both mistakes are structural, so this module makes them unrepresentable rather
than merely documented:

1. **A warmup lead-in must warm indicators without trading.** The scripts slice
   `warmup` bars before the window and pass that count as
   ``min_bars_override``. Forget the second half and the replay trades the
   lead-in: with 300 warmup bars and a strategy whose ``min_bars`` is 100, 200
   bars of the *previous* window get counted again, so consecutive monthly
   windows overlap by roughly a month. Measured on XAU 2026-03: 10 trades and
   +8.71% as-run against 4 trades and +1.93% correct.
   Here the bar count and the skip travel together in :class:`WindowSlice`, and
   :func:`run_slice` always forwards it. There is no way to slice a window and
   forget the override.

2. **A variant must state every key it controls.** Overriding a subset lets the
   base config leak in. When ``bot_config.yaml`` gained
   ``use_d1_regime_filter_long``, six D1 variants that only set
   ``use_d1_regime_filter`` silently collapsed into two, because the new key
   came from the base for all of them. :func:`resolve_variant` rejects a spec
   that touches part of a :data:`CONTROL_GROUPS` entry without setting all of
   it.

Semantics are preserved exactly as the certificate-producing scripts had them:
warmup is past-data-only, windows are half-open ``[start, end)``, and phase
labels use closes strictly before the window.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

# Keys that move together. A variant touching one member must set them all,
# because the base config supplies whatever is left — which is exactly how six
# distinct D1 variants became two.
CONTROL_GROUPS: Dict[str, Tuple[str, ...]] = {
    "d1_gate": (
        "use_d1_regime_filter",
        "use_d1_regime_filter_long",
        "use_d1_regime_filter_short",
    ),
    "partial_tp": ("partial_tp_pct", "partial_tp_fraction"),
}

DEFAULT_WARMUP_BARS = 300
_EMA_LEN = 200
_SLOPE_LOOKBACK = 21
_MIN_PHASE_HISTORY = _EMA_LEN + _SLOPE_LOOKBACK


class VariantSpecError(ValueError):
    """A variant left part of a control group to be inherited from the base."""


@dataclass(frozen=True)
class Window:
    """A half-open evaluation window ``[start_ms, end_ms)``."""

    label: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class WindowSlice:
    """Bars for a window plus the leading count that must NOT be traded.

    ``skip_bars`` is not advisory. :func:`run_slice` forwards it as
    ``min_bars_override`` on every call, which is the whole reason the frame and
    the count are one object instead of two arguments a caller can mismatch.
    """

    window: Window
    frame: pd.DataFrame
    skip_bars: int

    @property
    def traded_bars(self) -> int:
        return max(0, len(self.frame) - self.skip_bars)


@dataclass
class WindowResult:
    window: Window
    stats: Dict[str, Any] = field(default_factory=dict)
    phase: str = "unknown"

    @property
    def net_pct(self) -> float:
        return float(self.stats.get("net_profit_pct", 0.0) or 0.0)

    @property
    def trades(self) -> int:
        return int(self.stats.get("total_trades", 0) or 0)


def _timestamp_column(df: pd.DataFrame) -> str:
    for name in ("open_time", "timestamp"):
        if name in df.columns:
            return name
    raise KeyError("frame has neither 'open_time' nor 'timestamp'")


def _as_ms(df: pd.DataFrame, column: str) -> pd.Series:
    values = df[column].astype("int64")
    # `timestamp` is seconds in this codebase; `open_time` is milliseconds.
    return values * 1000 if column == "timestamp" else values


def month_windows(
    df: pd.DataFrame,
    *,
    complete_only: bool = True,
    first: Optional[str] = None,
) -> List[Window]:
    """Calendar-month windows covering ``df``.

    ``complete_only`` drops a trailing partial month. Comparing a part-month
    against full ones in the same compounded total silently flatters whichever
    config happens to be ahead mid-month.
    """
    column = _timestamp_column(df)
    ms = _as_ms(df, column)
    stamps = pd.to_datetime(ms, unit="ms")
    out: List[Window] = []
    for (year, month), group in df.groupby([stamps.dt.year, stamps.dt.month]):
        label = f"{int(year)}-{int(month):02d}"
        if first and label < first:
            continue
        start = pd.Timestamp(year=int(year), month=int(month), day=1)
        end = start + pd.offsets.MonthBegin(1)
        out.append(Window(label=label,
                          start_ms=int(start.timestamp() * 1000),
                          end_ms=int(end.timestamp() * 1000)))
    out.sort(key=lambda w: w.label)
    if complete_only and out:
        last_bar = pd.to_datetime(int(_as_ms(df, column).iloc[-1]), unit="ms")
        if last_bar.day < 28:
            out = out[:-1]
    return out


def slice_window(
    df: pd.DataFrame,
    window: Window,
    *,
    warmup_bars: int = DEFAULT_WARMUP_BARS,
) -> Optional[WindowSlice]:
    """Bars in ``[start, end)`` plus up to ``warmup_bars`` of PRIOR history.

    Returns ``None`` when the window holds no bars. The warmup is past-data
    only — never bars from after the window — matching
    ``btc_wfa_multi_strategy._slice``.
    """
    column = _timestamp_column(df)
    ms = _as_ms(df, column)
    inside = df.index[(ms >= window.start_ms) & (ms < window.end_ms)]
    if len(inside) == 0:
        return None
    first_pos = int(df.index.get_loc(inside[0]))
    last_pos = int(df.index.get_loc(inside[-1]))
    lo = max(0, first_pos - int(warmup_bars))
    frame = df.iloc[lo:last_pos + 1].reset_index(drop=True)
    return WindowSlice(window=window, frame=frame, skip_bars=first_pos - lo)


def resolve_variant(
    base: Mapping[str, Any],
    overrides: Mapping[str, Any],
    *,
    control_groups: Mapping[str, Sequence[str]] = None,
) -> Dict[str, Any]:
    """Merge ``overrides`` onto ``base``, refusing partial control groups.

    Touch one key of a group and you must set them all. Otherwise the untouched
    members come from ``base``, and a later edit to the base config silently
    changes what the experiment is measuring — which is how six D1 variants
    became two without any test noticing.
    """
    groups = CONTROL_GROUPS if control_groups is None else control_groups
    for name, keys in groups.items():
        touched = [k for k in keys if k in overrides]
        if not touched:
            continue
        missing = [k for k in keys if k not in overrides]
        if missing:
            raise VariantSpecError(
                f"variant sets {sorted(touched)} from control group '{name}' but "
                f"leaves {sorted(missing)} to be inherited from the base config. "
                "State every key in the group explicitly, so editing the base "
                "cannot change what this variant measures."
            )
    return {**dict(base), **dict(overrides)}


def run_slice(
    slice_: WindowSlice,
    *,
    strategy_name: str,
    strategy_config: Mapping[str, Any],
    engine_config: Mapping[str, Any],
    symbol: str,
    primary_timeframe: str = "4h",
    df_regime: Optional[pd.DataFrame] = None,
    regime_timeframe: Optional[str] = None,
) -> Dict[str, Any]:
    """Replay one slice, never trading its warmup lead-in.

    ``min_bars_override`` is taken from the slice rather than accepted as an
    argument: that is the one thing callers kept forgetting.
    """
    from xauby.backtest.replay import run_plugin_replay

    return run_plugin_replay(
        slice_.frame,
        strategy_config=dict(strategy_config),
        engine_config=dict(engine_config),
        symbol=symbol,
        strategy_name=strategy_name,
        df_regime=df_regime,
        primary_timeframe=primary_timeframe,
        regime_timeframe=regime_timeframe,
        min_bars_override=slice_.skip_bars,
    )


def phase_label(df_daily: pd.DataFrame, before_ms: int) -> str:
    """Bull/bear/sideways from the 1d EMA200, using only closes before ``before_ms``.

    Same definition the BTC certificate used, kept in one place because it was
    duplicated across two scripts and drifted in neither — yet.
    """
    import pandas_ta as ta

    column = _timestamp_column(df_daily)
    ms = _as_ms(df_daily, column)
    history = df_daily[ms < before_ms]
    if len(history) < _MIN_PHASE_HISTORY:
        return "unknown"
    close = history["close"].astype(float).reset_index(drop=True)
    ema = ta.ema(close, length=_EMA_LEN)
    if ema is None or bool(pd.isna(ema.iloc[-1])):
        return "unknown"
    last = float(close.iloc[-1])
    now = float(ema.iloc[-1])
    prev = float(ema.iloc[-1 - _SLOPE_LOOKBACK])
    rising = now > prev
    if last > now and rising:
        return "bull"
    if last < now and not rising:
        return "bear"
    return "sideways"


def walk_forward(
    df: pd.DataFrame,
    *,
    strategy_name: str,
    strategy_config: Mapping[str, Any],
    engine_config: Mapping[str, Any],
    symbol: str,
    windows: Optional[Iterable[Window]] = None,
    warmup_bars: int = DEFAULT_WARMUP_BARS,
    primary_timeframe: str = "4h",
    df_regime: Optional[pd.DataFrame] = None,
    regime_timeframe: Optional[str] = None,
    df_daily_for_phase: Optional[pd.DataFrame] = None,
) -> List[WindowResult]:
    """Replay a frozen config across windows. No per-window re-optimization."""
    selected = list(windows) if windows is not None else month_windows(df)
    results: List[WindowResult] = []
    for window in selected:
        sliced = slice_window(df, window, warmup_bars=warmup_bars)
        if sliced is None or sliced.traded_bars <= 0:
            continue
        stats = run_slice(
            sliced,
            strategy_name=strategy_name,
            strategy_config=strategy_config,
            engine_config=engine_config,
            symbol=symbol,
            primary_timeframe=primary_timeframe,
            df_regime=df_regime,
            regime_timeframe=regime_timeframe,
        )
        phase = "unknown"
        if df_daily_for_phase is not None:
            phase = phase_label(df_daily_for_phase, window.start_ms)
        results.append(WindowResult(window=window, stats=stats, phase=phase))
    return results


def aggregate(results: Sequence[WindowResult]) -> Dict[str, Any]:
    """Compound window returns, matching the BTC certificate's reporting.

    Each window starts flat, so this is a compounded series of independent
    windows — not the same thing as one continuous run, and not interchangeable
    with it. Use a continuous replay for a headline return; use this for
    stability and per-phase attribution.
    """
    if not results:
        return {"windows": 0}
    compounded = 1.0
    for item in results:
        compounded *= 1.0 + item.net_pct / 100.0
    nets = [item.net_pct for item in results]
    return {
        "windows": len(results),
        "positive_windows": sum(1 for value in nets if value > 0),
        "compounded_pct": round((compounded - 1.0) * 100.0, 2),
        "avg_window_pct": round(sum(nets) / len(nets), 3),
        "worst_window_pct": round(min(nets), 2),
        "best_window_pct": round(max(nets), 2),
        "trades": sum(item.trades for item in results),
    }


def aggregate_by_phase(results: Sequence[WindowResult]) -> Dict[str, Dict[str, Any]]:
    """Per-phase rollups plus an ``ALL`` bucket."""
    out: Dict[str, Dict[str, Any]] = {}
    for phase in ("bull", "bear", "sideways", "unknown"):
        subset = [r for r in results if r.phase == phase]
        if subset:
            out[phase] = aggregate(subset)
    out["ALL"] = aggregate(results)
    return out
