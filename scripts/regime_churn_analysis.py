#!/usr/bin/env python3
"""Measure regime-router churn: how often the classifier flips and how often
that tears down / rebuilds the active strategy before it can act.

Answers the operational question: "is the auto-regime layer flip-flopping so
much that the strategy never gets to tick a real entry, or is it stable?"

It drives the REAL production code paths:
  - xauby.regime.classifier.classify_market          (per-candle regime)
  - xauby.engine.regime_debounce.RegimeDebouncer     (N-candle confirmation)
  - xauby.engine.regime_router.RegimeRouter          (strategy mapping + NO_TRADE)

Data source (in priority order):
  1. --csv <file>   : real OHLCV klines (columns: open,high,low,close,volume;
                      optional timestamp). Use this with data from
                      scripts/fetch_global_klines.py for a production-grade read.
  2. synthetic      : deterministic multi-regime 1h series (seeded), used when no
                      CSV is supplied so the harness runs offline.

The synthetic path is a stress fixture, not market truth — it deliberately
includes trending, choppy, and transition episodes to exercise the router. Treat
its absolute numbers as illustrative and rerun with --csv for real figures.
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# Importing xauby.engine.* normally executes xauby/engine/__init__.py, which
# eagerly pulls the full LiteTradingEngine (pandas, dotenv, exchange clients).
# This harness only needs the pure regime logic, so register a lightweight stub
# package that exposes the submodules via __path__ without the heavy __init__.
import types  # noqa: E402

_eng = types.ModuleType("xauby.engine")
_eng.__path__ = [os.path.join(_ROOT, "xauby", "engine")]
sys.modules.setdefault("xauby.engine", _eng)

# RegimeRouter.validate_mapping() imports the real strategy registry, which pulls
# pandas via the plugin base. Stub it with the known plugin ids so the router can
# validate its mapping offline without loading every strategy module.
_KNOWN_STRATEGIES = {
    "donchian_trend", "cdc_action_zone", "supertrend_ema200",
    "bbrsi_mean_reversion", "bbkc_squeeze",
}
_strat_pkg = types.ModuleType("xauby.strategies")
_strat_pkg.__path__ = [os.path.join(_ROOT, "xauby", "strategies")]
_reg_mod = types.ModuleType("xauby.strategies.registry")
_reg_mod.available_strategies = lambda: set(_KNOWN_STRATEGIES)  # type: ignore[attr-defined]
sys.modules.setdefault("xauby.strategies", _strat_pkg)
sys.modules.setdefault("xauby.strategies.registry", _reg_mod)

from xauby.regime.classifier import classify_market  # noqa: E402
from xauby.engine.regime_debounce import RegimeDebouncer  # noqa: E402
from xauby.engine.regime_router import RegimeRouter  # noqa: E402

WINDOW = 250  # candles fed to the classifier each tick (matches engine limit)


# --- a minimal stand-in for SymbolContext (only the fields the router touches) -
@dataclass
class _SC:
    strategy_name: str = ""
    confirmed_regime: str = ""
    pending_regime: str = ""
    regime_debounce_count: int = 0
    no_trade_state: str = "ACTIVE"
    handoff_from_strategy: str = ""
    handoff_to_strategy: str = ""
    trailing_atr_mult: float = 2.0
    no_trade_candle_count: int = 0
    no_trade_recovery_count: int = 0


@dataclass
class _Spec:
    symbol: str
    strategy_name: str


@dataclass
class Metrics:
    candles: int = 0
    raw_counts: Dict[str, int] = field(default_factory=dict)
    raw_flips: int = 0          # classifier label != previous candle's label
    confirmed_switches: int = 0  # debounce-confirmed regime change
    strategy_reloads: int = 0    # router asked to swap the active strategy
    no_trade_candles: int = 0    # candles where new entries are blocked
    strat_target_counts: Dict[str, int] = field(default_factory=dict)


def synthetic_series(n: int, seed: int) -> List[Dict[str, float]]:
    """Deterministic 1h-like OHLCV with embedded regime episodes."""
    rng = random.Random(seed)
    price = 30000.0
    out: List[Dict[str, float]] = []
    # episode = (drift_per_bar, vol, n_bars) ; mix of trend / chop / shock
    episodes = [
        (0.0008, 0.004, 180),   # steady bull
        (-0.0002, 0.012, 90),   # choppy high-vol (transition magnet)
        (0.0, 0.003, 120),      # quiet range
        (-0.0010, 0.010, 80),   # bear
        (0.0015, 0.014, 60),    # volatility expansion / breakout
        (0.0001, 0.006, 150),   # weak drift, mixed
        (-0.0025, 0.020, 40),   # panic-ish flush
        (0.0006, 0.005, 200),   # recovery bull
    ]
    plan: List[tuple] = []
    while len(plan) < n:
        plan.extend(episodes)
    for drift, vol, _bars_ignored in _expand(plan, n):
        ret = drift + rng.gauss(0, vol)
        new = max(1.0, price * (1.0 + ret))
        hi = max(price, new) * (1.0 + abs(rng.gauss(0, vol)) * 0.5)
        lo = min(price, new) * (1.0 - abs(rng.gauss(0, vol)) * 0.5)
        volume = max(1.0, rng.gauss(1000, 300) * (1.0 + abs(ret) * 20))
        out.append({"open": price, "high": hi, "low": lo, "close": new, "volume": volume})
        price = new
    return out


def _expand(plan: List[tuple], n: int):
    flat: List[tuple] = []
    for drift, vol, bars in plan:
        for _ in range(bars):
            flat.append((drift, vol, 1))
            if len(flat) >= n:
                return flat
    return flat


def load_csv(path: str) -> List[Dict[str, float]]:
    import csv
    rows: List[Dict[str, float]] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r.get("volume", 0) or 0),
            })
    return rows


def run(
    series: List[Dict[str, float]],
    *,
    mapping: Dict[str, Optional[str]],
    debounce_candles: int,
    instant_switch_when_flat: bool,
    initial_strategy: str,
    timeframe: str,
    confidence_filter: bool = False,
    confidence_threshold: float = 0.7,
    gated: bool = True,
    ticks_per_candle: int = 60,
) -> Metrics:
    """Replay the regime router over the series.

    ``gated=True`` advances the router once per closed candle (the fixed
    behaviour). ``gated=False`` advances it ``ticks_per_candle`` times per candle
    with the same regime label, reproducing the pre-fix loop that ran the router
    every tick — which is how ``debounce_candles`` collapsed into ~N ticks.

    The position is held flat throughout, so every confirmed switch reloads the
    strategy: this is exactly the "strategy never gets to enter" worst case.
    """
    cfg = {
        "regime_router": {
            "mapping": mapping,
            "debounce_candles": debounce_candles,
            "recovery_candles": 3,
            "force_close_candles": 6,
            "instant_switch_when_flat": instant_switch_when_flat,
            "confidence_threshold": confidence_threshold,
        },
        "architecture": {"regime_confidence_filter": confidence_filter},
    }
    router = RegimeRouter(
        cfg,
        debouncer=RegimeDebouncer(threshold=debounce_candles),
        history_count_fn=lambda: 9999,  # past the 30-sample warmup so the filter bites
    )
    sc = _SC(strategy_name=initial_strategy, confirmed_regime="")
    spec = _Spec(symbol="BTCUSDT", strategy_name=initial_strategy)
    m = Metrics()
    prev_label = ""
    n_per_candle = 1 if gated else max(1, ticks_per_candle)

    for i in range(WINDOW, len(series)):
        window = series[i - WINDOW : i]
        reg = classify_market(window, indicators={}, macro_state={}, timeframe=timeframe)
        label = reg.regime
        m.candles += 1
        m.raw_counts[label] = m.raw_counts.get(label, 0) + 1
        if prev_label and label != prev_label:
            m.raw_flips += 1
        prev_label = label

        # The classifier output is identical for every tick inside one candle
        # (it reads closed candles), so we replay the same regime n_per_candle
        # times — matching how the live loop called the router each tick.
        for _ in range(n_per_candle):
            route = router.evaluate("BTCUSDT", reg, sc, spec, has_open_position=False)
            if route.old_regime and route.old_regime != route.regime and route.log_history:
                m.confirmed_switches += 1
            if route.strategy_changed and route.strategy_name:
                m.strategy_reloads += 1
        if sc.no_trade_state in ("NO_TRADE", "NO_TRADE_PENDING", "HANDOFF"):
            m.no_trade_candles += 1
        active = sc.strategy_name or initial_strategy
        m.strat_target_counts[active] = m.strat_target_counts.get(active, 0) + 1

    return m


# --- Mapping variants: progressively fewer distinct destination strategies ----
# Bear/panic regimes always map to None (NO_TRADE) — never a tradeable target.
_BEARS = {"PANIC_SELL": None, "BEAR_BREAKDOWN": None, "BEAR_TREND_STRONG": None}

MAPPING_VARIANTS: Dict[str, Dict[str, Optional[str]]] = {
    # V0: shipped — 4 distinct tradeable strategies
    "V0_current_4strat": {
        "BULL_BREAKOUT": "donchian_trend",
        "BULL_TREND_STRONG": "donchian_trend",
        "BULL_TREND_WEAK": "donchian_trend",
        "LOW_VOL_ACCUMULATION": "bbkc_squeeze",
        "LOW_VOL_RANGE": "bbkc_squeeze",
        "VOLATILITY_EXPANSION": "supertrend_ema200",
        "SIDEWAYS_CHOP": "bbrsi_mean_reversion",
        "BEAR_TREND_WEAK": "donchian_trend",
        **_BEARS,
    },
    # V3: 3 strategies — fold low-vol (bbkc_squeeze) into the mean-reversion bucket
    "V3_three_strat": {
        "BULL_BREAKOUT": "donchian_trend",
        "BULL_TREND_STRONG": "donchian_trend",
        "BULL_TREND_WEAK": "donchian_trend",
        "LOW_VOL_ACCUMULATION": "bbrsi_mean_reversion",
        "LOW_VOL_RANGE": "bbrsi_mean_reversion",
        "VOLATILITY_EXPANSION": "supertrend_ema200",
        "SIDEWAYS_CHOP": "bbrsi_mean_reversion",
        "BEAR_TREND_WEAK": "donchian_trend",
        **_BEARS,
    },
    # V1: 2 strategies — trend/breakout (donchian) vs range/chop (mean-reversion)
    "V1_two_strat": {
        "BULL_BREAKOUT": "donchian_trend",
        "BULL_TREND_STRONG": "donchian_trend",
        "BULL_TREND_WEAK": "donchian_trend",
        "VOLATILITY_EXPANSION": "donchian_trend",
        "BEAR_TREND_WEAK": "donchian_trend",
        "LOW_VOL_ACCUMULATION": "bbrsi_mean_reversion",
        "LOW_VOL_RANGE": "bbrsi_mean_reversion",
        "SIDEWAYS_CHOP": "bbrsi_mean_reversion",
        **_BEARS,
    },
    # V2: 1 strategy — pure regime on/off gate (trend follower, no chop strategy)
    "V2_trend_only": {
        "BULL_BREAKOUT": "donchian_trend",
        "BULL_TREND_STRONG": "donchian_trend",
        "BULL_TREND_WEAK": "donchian_trend",
        "VOLATILITY_EXPANSION": "donchian_trend",
        "BEAR_TREND_WEAK": "donchian_trend",
        "LOW_VOL_ACCUMULATION": "donchian_trend",
        "LOW_VOL_RANGE": "donchian_trend",
        "SIDEWAYS_CHOP": "donchian_trend",
        **_BEARS,
    },
}


def distinct_strategies(mapping: Dict[str, Optional[str]]) -> int:
    return len({v for v in mapping.values() if v})


def diversity(m: Metrics, floor_pct: float = 1.0) -> int:
    """How many strategies were active for at least floor_pct of candles."""
    return sum(1 for v in m.strat_target_counts.values()
               if m.candles and 100.0 * v / m.candles >= floor_pct)


def pct(x: int, total: int) -> str:
    return f"{(100.0 * x / total):.1f}%" if total else "0.0%"


def report(label: str, m: Metrics) -> None:
    print(f"\n=== {label} ===")
    print(f"candles classified      : {m.candles}")
    print(f"raw per-candle flips    : {m.raw_flips}  ({pct(m.raw_flips, m.candles)} of candles)")
    print(f"confirmed regime switches: {m.confirmed_switches}")
    print(f"strategy reloads (flat)  : {m.strategy_reloads}")
    print(f"NO_TRADE candles         : {m.no_trade_candles}  ({pct(m.no_trade_candles, m.candles)})")
    avg_hold = (m.candles / m.strategy_reloads) if m.strategy_reloads else float("inf")
    print(f"avg candles between reloads: {avg_hold:.1f}")
    top_raw = sorted(m.raw_counts.items(), key=lambda kv: -kv[1])[:6]
    print("regime distribution      : " + ", ".join(f"{k}={pct(v, m.candles)}" for k, v in top_raw))
    top_strat = sorted(m.strat_target_counts.items(), key=lambda kv: -kv[1])
    print("time per active strategy : " + ", ".join(f"{k}={pct(v, m.candles)}" for k, v in top_strat))


def run_matrix(args) -> int:
    """Compare destination-strategy reduction x confidence threshold.

    Rows = mapping variant (fewer distinct strategies going down).
    Columns = confidence threshold (off, then a gated sweep).
    Cells = strategy reloads | confirmed switches | NO_TRADE% | #strategies used.
    All runs use the POST-FIX per-candle gate, debounce=3, flat worst case.
    Synthetic runs average over --seeds; a CSV runs once.
    """
    if args.csv:
        seriess = [load_csv(args.csv)]
        seed_note = "csv"
    else:
        seeds = [int(s) for s in args.seeds.split(",") if s.strip()] or [args.seed]
        seriess = [synthetic_series(args.bars, s) for s in seeds]
        seed_note = "seeds " + ",".join(str(s) for s in seeds)

    thresholds = [("off", False, 0.0), ("0.60", True, 0.60), ("0.65", True, 0.65),
                  ("0.70", True, 0.70), ("0.75", True, 0.75)]

    def avg_run(mapping, conf_filter, thr):
        agg = {"reloads": 0.0, "switches": 0.0, "no_trade": 0.0, "div": 0.0, "cand": 0.0}
        for series in seriess:
            m = run(series, mapping=mapping, debounce_candles=3,
                    instant_switch_when_flat=False, initial_strategy=args.initial_strategy,
                    timeframe=args.timeframe, confidence_filter=conf_filter,
                    confidence_threshold=thr, gated=True)
            agg["reloads"] += m.strategy_reloads
            agg["switches"] += m.confirmed_switches
            agg["no_trade"] += 100.0 * m.no_trade_candles / m.candles if m.candles else 0
            agg["div"] += diversity(m)
            agg["cand"] += m.candles
        n = len(seriess)
        return {k: v / n for k, v in agg.items()}

    print(f"\nMATRIX — post-fix gate, debounce=3, flat worst case ({seed_note})")
    print("cell = reloads / switches / NO_TRADE% / #strat-used   (lower reloads = less churn)\n")
    header = f"{'mapping (distinct strat)':<28}" + "".join(f"{('thr='+t[0]):>20}" for t in thresholds)
    print(header)
    print("-" * len(header))
    for name, mapping in MAPPING_VARIANTS.items():
        label = f"{name} ({distinct_strategies(mapping)})"
        row = f"{label:<28}"
        for _tname, cf, thr in thresholds:
            a = avg_run(mapping, cf, thr)
            cell = f"{a['reloads']:.0f}/{a['switches']:.0f}/{a['no_trade']:.0f}%/{a['div']:.0f}"
            row += f"{cell:>20}"
        print(row)
    print("\nReading: 'off' column isolates the mapping-reduction effect; thr columns add")
    print("the confidence filter. #strat-used = strategies active >=1% of candles.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="real OHLCV CSV (open,high,low,close,volume)")
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--initial-strategy", default="donchian_trend")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--bars", type=int, default=1300, help="synthetic bar count")
    ap.add_argument("--ticks-per-candle", type=int, default=60,
                    help="loop ticks per candle for the PRE-FIX scenario "
                         "(interval_seconds=60 on a 1h candle => 60)")
    ap.add_argument("--matrix", action="store_true",
                    help="compare mapping variants x confidence thresholds "
                         "(answers: reduce destination strategies + tune threshold)")
    ap.add_argument("--seeds", default="",
                    help="comma seeds to average for --matrix (e.g. 7,11,23)")
    args = ap.parse_args()

    if args.csv:
        series = load_csv(args.csv)
        src = f"CSV {args.csv} ({len(series)} bars)"
    else:
        series = synthetic_series(args.bars, args.seed)
        src = f"synthetic seed={args.seed} ({len(series)} bars)"
    print(f"Data source: {src}  timeframe={args.timeframe}")
    print(f"Window per classify: {WINDOW} candles (matches engine)")

    if args.matrix:
        return run_matrix(args)

    # Production mapping (from bot_config.yaml BTC route)
    mapping: Dict[str, Optional[str]] = {
        "BULL_BREAKOUT": "donchian_trend",
        "BULL_TREND_STRONG": "donchian_trend",
        "BULL_TREND_WEAK": "donchian_trend",
        "LOW_VOL_ACCUMULATION": "bbkc_squeeze",
        "LOW_VOL_RANGE": "bbkc_squeeze",
        "VOLATILITY_EXPANSION": "supertrend_ema200",
        "SIDEWAYS_CHOP": "bbrsi_mean_reversion",
        "BEAR_TREND_WEAK": "donchian_trend",
        "PANIC_SELL": None,
        "BEAR_BREAKDOWN": None,
        "BEAR_TREND_STRONG": None,
    }

    common = dict(
        mapping=mapping, debounce_candles=3, instant_switch_when_flat=False,
        initial_strategy=args.initial_strategy, timeframe=args.timeframe,
    )

    # PRE-FIX: router advanced every tick → debounce counted ticks, not candles.
    report(
        f"PRE-FIX (router every tick, {args.ticks_per_candle} ticks/candle, conf_filter=off)",
        run(series, gated=False, ticks_per_candle=args.ticks_per_candle,
            confidence_filter=False, **common),
    )
    # POST-FIX step 1: gate routing to once per closed candle (debounce=real 3 candles).
    report(
        "POST-FIX A (per-candle gate, conf_filter=off)",
        run(series, gated=True, confidence_filter=False, **common),
    )
    # POST-FIX step 2: also enable the confidence filter (shipped config).
    report(
        "POST-FIX B (per-candle gate + conf_filter=on)  <-- shipped",
        run(series, gated=True, confidence_filter=True, **common),
    )
    # Debounce sensitivity (post-fix, gated) for reference.
    for d in (2, 5):
        report(
            f"post-fix gated, debounce={d}, conf_filter=on",
            run(series, gated=True, confidence_filter=True,
                mapping=mapping, debounce_candles=d, instant_switch_when_flat=False,
                initial_strategy=args.initial_strategy, timeframe=args.timeframe),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
