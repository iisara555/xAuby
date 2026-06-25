"""Replay incident CLI — step-by-step event trace for a run_id."""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xauby.observability.replay import replay_incident
from xauby.observability.incidents import format_timeline_line, summarize_run
from xauby.observability.store import EventStore


def main():
    parser = argparse.ArgumentParser(description="Replay / inspect events for a run_id")
    parser.add_argument("run_id", help="Engine run_id (from logs or xauby_bot_state.json)")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    parser.add_argument(
        "--all",
        dest="notable_only",
        action="store_false",
        help="Include tick/heartbeat/HOLD noise",
    )
    args = parser.parse_args()

    store = EventStore()
    events = replay_incident(store, args.run_id, limit=args.limit)

    if args.json:
        print(json.dumps(events, indent=2, ensure_ascii=False))
        return

    if not events:
        print(f"No events found for run_id={args.run_id}")
        return

    summary = summarize_run(events)
    print(
        f"Incident trace: run_id={args.run_id} "
        f"({summary['notable']} notable / {summary['total']} events)\n"
    )
    for ev in events:
        if args.notable_only and not _skip(ev):
            continue
        print(format_timeline_line(ev, width=100))


def _skip(ev: dict) -> bool:
    from xauby.observability.incidents import is_notable_event
    return not is_notable_event(ev)


if __name__ == "__main__":
    main()
