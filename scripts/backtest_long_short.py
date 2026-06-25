"""Backtest CDC ActionZone as long-only, short-only, and long/short reversal.

Purpose: measure whether adding the SHORT side adds edge before investing in any
live short (margin) infrastructure. This is an evaluation tool — it does NOT touch
the live engine, Signal contract, or order path.

CDC math is reused verbatim from the plugin (``classify_zone`` / ``_action_price``)
so zone classification matches the live strategy exactly. Fill timing mirrors
``ReplayEngine``: the decision uses the last CLOSED bar (index i-1) and the order
fills at the OPEN of the next bar (index i) — no look-ahead.

Rules (CDC ActionZone V3 2020 "แท้"):
  * long  entry : fresh GREEN  (zone[i-1]==GREEN and zone[i-2]!=GREEN)
  * short entry : fresh RED    (zone[i-1]==RED   and zone[i-2]!=RED)
  * long  exit  : zone[i-1]==RED      (mirrors strategy SELL on RED zone)
  * short exit  : zone[i-1]==GREEN
  * no stop loss, fee both legs, fixed-fraction (``position_pct``) compounding sizing.
"""
from __future__ import annotations

import argparse
import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")

import pandas_ta as ta

from xauby.backtest.data import get_or_load_data
from xauby.strategies.cdc_action_zone.indicators import _action_price, classify_zone

FEE = 0.001
MIN_BARS = 100


def _zone_series(close, ap_smoothing: int) -> list:
    """Per-bar CDC zone using the plugin's exact AP/EMA math."""
    ap = _action_price(close, ap_smoothing)
    fast = ta.ema(ap, length=12)
    slow = ta.ema(ap, length=26)
    n = len(close)
    zone = [None] * n
    for i in range(n):
        f = fast.iloc[i] if fast is not None else None
        s = slow.iloc[i] if slow is not None else None
        if f is None or s is None or f != f or s != s:  # NaN guard
            continue
        zone[i] = classify_zone(float(ap.iloc[i]), float(f), float(s))
    return zone


def _simulate(df, zone, mode: str, position_pct: float) -> dict:
    """Run one mode. side: +1 long, -1 short, 0 flat. Fill at next-bar open."""
    allow_long = mode in ("long_only", "long_short")
    allow_short = mode in ("short_only", "long_short")

    balance = 1.0
    side = 0
    entry = 0.0
    qty = 0.0
    trades = []          # (side, ret_frac, pnl)
    peak = 1.0
    max_dd = 0.0

    opens = df["open"].astype(float).reset_index(drop=True)
    closes = df["close"].astype(float).reset_index(drop=True)
    n = len(df)

    def close_pos(exit_px, bar_side):
        nonlocal balance, side, entry, qty
        pnl = bar_side * (exit_px - entry) * qty
        fee = (entry * qty + exit_px * qty) * FEE
        net = pnl - fee
        balance += net
        cost = entry * qty
        trades.append((bar_side, (net / cost) if cost > 0 else 0.0, net))
        side = 0
        entry = 0.0
        qty = 0.0

    def open_pos(entry_px, new_side):
        nonlocal balance, side, entry, qty
        side = new_side
        entry = entry_px
        qty = (balance * position_pct) / entry_px if entry_px > 0 else 0.0

    for i in range(MIN_BARS, n):
        z1 = zone[i - 1]
        z2 = zone[i - 2]
        op = float(opens.iloc[i])

        fresh_green = z1 == "GREEN" and z2 != "GREEN"
        fresh_red = z1 == "RED" and z2 != "RED"

        # --- act on the decision from the last closed bar, at this bar's open ---
        if side == 1 and z1 == "RED":            # exit / flip long
            close_pos(op, 1)
        elif side == -1 and z1 == "GREEN":       # exit / flip short
            close_pos(op, -1)

        if side == 0:
            if allow_long and fresh_green:
                open_pos(op, 1)
            elif allow_short and fresh_red:
                open_pos(op, -1)

        # --- mark-to-market drawdown on the bar close ---
        cp = float(closes.iloc[i])
        eq = balance + (side * (cp - entry) * qty if side != 0 else 0.0)
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)

    if side != 0:
        close_pos(float(closes.iloc[-1]), side)

    rets = [t[1] for t in trades]
    wins = sum(1 for r in rets if r > 0)
    gp = sum(t[2] for t in trades if t[2] > 0)
    gl = -sum(t[2] for t in trades if t[2] < 0)
    long_net = sum(t[2] for t in trades if t[0] == 1)
    short_net = sum(t[2] for t in trades if t[0] == -1)
    return {
        "net_pct": (balance - 1.0) * 100.0,
        "win_pct": (100.0 * wins / len(rets)) if rets else 0.0,
        "trades": len(rets),
        "mdd_pct": max_dd * 100.0,
        "pf": (gp / gl) if gl > 0 else (99.9 if gp > 0 else 0.0),
        "long_net_pct": long_net * 100.0,
        "short_net_pct": short_net * 100.0,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="CDC long-only vs short-only vs long/short backtest")
    p.add_argument("--symbol", default="XAUTUSDT")
    p.add_argument("--timeframe", default="4h")
    p.add_argument("--ap", type=int, default=2, help="ap_smoothing (2 = V3)")
    p.add_argument("--position-pct", type=float, default=1.0)
    p.add_argument("--force-download", action="store_true")
    args = p.parse_args()

    dfp, _, dsym, proxy = get_or_load_data(
        args.symbol,
        primary_timeframe=args.timeframe,
        regime_timeframe=None,
        force_download=args.force_download,
    )
    if dfp is None or dfp.empty:
        raise SystemExit(f"No candle data for {args.symbol}")

    df = dfp.reset_index(drop=True)
    zone = _zone_series(df["close"].astype(float).reset_index(drop=True), args.ap)

    tag = f"{dsym}{'*proxy' if proxy else ''}"
    print(f"Symbol {args.symbol} ({tag}) {args.timeframe} bars={len(df)} "
          f"ap={args.ap} position_pct={args.position_pct}")
    print(f"{'mode':14s} {'net%':>8s} {'win%':>6s} {'trd':>4s} {'mdd%':>7s} {'PF':>6s} "
          f"{'long%':>8s} {'short%':>8s}")
    print("-" * 72)
    for mode in ("long_only", "short_only", "long_short"):
        r = _simulate(df, zone, mode, args.position_pct)
        print(f"{mode:14s} {r['net_pct']:8.2f} {r['win_pct']:6.1f} {r['trades']:4d} "
              f"{r['mdd_pct']:7.2f} {r['pf']:6.2f} "
              f"{r['long_net_pct']:8.2f} {r['short_net_pct']:8.2f}")


if __name__ == "__main__":
    main()
