#!/usr/bin/env python3
"""Read-only Binance TH weekly CDC snapshot. No engine, keys, orders or P&L.

Run with PYTHONPATH=. python3 scripts/weekly_cdc_snapshot.py. Only public GET
requests are made. --input-dir reuses saved <symbol>.json and time.json files.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import pandas as pd

from xauby.strategies.cdc_weekly_spot.strategy import CDCWeeklySpotStrategy
from xauby.strategies.context import MarketContext

BASE_URL = "https://api.binance.th"
SYMBOLS = ("BTCTHB", "SOLTHB")
WEEK_MS = 604_800_000


def iso(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat()


def snapshot(symbol: str, rows: list, server_time_ms: int) -> dict:
    """Validate native weekly candles, then evaluate only the latest close."""
    if symbol not in SYMBOLS or not isinstance(rows, list) or not rows:
        raise ValueError("Expected native BTCTHB or SOLTHB weekly klines")
    if any(not isinstance(r, list) or len(r) < 7 for r in rows):
        raise ValueError("Invalid kline payload")
    opens = [int(r[0]) for r in rows]
    if any(b <= a for a, b in zip(opens, opens[1:])):
        raise ValueError("Candles must be ordered and unique")
    if any(int(r[6]) != int(r[0]) + WEEK_MS - 1 for r in rows):
        raise ValueError("Expected full 1w candle intervals")
    closed = [r for r in rows if int(r[6]) < server_time_ms]
    if len(closed) < CDCWeeklySpotStrategy.min_bars:
        raise ValueError("Need at least 100 closed weekly candles")
    if server_time_ms - (int(closed[-1][6]) + 1) >= WEEK_MS:
        raise ValueError("Latest completed week is missing")
    if any(int(b[0]) - int(a[0]) != WEEK_MS for a, b in zip(closed, closed[1:])):
        raise ValueError("Missing weekly candles; do not bridge gaps")
    frame = pd.DataFrame([
        {"timestamp": int(r[0]) // 1000,
         **{key: float(r[i]) for i, key in enumerate(
             ("open", "high", "low", "close", "volume"), 1)}}
        for r in closed
    ])
    values = frame[["open", "high", "low", "close", "volume"]]
    if not np.isfinite(values.to_numpy()).all() or (values.iloc[:, :4] <= 0).any().any():
        raise ValueError("Non-finite or non-positive candle prices")
    if ((frame.high < frame[["open", "close", "low"]].max(axis=1)).any()
            or (frame.low > frame[["open", "close", "high"]].min(axis=1)).any()
            or (frame.volume < 0).any()):
        raise ValueError("Invalid OHLCV candle values")
    strategy = CDCWeeklySpotStrategy(CDCWeeklySpotStrategy.default_config())
    context = MarketContext(
        symbol=symbol, timeframe_primary="1w", df_primary=frame,
        current_price=float(frame.close.iloc[-1]),
        extras={"last_bar_is_forming": False},
    )
    flat = strategy.analyze(context)
    held = strategy.analyze(replace(context, has_position=True, position_side="LONG"))
    indicators = flat.indicators
    return {
        "symbol": symbol,
        "source_url": f"{BASE_URL}/api/v1/klines?symbol={symbol}&interval=1w&limit=1000",
        "timeframe": "1w",
        "as_of_utc": iso(server_time_ms),
        "first_week_open_utc": iso(int(closed[0][0])),
        "closed_weeks": len(closed),
        "forming_rows_ignored": len(rows) - len(closed),
        "week_open_utc": iso(int(closed[-1][0])),
        "decision_time_utc": iso(int(closed[-1][6]) + 1),
        "close_thb": float(frame.close.iloc[-1]),
        "zone": indicators["cdc_zone_4h"],
        "previous_zone": indicators["cdc_zone_4h_prev"],
        "ema12_thb": indicators["ema_fast_4h"],
        "ema26_thb": indicators["ema_slow_4h"],
        "action_if_flat": flat.action,
        "action_if_holding_long": held.action,
        "strategy": strategy.name,
        "performance_assessed": False,
    }


def public_json(path: str):
    with urlopen(BASE_URL + path, timeout=15) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path)
    args = parser.parse_args()
    if args.input_dir:
        now = json.loads((args.input_dir / "time.json").read_text())["serverTime"]
        data = {s: json.loads((args.input_dir / f"{s}.json").read_text()) for s in SYMBOLS}
    else:
        now = public_json("/api/v1/time")["serverTime"]
        data = {s: public_json(f"/api/v1/klines?symbol={s}&interval=1w&limit=1000")
                for s in SYMBOLS}
    print(json.dumps([snapshot(s, data[s], int(now)) for s in SYMBOLS], indent=2,
                     allow_nan=False))


if __name__ == "__main__":
    main()
