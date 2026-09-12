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

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

# Keys that move together. A variant touching one member must set them all,
# because the base config supplies whatever is left — which is exactly how six
# distinct D1 variants became two.
CONTROL_GROUPS: dict[str, tuple[str, ...]] = {
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


class TemporalLeakageError(ValueError):
    """A temporal validation plan can expose holdout information to selection."""


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
    stats: dict[str, Any] = field(default_factory=dict)
    phase: str = "unknown"

    @property
    def net_pct(self) -> float:
        return float(self.stats.get("net_profit_pct", 0.0) or 0.0)

    @property
    def trades(self) -> int:
        return int(self.stats.get("total_trades", 0) or 0)


@dataclass(frozen=True)
class BarSpan:
    """A half-open positional range ``[start, end)`` in one locked data set."""

    label: str
    start: int
    end: int

    def __post_init__(self) -> None:
        if isinstance(self.start, bool) or isinstance(self.end, bool):
            raise TemporalLeakageError("bar span positions must be integers")
        if not isinstance(self.start, int) or not isinstance(self.end, int):
            raise TemporalLeakageError("bar span positions must be integers")
        if self.start < 0 or self.end < self.start:
            raise TemporalLeakageError(
                f"invalid bar span {self.label!r}: [{self.start}, {self.end})"
            )

    @property
    def bars(self) -> int:
        return self.end - self.start

    def overlaps(self, other: BarSpan) -> bool:
        return max(self.start, other.start) < min(self.end, other.end)

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "start": self.start, "end": self.end, "bars": self.bars}


@dataclass(frozen=True)
class PurgedSplit:
    """Past-only training followed by embargo, purge, and untouched evaluation."""

    label: str
    train: BarSpan
    embargo: BarSpan
    purge: BarSpan
    evaluation: BarSpan

    def validate(self, *, embargo_bars: int, purge_bars: int) -> None:
        spans = (self.train, self.embargo, self.purge, self.evaluation)
        if not (
            self.train.start == 0
            and self.train.end == self.embargo.start
            and self.embargo.end == self.purge.start
            and self.purge.end == self.evaluation.start
        ):
            raise TemporalLeakageError(
                f"{self.label}: train/embargo/purge/evaluation boundaries are not contiguous"
            )
        if self.embargo.bars != embargo_bars:
            raise TemporalLeakageError(f"{self.label}: embargo length changed")
        if self.purge.bars != purge_bars:
            raise TemporalLeakageError(f"{self.label}: purge length changed")
        if self.train.bars <= 0 or self.evaluation.bars <= 0:
            raise TemporalLeakageError(f"{self.label}: train and evaluation must be non-empty")
        for left, right in zip(spans, spans[1:], strict=False):
            if left.overlaps(right):
                raise TemporalLeakageError(f"{self.label}: temporal ranges overlap")

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "train": self.train.to_dict(),
            "embargo": self.embargo.to_dict(),
            "purge": self.purge.to_dict(),
            "evaluation": self.evaluation.to_dict(),
        }


@dataclass(frozen=True)
class NestedPurgedFold:
    index: int
    outer: PurgedSplit
    inner: tuple[PurgedSplit, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "outer": self.outer.to_dict(),
            "inner": [split.to_dict() for split in self.inner],
        }


@dataclass(frozen=True)
class NestedPurgedPlan:
    """Fully materialized temporal boundaries for reproducible nested WFA."""

    data_rows: int
    timeline_sha256: str
    outer_folds: int
    inner_folds: int
    outer_test_bars: int
    outer_step_bars: int
    inner_validation_bars: int
    min_train_bars: int
    min_inner_train_bars: int
    purge_bars: int
    embargo_bars: int
    warmup_bars: int
    folds: tuple[NestedPurgedFold, ...]

    def validate(self) -> None:
        for name in (
            "data_rows",
            "outer_folds",
            "inner_folds",
            "outer_test_bars",
            "outer_step_bars",
            "inner_validation_bars",
            "min_train_bars",
            "min_inner_train_bars",
        ):
            _positive_int(name, getattr(self, name))
        _positive_int("purge_bars", self.purge_bars, allow_zero=True)
        _positive_int("embargo_bars", self.embargo_bars, allow_zero=True)
        _positive_int("warmup_bars", self.warmup_bars, allow_zero=True)
        if self.outer_step_bars < self.outer_test_bars:
            raise TemporalLeakageError("outer test folds cannot overlap")
        if len(self.timeline_sha256) != 64:
            raise TemporalLeakageError("timeline_sha256 must be a SHA-256 digest")
        if len(self.folds) != self.outer_folds:
            raise TemporalLeakageError("outer fold count does not match the locked plan")
        expected_first_test = self.data_rows - (
            self.outer_test_bars + (self.outer_folds - 1) * self.outer_step_bars
        )
        previous_test: BarSpan | None = None
        for expected_index, fold in enumerate(self.folds):
            if fold.index != expected_index:
                raise TemporalLeakageError("outer fold indices are not contiguous")
            fold.outer.validate(
                embargo_bars=self.embargo_bars,
                purge_bars=self.purge_bars,
            )
            if fold.outer.train.bars < self.min_train_bars:
                raise TemporalLeakageError(f"outer fold {fold.index} has insufficient training")
            if fold.outer.evaluation.end > self.data_rows:
                raise TemporalLeakageError(f"outer fold {fold.index} exceeds locked data")
            if fold.outer.evaluation.bars != self.outer_test_bars:
                raise TemporalLeakageError(f"outer fold {fold.index} test length changed")
            expected_test_start = expected_first_test + fold.index * self.outer_step_bars
            if fold.outer.evaluation.start != expected_test_start:
                raise TemporalLeakageError(
                    f"outer fold {fold.index} is not on the locked walk-forward schedule"
                )
            if previous_test is not None and previous_test.end > fold.outer.evaluation.start:
                raise TemporalLeakageError("outer test folds overlap")
            previous_test = fold.outer.evaluation

            if len(fold.inner) != self.inner_folds:
                raise TemporalLeakageError(
                    f"outer fold {fold.index} inner fold count changed"
                )
            expected_first_validation = (
                fold.outer.train.end - self.inner_folds * self.inner_validation_bars
            )
            previous_validation: BarSpan | None = None
            for inner_index, inner in enumerate(fold.inner):
                inner.validate(
                    embargo_bars=self.embargo_bars,
                    purge_bars=self.purge_bars,
                )
                if inner.train.bars < self.min_inner_train_bars:
                    raise TemporalLeakageError(f"{inner.label}: insufficient inner training")
                if inner.evaluation.bars != self.inner_validation_bars:
                    raise TemporalLeakageError(f"{inner.label}: validation length changed")
                expected_validation_start = (
                    expected_first_validation + inner_index * self.inner_validation_bars
                )
                if inner.evaluation.start != expected_validation_start:
                    raise TemporalLeakageError(
                        f"{inner.label}: validation is not on the locked inner schedule"
                    )
                if inner.evaluation.end > fold.outer.train.end:
                    raise TemporalLeakageError(
                        f"{inner.label}: inner validation reaches the outer holdout boundary"
                    )
                if inner.evaluation.overlaps(fold.outer.evaluation):
                    raise TemporalLeakageError(
                        f"{inner.label}: inner validation overlaps outer holdout"
                    )
                if previous_validation is not None and previous_validation.end > inner.evaluation.start:
                    raise TemporalLeakageError(
                        f"{inner.label}: inner validation folds overlap"
                    )
                previous_validation = inner.evaluation

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": "nested_purged_walk_forward",
            "data_rows": self.data_rows,
            "timeline_sha256": self.timeline_sha256,
            "outer_folds": self.outer_folds,
            "inner_folds": self.inner_folds,
            "outer_test_bars": self.outer_test_bars,
            "outer_step_bars": self.outer_step_bars,
            "inner_validation_bars": self.inner_validation_bars,
            "min_train_bars": self.min_train_bars,
            "min_inner_train_bars": self.min_inner_train_bars,
            "purge_bars": self.purge_bars,
            "embargo_bars": self.embargo_bars,
            "warmup_bars": self.warmup_bars,
            "folds": [fold.to_dict() for fold in self.folds],
        }

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class NestedFoldResult:
    fold: NestedPurgedFold
    selected_candidate: str
    inner_scores: dict[str, float]
    inner_results: dict[str, list[WindowResult]]
    outer_result: WindowResult


def _timestamp_column(df: pd.DataFrame) -> str:
    for name in ("open_time", "timestamp"):
        if name in df.columns:
            return name
    raise KeyError("frame has neither 'open_time' nor 'timestamp'")


def _as_ms(df: pd.DataFrame, column: str) -> pd.Series:
    values = df[column].astype("int64")
    # `timestamp` is seconds in this codebase; `open_time` is milliseconds.
    return values * 1000 if column == "timestamp" else values


def _positive_int(name: str, value: int, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TemporalLeakageError(f"{name} must be an integer")
    minimum = 0 if allow_zero else 1
    if value < minimum:
        op = ">= 0" if allow_zero else "> 0"
        raise TemporalLeakageError(f"{name} must be {op}")
    return value


def _validate_temporal_frame(df: pd.DataFrame) -> None:
    if df.empty:
        raise TemporalLeakageError("nested walk-forward requires a non-empty frame")
    column = _timestamp_column(df)
    ms = _as_ms(df, column).reset_index(drop=True)
    if bool(ms.duplicated().any()):
        raise TemporalLeakageError("frame timestamps must be unique")
    if not bool(ms.is_monotonic_increasing):
        raise TemporalLeakageError("frame timestamps must be strictly increasing")


def _timeline_sha256(df: pd.DataFrame) -> str:
    column = _timestamp_column(df)
    values = ",".join(str(int(value)) for value in _as_ms(df, column))
    return hashlib.sha256(values.encode("ascii")).hexdigest()


def _purged_split(
    label: str,
    *,
    evaluation_start: int,
    evaluation_end: int,
    embargo_bars: int,
    purge_bars: int,
) -> PurgedSplit:
    purge_start = evaluation_start - purge_bars
    embargo_start = purge_start - embargo_bars
    if embargo_start <= 0:
        raise TemporalLeakageError(f"{label}: not enough history before purge and embargo")
    split = PurgedSplit(
        label=label,
        train=BarSpan(f"{label}:train", 0, embargo_start),
        embargo=BarSpan(f"{label}:embargo", embargo_start, purge_start),
        purge=BarSpan(f"{label}:purge", purge_start, evaluation_start),
        evaluation=BarSpan(f"{label}:evaluation", evaluation_start, evaluation_end),
    )
    split.validate(embargo_bars=embargo_bars, purge_bars=purge_bars)
    return split


def nested_purged_plan(
    df: pd.DataFrame,
    *,
    outer_folds: int,
    inner_folds: int,
    outer_test_bars: int,
    inner_validation_bars: int,
    min_train_bars: int,
    min_inner_train_bars: int,
    purge_bars: int,
    embargo_bars: int,
    warmup_bars: int = DEFAULT_WARMUP_BARS,
    outer_step_bars: int | None = None,
) -> NestedPurgedPlan:
    """Build an expanding, past-only nested walk-forward plan.

    The most recent ``outer_folds`` holdouts are used.  Each outer test is
    preceded by two explicit non-trading regions: an embargo, then the purge
    interval nearest the holdout.  Inner validation folds are non-overlapping
    and end no later than the outer training boundary, so candidate selection
    cannot observe the outer test.

    ``purge_bars`` is the maximum label/holding horizon in bars.  Keeping it
    explicit and adjacent to the holdout makes overlapping outcomes impossible
    to hide inside a generic train/test ratio.
    """
    _validate_temporal_frame(df)
    outer_folds = _positive_int("outer_folds", outer_folds)
    inner_folds = _positive_int("inner_folds", inner_folds)
    outer_test_bars = _positive_int("outer_test_bars", outer_test_bars)
    inner_validation_bars = _positive_int(
        "inner_validation_bars", inner_validation_bars
    )
    min_train_bars = _positive_int("min_train_bars", min_train_bars)
    min_inner_train_bars = _positive_int(
        "min_inner_train_bars", min_inner_train_bars
    )
    purge_bars = _positive_int("purge_bars", purge_bars, allow_zero=True)
    embargo_bars = _positive_int("embargo_bars", embargo_bars, allow_zero=True)
    warmup_bars = _positive_int("warmup_bars", warmup_bars, allow_zero=True)
    step = outer_test_bars if outer_step_bars is None else _positive_int(
        "outer_step_bars", outer_step_bars
    )
    if step < outer_test_bars:
        raise TemporalLeakageError(
            "outer_step_bars must be >= outer_test_bars; headline test folds cannot overlap"
        )

    rows = len(df)
    evaluation_coverage = outer_test_bars + (outer_folds - 1) * step
    first_test_start = rows - evaluation_coverage
    first_train_end = first_test_start - purge_bars - embargo_bars
    if first_train_end < min_train_bars:
        required = min_train_bars + purge_bars + embargo_bars + evaluation_coverage
        raise TemporalLeakageError(
            f"insufficient rows for locked outer plan: have {rows}, require at least {required}"
        )

    folds: list[NestedPurgedFold] = []
    inner_coverage = inner_folds * inner_validation_bars
    for outer_index in range(outer_folds):
        test_start = first_test_start + outer_index * step
        test_end = test_start + outer_test_bars
        outer = _purged_split(
            f"outer-{outer_index + 1}",
            evaluation_start=test_start,
            evaluation_end=test_end,
            embargo_bars=embargo_bars,
            purge_bars=purge_bars,
        )
        if outer.train.bars < min_train_bars:
            raise TemporalLeakageError(
                f"outer-{outer_index + 1}: insufficient outer training history"
            )

        first_inner_start = outer.train.end - inner_coverage
        earliest_inner_train_end = first_inner_start - purge_bars - embargo_bars
        if earliest_inner_train_end < min_inner_train_bars:
            required_outer_train = (
                min_inner_train_bars
                + purge_bars
                + embargo_bars
                + inner_coverage
            )
            raise TemporalLeakageError(
                f"outer-{outer_index + 1}: inner plan requires at least "
                f"{required_outer_train} outer-training bars, has {outer.train.bars}"
            )

        inner: list[PurgedSplit] = []
        for inner_index in range(inner_folds):
            validation_start = first_inner_start + inner_index * inner_validation_bars
            validation_end = validation_start + inner_validation_bars
            inner.append(
                _purged_split(
                    f"outer-{outer_index + 1}:inner-{inner_index + 1}",
                    evaluation_start=validation_start,
                    evaluation_end=validation_end,
                    embargo_bars=embargo_bars,
                    purge_bars=purge_bars,
                )
            )
        folds.append(NestedPurgedFold(index=outer_index, outer=outer, inner=tuple(inner)))

    plan = NestedPurgedPlan(
        data_rows=rows,
        timeline_sha256=_timeline_sha256(df),
        outer_folds=outer_folds,
        inner_folds=inner_folds,
        outer_test_bars=outer_test_bars,
        outer_step_bars=step,
        inner_validation_bars=inner_validation_bars,
        min_train_bars=min_train_bars,
        min_inner_train_bars=min_inner_train_bars,
        purge_bars=purge_bars,
        embargo_bars=embargo_bars,
        warmup_bars=warmup_bars,
        folds=tuple(folds),
    )
    plan.validate()
    return plan


def nested_purged_plan_from_policy(
    df: pd.DataFrame,
    policy: Mapping[str, Any],
) -> NestedPurgedPlan:
    """Materialize the exact validation block frozen in Protocol v2."""
    if str(policy.get("method") or "") != "nested_purged_walk_forward":
        raise TemporalLeakageError(
            "validation policy method must be 'nested_purged_walk_forward'"
        )
    required = (
        "outer_folds",
        "inner_folds",
        "outer_test_bars",
        "outer_step_bars",
        "inner_validation_bars",
        "min_train_bars",
        "min_inner_train_bars",
        "purge_bars",
        "embargo_bars",
        "warmup_bars",
    )
    missing = [key for key in required if key not in policy]
    if missing:
        raise TemporalLeakageError(f"validation policy missing {missing}")
    return nested_purged_plan(df, **{key: policy[key] for key in required})


def month_windows(
    df: pd.DataFrame,
    *,
    complete_only: bool = True,
    first: str | None = None,
) -> list[Window]:
    """Calendar-month windows covering ``df``.

    ``complete_only`` drops a trailing partial month. Comparing a part-month
    against full ones in the same compounded total silently flatters whichever
    config happens to be ahead mid-month.
    """
    column = _timestamp_column(df)
    ms = _as_ms(df, column)
    stamps = pd.to_datetime(ms, unit="ms")
    out: list[Window] = []
    for (year, month), _group in df.groupby([stamps.dt.year, stamps.dt.month]):
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
) -> WindowSlice | None:
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
) -> dict[str, Any]:
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
    df_regime: pd.DataFrame | None = None,
    regime_timeframe: str | None = None,
) -> dict[str, Any]:
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


def slice_bar_span(
    df: pd.DataFrame,
    span: BarSpan,
    *,
    warmup_bars: int,
) -> WindowSlice:
    """Create a replay slice whose leading history is structurally non-trading."""
    _validate_temporal_frame(df)
    warmup_bars = _positive_int("warmup_bars", warmup_bars, allow_zero=True)
    if span.bars <= 0:
        raise TemporalLeakageError(f"{span.label}: evaluation span must be non-empty")
    if span.end > len(df):
        raise TemporalLeakageError(f"{span.label}: evaluation exceeds locked data")
    lo = max(0, span.start - warmup_bars)
    frame = df.iloc[lo:span.end].reset_index(drop=True)
    column = _timestamp_column(df)
    ms = _as_ms(df, column).reset_index(drop=True)
    start_ms = int(ms.iloc[span.start])
    end_ms = int(ms.iloc[span.end]) if span.end < len(ms) else int(ms.iloc[-1]) + 1
    return WindowSlice(
        window=Window(span.label, start_ms, end_ms),
        frame=frame,
        skip_bars=span.start - lo,
    )


def nested_purged_walk_forward(
    df: pd.DataFrame,
    *,
    plan: NestedPurgedPlan,
    candidates: Mapping[str, Mapping[str, Any]],
    score_candidate: Callable[[Sequence[WindowResult]], float],
    strategy_name: str,
    engine_config: Mapping[str, Any],
    symbol: str,
    primary_timeframe: str = "4h",
    df_regime: pd.DataFrame | None = None,
    regime_timeframe: str | None = None,
) -> list[NestedFoldResult]:
    """Select on inner folds, then evaluate the winner once on each outer fold.

    The selector receives only inner-fold results.  Outer results do not exist
    until after the selected candidate is frozen for that fold, preventing the
    common accidental pattern of ranking on the same holdout later reported as
    out-of-sample performance.
    """
    _validate_temporal_frame(df)
    plan.validate()
    if len(df) != plan.data_rows:
        raise TemporalLeakageError(
            f"locked plan expects {plan.data_rows} rows, received {len(df)}"
        )
    if _timeline_sha256(df) != plan.timeline_sha256:
        raise TemporalLeakageError("frame timeline does not match the locked plan")
    if not candidates:
        raise TemporalLeakageError("nested walk-forward requires at least one candidate")
    normalized: dict[str, dict[str, Any]] = {}
    for candidate_id, config in candidates.items():
        key = str(candidate_id or "").strip()
        if not key:
            raise TemporalLeakageError("candidate ids must be non-empty")
        if key in normalized:
            raise TemporalLeakageError(f"duplicate candidate id {key!r}")
        if not isinstance(config, Mapping):
            raise TemporalLeakageError(f"candidate {key!r} config must be a mapping")
        normalized[key] = deepcopy(dict(config))

    results: list[NestedFoldResult] = []
    for fold in plan.folds:
        inner_results: dict[str, list[WindowResult]] = {}
        inner_scores: dict[str, float] = {}
        for candidate_id in sorted(normalized):
            candidate_results: list[WindowResult] = []
            for split in fold.inner:
                sliced = slice_bar_span(
                    df,
                    split.evaluation,
                    warmup_bars=plan.warmup_bars,
                )
                stats = run_slice(
                    sliced,
                    strategy_name=strategy_name,
                    strategy_config=normalized[candidate_id],
                    engine_config=engine_config,
                    symbol=symbol,
                    primary_timeframe=primary_timeframe,
                    df_regime=df_regime,
                    regime_timeframe=regime_timeframe,
                )
                candidate_results.append(WindowResult(window=sliced.window, stats=stats))
            score = float(score_candidate(tuple(candidate_results)))
            if not pd.notna(score) or score in (float("inf"), float("-inf")):
                raise TemporalLeakageError(
                    f"candidate {candidate_id!r} produced a non-finite selection score"
                )
            inner_results[candidate_id] = candidate_results
            inner_scores[candidate_id] = score

        # Candidate id is a deterministic tie-breaker; dictionary insertion
        # order cannot change a certificate winner.
        selected = min(inner_scores, key=lambda key: (-inner_scores[key], key))
        outer_slice = slice_bar_span(
            df,
            fold.outer.evaluation,
            warmup_bars=plan.warmup_bars,
        )
        outer_stats = run_slice(
            outer_slice,
            strategy_name=strategy_name,
            strategy_config=normalized[selected],
            engine_config=engine_config,
            symbol=symbol,
            primary_timeframe=primary_timeframe,
            df_regime=df_regime,
            regime_timeframe=regime_timeframe,
        )
        results.append(
            NestedFoldResult(
                fold=fold,
                selected_candidate=selected,
                inner_scores=inner_scores,
                inner_results=inner_results,
                outer_result=WindowResult(window=outer_slice.window, stats=outer_stats),
            )
        )
    return results


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
    windows: Iterable[Window] | None = None,
    warmup_bars: int = DEFAULT_WARMUP_BARS,
    primary_timeframe: str = "4h",
    df_regime: pd.DataFrame | None = None,
    regime_timeframe: str | None = None,
    df_daily_for_phase: pd.DataFrame | None = None,
) -> list[WindowResult]:
    """Replay a frozen config across windows. No per-window re-optimization."""
    selected = list(windows) if windows is not None else month_windows(df)
    results: list[WindowResult] = []
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


def aggregate(results: Sequence[WindowResult]) -> dict[str, Any]:
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


def aggregate_by_phase(results: Sequence[WindowResult]) -> dict[str, dict[str, Any]]:
    """Per-phase rollups plus an ``ALL`` bucket."""
    out: dict[str, dict[str, Any]] = {}
    for phase in ("bull", "bear", "sideways", "unknown"):
        subset = [r for r in results if r.phase == phase]
        if subset:
            out[phase] = aggregate(subset)
    out["ALL"] = aggregate(results)
    return out
