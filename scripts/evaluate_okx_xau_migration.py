"""Evaluate whether OKX XAU long/short is worth migrating for profit."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

import pandas as pd
import pandas_ta as ta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scripts.fetch_okx_xau_history import (  # noqa: E402
    DEFAULT_INST_ID,
    candle_cache_path,
    fetch_candles,
    candles_to_cache_df,
)
from xauby.backtest.constants import CACHE_DIR  # noqa: E402
from xauby.strategies.cdc_action_zone.indicators import _action_price, classify_zone  # noqa: E402


@dataclass
class ModeResult:
    mode: str
    net_pct: float
    win_pct: float
    trades: int
    mdd_pct: float
    profit_factor: float
    long_net_pct: float
    short_net_pct: float


@dataclass
class MigrationDecision:
    symbol: str
    timeframe: str
    bars: int
    start: str
    end: str
    fee_pct: float
    slippage_bps: float
    funding_rate_8h: float
    min_profit_edge_pp: float
    passed: bool
    reason: str
    modes: Dict[str, ModeResult]


def load_okx_candles(
    inst_id: str,
    timeframe: str,
    *,
    cache_dir: str = CACHE_DIR,
    fetch_if_missing: bool = False,
    limit: int = 900,
) -> pd.DataFrame:
    path = candle_cache_path(inst_id, timeframe, cache_dir=cache_dir)
    if os.path.exists(path):
        return pd.read_csv(path)
    if not fetch_if_missing:
        raise FileNotFoundError(
            f"Missing {path}. Run scripts/fetch_okx_xau_history.py first or pass --fetch."
        )
    rows = fetch_candles(inst_id, timeframe, limit=limit, pages=max(1, (limit + 299) // 300))
    df = candles_to_cache_df(rows, timeframe)
    if df.empty:
        raise RuntimeError("OKX returned no candle data")
    os.makedirs(cache_dir, exist_ok=True)
    df.to_csv(path, index=False)
    return df


def zone_series(close: pd.Series, ap_smoothing: int) -> List[Optional[str]]:
    ap = _action_price(close.astype(float), ap_smoothing)
    fast = ta.ema(ap, length=12)
    slow = ta.ema(ap, length=26)
    zones: List[Optional[str]] = []
    for i in range(len(close)):
        f = fast.iloc[i] if fast is not None else None
        s = slow.iloc[i] if slow is not None else None
        if f is None or s is None or f != f or s != s:
            zones.append(None)
        else:
            zones.append(classify_zone(float(ap.iloc[i]), float(f), float(s)))
    return zones


def simulate_mode(
    df: pd.DataFrame,
    zones: List[Optional[str]],
    mode: str,
    *,
    fee_pct: float,
    slippage_bps: float,
    funding_rate_8h: float,
    bar_hours: float,
    position_pct: float = 1.0,
    min_bars: int = 100,
) -> ModeResult:
    allow_long = mode in ("long_only", "long_short")
    allow_short = mode in ("short_only", "long_short")
    slip = float(slippage_bps) / 10_000.0
    balance = 1.0
    side = 0
    entry = 0.0
    qty = 0.0
    funding_paid = 0.0
    trades: List[tuple[int, float, float]] = []
    peak = 1.0
    max_dd = 0.0

    opens = df["open"].astype(float).reset_index(drop=True)
    closes = df["close"].astype(float).reset_index(drop=True)

    def fill_price(raw_price: float, order_side: int) -> float:
        # order_side: +1 buy, -1 sell. Buys pay up; sells receive down.
        return raw_price * (1.0 + slip if order_side > 0 else 1.0 - slip)

    def accrue_funding(mark_price: float) -> None:
        nonlocal funding_paid
        if side == 0 or funding_rate_8h == 0.0 or bar_hours <= 0:
            return
        notional = mark_price * qty
        funding_paid += side * notional * funding_rate_8h * (bar_hours / 8.0)

    def close_position(raw_exit_px: float, bar_side: int) -> None:
        nonlocal balance, side, entry, qty, funding_paid
        exit_px = fill_price(raw_exit_px, -1 if bar_side == 1 else 1)
        pnl = bar_side * (exit_px - entry) * qty
        fees = (entry * qty + exit_px * qty) * fee_pct
        net = pnl - fees - funding_paid
        balance += net
        cost = entry * qty
        trades.append((bar_side, net / cost if cost > 0 else 0.0, net))
        side = 0
        entry = 0.0
        qty = 0.0
        funding_paid = 0.0

    def open_position(raw_entry_px: float, new_side: int) -> None:
        nonlocal side, entry, qty, funding_paid
        order_side = 1 if new_side == 1 else -1
        entry = fill_price(raw_entry_px, order_side)
        qty = (balance * position_pct) / entry if entry > 0 else 0.0
        side = new_side
        funding_paid = 0.0

    for i in range(min_bars, len(df)):
        z1 = zones[i - 1]
        z2 = zones[i - 2]
        op = float(opens.iloc[i])
        fresh_green = z1 == "GREEN" and z2 != "GREEN"
        fresh_red = z1 == "RED" and z2 != "RED"

        if side == 1 and z1 == "RED":
            close_position(op, 1)
        elif side == -1 and z1 == "GREEN":
            close_position(op, -1)

        if side == 0:
            if allow_long and fresh_green:
                open_position(op, 1)
            elif allow_short and fresh_red:
                open_position(op, -1)

        cp = float(closes.iloc[i])
        accrue_funding(cp)
        equity = balance + (side * (cp - entry) * qty if side else 0.0) - funding_paid
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)

    if side != 0:
        close_position(float(closes.iloc[-1]), side)

    returns = [row[1] for row in trades]
    wins = sum(1 for value in returns if value > 0)
    gross_profit = sum(row[2] for row in trades if row[2] > 0)
    gross_loss = -sum(row[2] for row in trades if row[2] < 0)
    long_net = sum(row[2] for row in trades if row[0] == 1)
    short_net = sum(row[2] for row in trades if row[0] == -1)
    return ModeResult(
        mode=mode,
        net_pct=(balance - 1.0) * 100.0,
        win_pct=(100.0 * wins / len(returns)) if returns else 0.0,
        trades=len(returns),
        mdd_pct=max_dd * 100.0,
        profit_factor=(gross_profit / gross_loss) if gross_loss > 0 else (99.9 if gross_profit > 0 else 0.0),
        long_net_pct=long_net * 100.0,
        short_net_pct=short_net * 100.0,
    )


def evaluate(
    df: pd.DataFrame,
    *,
    symbol: str = "XAUUSDT",
    timeframe: str = "4h",
    ap_smoothing: int = 2,
    fee_pct: float = 0.0005,
    slippage_bps: float = 2.0,
    funding_rate_8h: float = 0.00004,
    min_profit_edge_pp: float = 5.0,
) -> MigrationDecision:
    if df.empty:
        raise ValueError("No candles to evaluate")
    clean = df.dropna(subset=["open", "close"]).sort_values("open_time").reset_index(drop=True)
    zones = zone_series(clean["close"], ap_smoothing)
    modes = {
        mode: simulate_mode(
            clean,
            zones,
            mode,
            fee_pct=fee_pct,
            slippage_bps=slippage_bps,
            funding_rate_8h=funding_rate_8h,
            bar_hours=_bar_hours(timeframe),
        )
        for mode in ("long_only", "short_only", "long_short")
    }
    edge = modes["long_short"].net_pct - modes["long_only"].net_pct
    passed = modes["long_short"].net_pct > 0.0 and edge >= min_profit_edge_pp
    if passed:
        reason = f"PASS: long_short beats long_only by {edge:.2f} pp"
    else:
        reason = f"FAIL: long_short edge {edge:.2f} pp is below {min_profit_edge_pp:.2f} pp or not positive"
    return MigrationDecision(
        symbol=symbol,
        timeframe=timeframe,
        bars=len(clean),
        start=str(pd.to_datetime(int(clean["open_time"].iloc[0]), unit="ms")),
        end=str(pd.to_datetime(int(clean["open_time"].iloc[-1]), unit="ms")),
        fee_pct=fee_pct,
        slippage_bps=slippage_bps,
        funding_rate_8h=funding_rate_8h,
        min_profit_edge_pp=min_profit_edge_pp,
        passed=passed,
        reason=reason,
        modes=modes,
    )


def _bar_hours(timeframe: str) -> float:
    tf = str(timeframe or "4h").lower()
    value = int(tf[:-1] or "1")
    if tf.endswith("m"):
        return value / 60.0
    if tf.endswith("h"):
        return float(value)
    if tf.endswith("d"):
        return float(value * 24)
    return 4.0


def _decision_to_json(decision: MigrationDecision) -> Dict[str, object]:
    out = asdict(decision)
    out["modes"] = {name: asdict(result) for name, result in decision.modes.items()}
    return out


def print_table(decision: MigrationDecision) -> None:
    print(
        f"{decision.symbol} {decision.timeframe} bars={decision.bars} "
        f"{decision.start} -> {decision.end}"
    )
    print(
        f"fee={decision.fee_pct:.6f} slippage_bps={decision.slippage_bps:.2f} "
        f"funding_8h={decision.funding_rate_8h:.8f}"
    )
    print(f"{'mode':14s} {'net%':>8s} {'win%':>6s} {'trd':>4s} {'mdd%':>7s} {'PF':>6s} {'long%':>8s} {'short%':>8s}")
    print("-" * 76)
    for mode in ("long_only", "short_only", "long_short"):
        r = decision.modes[mode]
        print(
            f"{mode:14s} {r.net_pct:8.2f} {r.win_pct:6.1f} {r.trades:4d} "
            f"{r.mdd_pct:7.2f} {r.profit_factor:6.2f} {r.long_net_pct:8.2f} {r.short_net_pct:8.2f}"
        )
    print(decision.reason)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate OKX XAU long/short migration")
    parser.add_argument("--inst-id", default=DEFAULT_INST_ID)
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--cache-dir", default=CACHE_DIR)
    parser.add_argument("--fetch", action="store_true", help="Fetch OKX candles if the cache is missing")
    parser.add_argument("--limit", type=int, default=900)
    parser.add_argument("--fee-pct", type=float, default=0.0005)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--funding-rate-8h", type=float, default=0.00004)
    parser.add_argument("--min-profit-edge-pp", type=float, default=5.0)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-fail", action="store_true", help="Always exit 0 after printing the decision")
    args = parser.parse_args()

    df = load_okx_candles(
        args.inst_id,
        args.timeframe,
        cache_dir=args.cache_dir,
        fetch_if_missing=args.fetch,
        limit=args.limit,
    )
    decision = evaluate(
        df,
        symbol="XAUUSDT",
        timeframe=args.timeframe,
        fee_pct=args.fee_pct,
        slippage_bps=args.slippage_bps,
        funding_rate_8h=args.funding_rate_8h,
        min_profit_edge_pp=args.min_profit_edge_pp,
    )
    if args.json:
        print(json.dumps(_decision_to_json(decision), indent=2, sort_keys=True))
    else:
        print_table(decision)
    return 0 if args.no_fail or decision.passed else 2


if __name__ == "__main__":
    sys.exit(main())
