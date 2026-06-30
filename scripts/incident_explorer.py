#!/usr/bin/env python3
"""Incident Explorer CLI — list runs or print a run timeline."""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xauby.observability.incidents import (
    format_timeline_line,
    list_runs,
    load_run_timeline,
    summarize_run,
)
from xauby.observability.store import EventStore
from xauby.utils.common import format_ts_ict
from xauby.meta import PRODUCT_TITLE


def cmd_list(args: argparse.Namespace) -> None:
    store = EventStore()
    runs = list_runs(store, limit=args.limit, symbol=args.symbol or None)
    if args.json:
        print(json.dumps(runs, indent=2, ensure_ascii=False))
        return
    if not runs:
        print("No engine runs found in event store.")
        return
    print(f"{'run_id':<14} {'started (ICT)':<17} {'ended':<8} {'events':>6}  mode")
    print("-" * 60)
    for row in runs:
        rid = row.get("run_id", "?")
        started = format_ts_ict(row.get("started_at") or "", fmt="%m-%d %H:%M:%S")
        ended = format_ts_ict(row.get("ended_at") or "", fmt="%H:%M:%S")
        count = int(row.get("event_count") or 0)
        mode = row.get("mode") or "?"
        print(f"{rid:<14} {started:<17} {ended:<8} {count:>6}  {mode}")


def cmd_show(args: argparse.Namespace) -> None:
    store = EventStore()
    filter_mode = getattr(args, "filter", None) or (
        "all" if not args.notable_only else "notable"
    )
    events = load_run_timeline(
        store,
        args.run_id,
        limit=args.limit,
        filter_mode=filter_mode,
    )
    if args.json:
        print(json.dumps(events, indent=2, ensure_ascii=False))
        return
    if not events:
        print(f"No events for run_id={args.run_id}")
        return
    summary = summarize_run(
        load_run_timeline(store, args.run_id, limit=args.limit, notable_only=False)
    )
    print(
        f"Run {args.run_id} — {summary.get('symbol')} / {summary.get('mode')} — "
        f"{summary.get('notable')} notable / {summary.get('total')} total events\n"
    )
    for ev in events:
        print(format_timeline_line(ev, width=100))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=f"{PRODUCT_TITLE} — incident explorer",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List recent engine runs")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--symbol", default="")
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="Show chronological timeline for a run_id")
    p_show.add_argument("run_id")
    p_show.add_argument("--limit", type=int, default=500)
    p_show.add_argument(
        "--all",
        dest="notable_only",
        action="store_false",
        help="Include tick/heartbeat/HOLD noise (same as --filter all)",
    )
    p_show.add_argument(
        "--filter",
        choices=("notable", "trade", "all"),
        default=None,
        help="Timeline filter: notable (default), trade (BUY/SELL/ORDER_FILLED), or all",
    )
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_show)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
