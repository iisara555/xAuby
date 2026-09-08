"""Validate public OKX candles and export immutable LONA research inputs.

This is data preparation only, with no strategy simulation or exchange auth.
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

UTC = dt.timezone.utc


def prepare(source, output):
    start = int(dt.datetime(2026, 3, 9, tzinfo=UTC).timestamp())
    end = int(dt.datetime(2026, 9, 8, tzinfo=UTC).timestamp())
    rows = []
    with source.open() as handle:
        for row in csv.DictReader(handle):
            ts = int(row["timestamp"])
            if not start <= ts < end:
                continue
            values = [float(row[key]) for key in ("open", "high", "low", "close", "volume")]
            o, h, low, c, v = values
            if not all(math.isfinite(value) for value in values) or min(o, h, low, c) <= 0 or v < 0:
                raise ValueError("invalid OHLCV value")
            if h < max(o, low, c) or low > min(o, h, c) or ts % 900:
                raise ValueError("invalid OHLC bounds or 15-minute alignment")
            rows.append([ts, *values])
    timestamps = [row[0] for row in rows]
    if len(rows) < 17000 or timestamps != sorted(set(timestamps)):
        raise ValueError("insufficient, duplicate, or out-of-order data")
    gaps = [(a, b) for a, b in zip(timestamps, timestamps[1:]) if b - a != 900]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for ts, *values in rows:
            writer.writerow([dt.datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M:%S"), *values])
    return {
        "file": output.name,
        "bars": len(rows),
        "start": dt.datetime.fromtimestamp(timestamps[0], UTC).isoformat(),
        "end": dt.datetime.fromtimestamp(timestamps[-1], UTC).isoformat(),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "gap_count": len(gaps),
        "missing_bars": sum((b - a) // 900 - 1 for a, b in gaps),
        "zero_volume_bars": sum(row[-1] == 0 for row in rows),
        "gap_policy": "No synthetic fills; preserve exchange timestamps.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    metadata = prepare(args.source, args.output)
    args.output.with_suffix(".metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps(metadata, indent=2))
