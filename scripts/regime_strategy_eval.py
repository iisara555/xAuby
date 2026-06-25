"""Per-regime strategy evaluation harness (data-driven strategy selection).

Labels every bar with the live regime classifier (rolling 250-bar window, no
lookahead), replays strategies bar-by-bar with the production ReplayEngine
while gating BUY entries to the bars whose regime belongs to the strategy's
family (mirrors RegimeRouter behaviour), and reports PF / win rate / trades /
max DD per (family x strategy x split).

Splits: train = oldest 70%%, test = newest 30%% (with indicator warmup
prepended and pre-test trades filtered out), recent = last ~6 months.

Usage:
    python scripts/regime_strategy_eval.py                       # full verdict table
    python scripts/regime_strategy_eval.py --grid                # tune candidates on train only
    python scripts/regime_strategy_eval.py --families trend,chop --strategies cdc_action_zone,donchian_trend
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Set

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from xauby.backtest.constants import DEFAULT_FEE_PCT, DEFAULT_INITIAL_BALANCE, DEFAULT_RISK_PCT
from xauby.observability.replay import PositionSimulator, ReplayEngine
from xauby.regime.classifier import classify_market
from xauby.runtime.trading_config import split_legacy_runtime_config
from xauby.strategies import load_strategy
from xauby.strategies.sandbox import StrategyRunner
from xauby.strategies.signal import hold

LABEL_WINDOW = 250  # mirrors engine loop: get_candles(limit=250) -> classify_market
WARMUP_BARS = 420   # indicator warmup prepended to test/recent slices
RECENT_BARS_BY_TF = {"1h": 4420, "4h": 1105, "1d": 184}  # ~6 months

FAMILIES: Dict[str, Dict[str, Any]] = {
    "trend": {
        "labels": {"BULL_BREAKOUT", "BULL_TREND_STRONG", "BULL_TREND_WEAK"},
        "incumbent": "cdc_action_zone",
    },
    "lowvol": {
        "labels": {"LOW_VOL_ACCUMULATION", "LOW_VOL_RANGE"},
        "incumbent": "bbkc_squeeze",
    },
    "chop": {
        "labels": {"SIDEWAYS_CHOP", "BEAR_TREND_WEAK"},
        "incumbent": "bbrsi_mean_reversion",
    },
    "vol_expand": {
        "labels": {"VOLATILITY_EXPANSION"},
        "incumbent": "supertrend_ema200",
    },
}
CANDIDATES = ("donchian_trend", "rsi2_meanrev", "vol_breakout")

# Short-side research families (no production mapping exists for these — the
# bear labels are mapped to null, so the baseline is "no trade"). bear_weak
# carries the current long incumbent for a same-labels comparison.
SHORT_FAMILIES: Dict[str, Dict[str, Any]] = {
    "bear": {
        "labels": {"BEAR_BREAKDOWN", "BEAR_TREND_STRONG", "PANIC_SELL"},
        "incumbent": None,
    },
    "bear_weak": {
        "labels": {"BEAR_TREND_WEAK"},
        "incumbent": "bbrsi_mean_reversion",  # long, measured on same labels
    },
}
SHORT_CANDIDATES = ("donchian_short", "supertrend_short", "rsi2_short")


def load_bot_config(path: str = "bot_config.yaml") -> Dict[str, Any]:
    import yaml

    with open(os.path.join(ROOT, path), "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_full_df(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    if "timestamp" not in df.columns:
        df["timestamp"] = df["open_time"] // 1000
    df["timestamp"] = df["timestamp"].astype("int64")
    return df.reset_index(drop=True)


def label_regimes(
    df: pd.DataFrame,
    cache_path: str,
    timeframe: str = "1h",
    relabel: bool = False,
) -> Dict[int, str]:
    """Label each bar's regime using data up to that bar only (no lookahead)."""
    labels: Dict[int, str] = {}
    if not relabel and os.path.exists(cache_path):
        cached = pd.read_csv(cache_path)
        labels = dict(zip(cached["timestamp"].astype("int64"), cached["regime"].astype(str)))

    records = df[["open", "high", "low", "close", "volume"]].to_dict("records")
    ts_arr = df["timestamp"].tolist()
    missing = [i for i in range(LABEL_WINDOW - 1, len(df)) if ts_arr[i] not in labels]
    if missing:
        print(f"Labeling {len(missing)} bars with classify_market (window={LABEL_WINDOW})...")
        t0 = time.perf_counter()
        for n, i in enumerate(missing):
            reg = classify_market(
                records[i - LABEL_WINDOW + 1 : i + 1],
                indicators=None,
                macro_state={},
                timeframe=timeframe,
            )
            labels[ts_arr[i]] = reg.regime
            if (n + 1) % 5000 == 0:
                print(f"  {n + 1}/{len(missing)} ({time.perf_counter() - t0:.0f}s)")
        out = pd.DataFrame(
            {"timestamp": sorted(labels.keys()), "regime": [labels[t] for t in sorted(labels.keys())]}
        )
        out.to_csv(cache_path, index=False)
        print(f"  done in {time.perf_counter() - t0:.0f}s; cached -> {cache_path}")
    return labels


class ShortPositionSimulator:
    """Research-only SHORT mirror of observability.replay.PositionSimulator.

    Same interface so it drops into ReplayEngine unchanged. Convention: the
    strategy's BUY opens a short, SELL closes it. SL sits ABOVE price,
    trailing follows the lowest low downward. Fees model Binance USDT-M
    futures: taker per side + funding per 8h of holding time.
    """

    def __init__(
        self,
        initial_balance: float = 1000.0,
        fee_pct: float = 0.0005,
        funding_8h: float = 0.0001,
        sl_atr_mult: float = 2.5,
        trailing_atr_mult: float = 1.5,
        be_enabled: bool = False,
        be_activation_atr_mult: float = 1.5,
        be_buffer_atr_mult: float = 0.1,
        risk_pct: float = 0.01,
        max_position_pct: float = 0.0,
        sl_confirm_ticks: int = 3,
    ):
        from xauby.observability.replay import SimPosition  # reuse dataclasses

        self._SimPosition = SimPosition
        self.balance = initial_balance
        self.fee_pct = fee_pct
        self.funding_8h = funding_8h
        self.sl_atr_mult = sl_atr_mult
        self.trailing_atr_mult = trailing_atr_mult
        self.be_enabled = be_enabled
        self.be_activation_atr_mult = be_activation_atr_mult
        self.be_buffer_atr_mult = be_buffer_atr_mult
        self.risk_pct = risk_pct
        self.max_position_pct = max_position_pct
        self.sl_confirm_ticks = max(1, sl_confirm_ticks)
        self.sl_breach_bars = 0
        self.position = None
        self.trades: list = []

    @property
    def has_position(self) -> bool:
        return self.position is not None

    @property
    def sl_confirmed(self) -> bool:
        return self.sl_breach_bars >= self.sl_confirm_ticks

    def update_sl_breach(self, price: float) -> None:
        if not self.has_position:
            self.sl_breach_bars = 0
            return
        sl = self.position.stop_loss
        if sl > 0 and price >= sl:
            self.sl_breach_bars += 1
        else:
            self.sl_breach_bars = 0

    def _sl_distance(self, entry_price: float, atr: float, signal) -> float:
        if signal is not None:
            if signal.stop_loss_distance is not None and signal.stop_loss_distance > 0:
                return float(signal.stop_loss_distance)
            if signal.stop_loss_price is not None and signal.stop_loss_price > 0:
                # For shorts a strategy SL price sits above entry.
                return float(signal.stop_loss_price) - entry_price
        if atr > 0:
            return atr * self.sl_atr_mult
        return 0.0

    def try_open(self, entry_price: float, atr: float, bar_time: int, signal=None) -> bool:
        if self.has_position or entry_price <= 0:
            return False
        sl_distance = self._sl_distance(entry_price, atr, signal)
        if sl_distance <= 0:
            return False
        risk_amount = self.balance * self.risk_pct
        qty = risk_amount / sl_distance
        notional = qty * entry_price
        if self.max_position_pct > 0:
            max_usdt = self.balance * self.max_position_pct
            if notional > max_usdt:
                notional = max_usdt
                qty = notional / entry_price
        if qty <= 0:
            return False
        sl = entry_price + sl_distance
        # highest_price field tracks the LOWEST low for shorts.
        self.position = self._SimPosition(
            entry_price=entry_price,
            stop_loss=sl,
            highest_price=entry_price,
            qty=qty,
            entry_time=bar_time,
            initial_sl=sl,
        )
        self.sl_breach_bars = 0
        return True

    def _close(self, exit_price: float, bar_time: int, trigger: str) -> None:
        from xauby.observability.replay import SimTrade

        if not self.position:
            return
        pos = self.position
        pnl = (pos.entry_price - exit_price) * pos.qty
        fee = (pos.entry_price + exit_price) * pos.qty * self.fee_pct
        hold_hours = max(0.0, (bar_time - pos.entry_time) / 3600.0)
        funding = pos.entry_price * pos.qty * self.funding_8h * (hold_hours / 8.0)
        net = pnl - fee - funding
        self.balance += net
        cost = pos.entry_price * pos.qty
        self.trades.append(
            SimTrade(
                entry_time=pos.entry_time,
                exit_time=bar_time,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                pnl=net,
                pnl_pct=(net / cost * 100) if cost > 0 else 0.0,
                trigger=trigger,
            )
        )
        self.position = None
        self.sl_breach_bars = 0

    def on_bar(self, *, open_p, high, low, close, atr, zone, bar_time, signal) -> list:
        events = []
        if not self.has_position:
            if (
                getattr(signal, "intent", "") == "OPEN"
                and getattr(signal, "position_side", "") == "SHORT"
            ):
                self.try_open(open_p, atr, bar_time, signal=signal)
            if not self.has_position:
                return events

        pos = self.position
        # Gap-through stop at bar open (price above SL).
        if pos.stop_loss > 0 and open_p >= pos.stop_loss:
            trigger = "TRAILING_STOP" if pos.stop_loss < pos.initial_sl else "STOP_LOSS"
            self._close(open_p, bar_time, trigger)
            return events
        if (
            getattr(signal, "intent", "") == "CLOSE"
            and getattr(signal, "position_side", "") == "SHORT"
        ) or signal.action == "SELL":  # legacy research convention
            self._close(open_p, bar_time, "SIGNAL")
            return events

        pos.highest_price = min(pos.highest_price, low)  # lowest low
        candidate_sl = pos.highest_price + atr * self.trailing_atr_mult
        if self.be_enabled and atr > 0:
            activation = pos.entry_price - atr * self.be_activation_atr_mult
            if pos.highest_price <= activation:
                candidate_sl = min(
                    candidate_sl,
                    pos.entry_price - atr * self.be_buffer_atr_mult,
                )
        if candidate_sl < pos.stop_loss:
            pos.stop_loss = candidate_sl
        return events


class RegimeGatedRunner:
    """Wraps StrategyRunner: BUY allowed only when the decision-time bar's
    regime is in the family (mirrors RegimeRouter gating; exits unaffected,
    like a live handoff where the open position keeps being managed)."""

    def __init__(
        self,
        inner: StrategyRunner,
        labels: Dict[int, str],
        family_labels: Optional[Set[str]],
        debounce: int = 1,
    ):
        self.inner = inner
        self.labels = labels
        self.family_labels = family_labels
        self.debounce = max(1, int(debounce))

    def run(self, ctx):
        sig = self.inner.run(ctx)
        if self.family_labels is None or sig.action != "BUY":
            return sig
        ts_col = ctx.df_primary["timestamp"]
        recent_ts = [int(t) for t in ts_col.iloc[-self.debounce :]]
        for t in recent_ts:
            if self.labels.get(t) not in self.family_labels:
                return hold(
                    f"regime-gated (label={self.labels.get(recent_ts[-1], 'N/A')})",
                    strategy_name=sig.strategy_name,
                    timeframe=sig.timeframe,
                )
        return sig


def run_gated_replay(
    df: pd.DataFrame,
    strategy_name: str,
    strat_cfg: Dict[str, Any],
    engine_cfg: Dict[str, Any],
    labels: Dict[int, str],
    family_labels: Optional[Set[str]],
    debounce: int = 1,
    min_entry_ts: int = 0,
    timeframe: str = "1h",
    short: bool = False,
    taker_pct: float = 0.0005,
    funding_8h: float = 0.0001,
) -> Dict[str, Any]:
    """Replicates run_plugin_replay wiring with a regime-gated runner.

    min_entry_ts: trades entered before this timestamp are dropped from the
    stats (used to exclude warmup-period trades from the test split).
    short: use the research ShortPositionSimulator (futures fee + funding).
    """
    trading_cfg = engine_cfg.get("trading") or {}
    scfg = dict(strat_cfg or {})
    strategy = load_strategy(strategy_name, scfg)
    merged_cfg = dict(strategy.config)

    sim_common = dict(
        initial_balance=DEFAULT_INITIAL_BALANCE,
        sl_atr_mult=float(merged_cfg.get("sl_atr_mult") or 2.0),
        trailing_atr_mult=float(merged_cfg.get("trailing_atr_mult", 1.5)),
        be_enabled=bool(merged_cfg.get("breakeven_sl_enabled", False)),
        be_activation_atr_mult=float(merged_cfg.get("breakeven_activation_atr_mult", 1.5)),
        be_buffer_atr_mult=float(merged_cfg.get("breakeven_buffer_atr_mult", 0.1)),
        risk_pct=float(trading_cfg.get("risk_pct") or DEFAULT_RISK_PCT),
        max_position_pct=float(trading_cfg.get("max_position_per_trade_pct", 0.0)) / 100.0,
        sl_confirm_ticks=int(trading_cfg.get("sl_confirm_ticks", 3)),
    )
    if short:
        simulator = ShortPositionSimulator(
            fee_pct=taker_pct, funding_8h=funding_8h, **sim_common
        )
    else:
        simulator = PositionSimulator(
            fee_pct=float(
                (engine_cfg.get("backtest") or {}).get("fee_pct")
                or trading_cfg.get("fee_pct")
                or DEFAULT_FEE_PCT
            ),
            **sim_common,
        )
    runner = RegimeGatedRunner(
        StrategyRunner(strategy, timeout=30.0, check_imports=False),
        labels,
        family_labels,
        debounce=debounce,
    )
    replay = ReplayEngine(
        runner=runner,
        simulator=simulator,
        symbol="BTCUSDT",
        timeframe_primary=timeframe,
        timeframe_regime=None,
        strategy_config=merged_cfg,
        engine_config=engine_cfg,
    )
    warmup = max(WARMUP_BARS, int(getattr(strategy, "min_bars", 100) or 100))
    trades, _events, final_bal, _checklist = replay.replay_bars(df, min_bars=warmup)

    if min_entry_ts > 0:
        kept = [t for t in trades if t.entry_time >= min_entry_ts]
        # Rebuild final balance for the filtered subset (trade PnL is additive).
        final_bal = DEFAULT_INITIAL_BALANCE + sum(t.pnl for t in kept)
        trades = kept
    stats = ReplayEngine.stats(trades, DEFAULT_INITIAL_BALANCE, final_bal)
    stats["trades_detail"] = [
        {"entry_time": t.entry_time, "exit_time": t.exit_time, "pnl": round(t.pnl, 4), "trigger": t.trigger}
        for t in trades
    ]
    return stats


def candidate_grid(strategy_name: str) -> List[Dict[str, Any]]:
    mod = importlib.import_module(f"xauby.strategies.{strategy_name}.strategy")
    return list(getattr(mod, "PARAM_GRID", [{}]))


def candidate_families(strategy_name: str, fams: Optional[Dict[str, Dict[str, Any]]] = None) -> List[str]:
    mod = importlib.import_module(f"xauby.strategies.{strategy_name}.strategy")
    targets = set(getattr(mod, "TARGET_REGIMES", set()))
    fams = fams or FAMILIES
    return [fam for fam, spec in fams.items() if spec["labels"] & targets]


def strategies_for_family(fam: str, requested: List[str]) -> List[str]:
    out = [FAMILIES[fam]["incumbent"]]
    for cand in CANDIDATES:
        if cand in requested and fam in candidate_families(cand):
            out.append(cand)
    return [s for s in out if s in requested or s == FAMILIES[fam]["incumbent"]]


def build_strat_cfg(cfg: Dict[str, Any], name: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if name in CANDIDATES:
        # Plugin defaults rule for candidates; YAML normalization could inject
        # unrelated generic gates (rsi_min/vol_min_ratio) and skew the test.
        base: Dict[str, Any] = {}
    else:
        base, _ = split_legacy_runtime_config(cfg, name, symbol="BTCUSDT", for_live=False)
    if overrides:
        base = {**base, **overrides}
    return base


def main() -> int:
    parser = argparse.ArgumentParser(description="Per-regime strategy PF evaluation")
    parser.add_argument("--timeframe", default="1h", choices=sorted(RECENT_BARS_BY_TF.keys()))
    parser.add_argument("--data", default=None, help="default core/backtest_candles_{tf}_btcusdt_full.csv")
    parser.add_argument("--labels-cache", default=None, help="default core/regime_labels_{tf}_btcusdt.csv")
    parser.add_argument("--strategies", default=",".join([f["incumbent"] for f in FAMILIES.values()] + list(CANDIDATES)))
    parser.add_argument("--families", default=",".join(FAMILIES.keys()))
    parser.add_argument("--train-frac", type=float, default=0.70)
    parser.add_argument("--debounce", type=int, default=1)
    parser.add_argument("--short-strategies", default="", help="comma list of short research plugins (BUY=open short)")
    parser.add_argument("--skip-long", action="store_true", help="evaluate only the short families")
    parser.add_argument("--taker-pct", type=float, default=0.0005, help="futures taker fee per side for shorts")
    parser.add_argument("--funding-8h", type=float, default=0.0001, help="funding rate per 8h for shorts")
    parser.add_argument("--grid", action="store_true", help="tune candidates on the train split only")
    parser.add_argument("--relabel", action="store_true")
    parser.add_argument("--json-out", default=None, help="default core/regime_eval_btcusdt_{tf}.json")
    parser.add_argument("--config-overrides", default=None, help="JSON file {strategy: {param: value}} of frozen tuned params")
    args = parser.parse_args()

    tf = args.timeframe
    data_path = args.data or os.path.join(ROOT, "core", f"backtest_candles_{tf}_btcusdt_full.csv")
    labels_path = args.labels_cache or os.path.join(ROOT, "core", f"regime_labels_{tf}_btcusdt.csv")
    json_out = args.json_out or os.path.join(ROOT, "core", f"regime_eval_btcusdt_{tf}.json")

    cfg = load_bot_config()
    df = load_full_df(data_path)
    with open(data_path, "rb") as f:
        data_hash = hashlib.sha256(f.read()).hexdigest()[:16]
    labels = label_regimes(df, labels_path, timeframe=tf, relabel=args.relabel)

    dist = pd.Series([labels[t] for t in df["timestamp"] if t in labels]).value_counts()
    print("\nRegime label distribution (full window):")
    for k, v in dist.items():
        print(f"  {k:22s} {v:6d} bars ({v / len(df) * 100:.1f}%)")

    split_idx = int(len(df) * args.train_frac)
    split_ts = int(df["timestamp"].iloc[split_idx])
    train_df = df.iloc[:split_idx].reset_index(drop=True)
    test_df = df.iloc[max(0, split_idx - WARMUP_BARS):].reset_index(drop=True)
    recent_df = df.tail(RECENT_BARS_BY_TF[tf]).reset_index(drop=True)
    print(f"\nData: {len(df)} bars  sha256[:16]={data_hash}")
    print(f"Train: bars 0..{split_idx} | Test: from ts {split_ts} | Recent: last {len(recent_df)} bars")

    requested = [s.strip() for s in args.strategies.split(",") if s.strip()]
    families = [f.strip() for f in args.families.split(",") if f.strip() and f.strip() in FAMILIES]

    overrides_by_strategy: Dict[str, Dict[str, Any]] = {}
    if args.config_overrides and os.path.exists(args.config_overrides):
        with open(args.config_overrides, "r", encoding="utf-8") as f:
            overrides_by_strategy = json.load(f)

    results: List[Dict[str, Any]] = []

    short_requested = [s.strip() for s in args.short_strategies.split(",") if s.strip()]

    if args.grid:
        # Tune candidates on TRAIN only; never look at test.
        grid_work = [
            (cand, fam, FAMILIES[fam]["labels"], False)
            for cand in requested if cand in CANDIDATES
            for fam in candidate_families(cand) if fam in families
        ] + [
            (cand, fam, SHORT_FAMILIES[fam]["labels"], True)
            for cand in short_requested if cand in SHORT_CANDIDATES
            for fam in candidate_families(cand, SHORT_FAMILIES)
        ]
        best_by_strategy: Dict[str, Dict[str, Any]] = {}
        for cand, fam, fam_labels, is_short in grid_work:
            rows = []
            for combo in candidate_grid(cand):
                scfg = build_strat_cfg(cfg, cand, combo)
                stats = run_gated_replay(
                    train_df, cand, scfg, cfg, labels, fam_labels,
                    debounce=args.debounce, timeframe=tf,
                    short=is_short, taker_pct=args.taker_pct, funding_8h=args.funding_8h,
                )
                rows.append({"combo": combo, **{k: stats[k] for k in ("profit_factor", "total_trades", "win_rate", "max_drawdown_pct", "net_profit_pct")}})
                print(f"  [grid] {cand}/{fam}{' SHORT' if is_short else ''} {combo} -> PF {stats['profit_factor']:.2f} trades {stats['total_trades']}")
            eligible = [r for r in rows if r["total_trades"] >= 20]
            pool = eligible or [r for r in rows if r["total_trades"] >= 10]
            if pool:
                best = max(pool, key=lambda r: r["profit_factor"])
                key = f"{cand}@{fam}"
                best_by_strategy[key] = {"combo": best["combo"], "train": best, "low_trades": not eligible, "short": is_short}
                print(f"[grid] BEST {key}: {best['combo']} PF {best['profit_factor']:.2f} trades {best['total_trades']}{' (LOW TRADES)' if not eligible else ''}")
            else:
                print(f"[grid] {cand}/{fam}: no combo produced >=10 trades on train")
        out_path = os.path.join(ROOT, "core", f"regime_grid_btcusdt_{tf}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(best_by_strategy, f, indent=2, default=str)
        print(f"\nGrid results -> {out_path}")
        return 0

    splits = [
        ("train", train_df, 0),
        ("test", test_df, split_ts),
        ("recent", recent_df, 0),
    ]

    # Worklist: (family, labels, strategy, is_short, is_incumbent)
    work: List[tuple] = []
    if not args.skip_long:
        for fam in families:
            for name in strategies_for_family(fam, requested):
                work.append((fam, FAMILIES[fam]["labels"], name, False, name == FAMILIES[fam]["incumbent"]))
    for fam, spec in SHORT_FAMILIES.items():
        names = [c for c in short_requested if fam in candidate_families(c, SHORT_FAMILIES)]
        if not names:
            continue
        inc = spec.get("incumbent")
        if inc:
            # Long incumbent measured on the same labels for comparison.
            work.append((fam, spec["labels"], inc, False, True))
        for n in names:
            work.append((fam, spec["labels"], n, True, False))

    for fam, fam_labels, name, is_short, is_inc in work:
        overrides = overrides_by_strategy.get(f"{name}@{fam}") or overrides_by_strategy.get(name)
        scfg = build_strat_cfg(cfg, name, overrides)
        for split_name, sdf, min_ts in splits:
            stats = run_gated_replay(
                sdf, name, scfg, cfg, labels, fam_labels,
                debounce=args.debounce, min_entry_ts=min_ts, timeframe=tf,
                short=is_short, taker_pct=args.taker_pct, funding_8h=args.funding_8h,
            )
            row = {
                "family": fam,
                "strategy": name,
                "split": split_name,
                "incumbent": is_inc,
                "short": is_short,
                "profit_factor": round(stats["profit_factor"], 3),
                "win_rate": round(stats["win_rate"], 1),
                "total_trades": stats["total_trades"],
                "max_drawdown_pct": round(stats["max_drawdown_pct"], 2),
                "net_profit_pct": round(stats["net_profit_pct"], 2),
                "overrides": overrides or {},
            }
            results.append(row)
            tag = "(incumbent)" if is_inc else ("(short)" if is_short else "(candidate)")
            print(f"{fam:10s} {name:24s} {tag:12s} {split_name:6s} PF {row['profit_factor']:7.2f} WR {row['win_rate']:5.1f}% trades {row['total_trades']:4d} DD {row['max_drawdown_pct']:5.2f}% net {row['net_profit_pct']:7.2f}%")

    report = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "timeframe": tf,
        "data_file": data_path,
        "data_sha256_16": data_hash,
        "bars": len(df),
        "train_frac": args.train_frac,
        "split_ts": split_ts,
        "debounce": args.debounce,
        "label_distribution": {str(k): int(v) for k, v in dist.items()},
        "results": results,
    }
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport -> {json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
