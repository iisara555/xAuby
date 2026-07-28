#!/usr/bin/env python3
"""Replay validation CLI — diff live signal_evaluated vs strategy re-run."""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xauby.database.db import LiteDB
from xauby.observability.replay_validation import validate_run
from xauby.observability.store import EventStore
from xauby.utils.common import format_ts_ict


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate live engine signals by replaying the strategy at logged ticks"
    )
    parser.add_argument("run_id", help="Engine run_id from state or incident list")
    parser.add_argument("--config", default="bot_config.yaml")
    parser.add_argument("--strategy", default="", help="Override strategy plugin id")
    parser.add_argument("--symbol", default=None, help="Explicitly specify the symbol")
    parser.add_argument("--max-signals", type=int, default=200)
    parser.add_argument(
        "--db", default=None,
        help="runtime SQLite path (default: SQLITE_DB_PATH/XAUBY_HOME)",
    )
    parser.add_argument(
        "--require-short", action="store_true",
        help="fail unless at least one signal was replayed while SHORT",
    )
    parser.add_argument(
        "--allow-empty", action="store_true",
        help="diagnostic only: exit zero even when no signal could be checked",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    db = LiteDB(args.db, readonly=True)
    store = EventStore(db=db)
    report = validate_run(
        store,
        db,
        args.run_id,
        symbol=args.symbol,
        config_path=args.config,
        strategy_name=args.strategy or None,
        max_signals=args.max_signals,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        if not report.ok:
            return 1
        if not args.allow_empty and report.matched + report.mismatched == 0:
            return 2
        if args.require_short and report.short_signals_checked == 0:
            return 3
        return 0

    status = "PASS" if report.ok else "FAIL"
    print(f"Replay validation [{status}] run_id={report.run_id}")
    if report.coverage:
        c = report.coverage
        print(
            f"  Event coverage: {c.wired_seen}/{c.wired_total} wired types "
            f"({c.coverage_pct:.0f}%)  integrity={'OK' if c.integrity_ok else 'FAIL'}"
        )
        if c.wired_missing:
            print(f"  Types not seen this run: {', '.join(c.wired_missing[:8])}")
        if c.integrity_issues:
            print(f"  Integrity issues: {', '.join(c.integrity_issues)}")
    print(
        f"  Strategy replay: matched={report.matched}/"
        f"{report.matched + report.mismatched} ({report.match_rate_pct:.1f}%)  "
        f"skipped={report.skipped}"
    )
    print(f"  Short-side signals checked: {report.short_signals_checked}")

    if not report.mismatches:
        if report.total_signals == 0:
            print("  No signal_evaluated events in this run.")
        else:
            print("  All replayed signals match live events.")
        if not report.ok:
            return 1
        if not args.allow_empty and report.matched + report.mismatched == 0:
            print("  RELEASE BLOCKED: no signal/tick pair could be checked.")
            return 2
        if args.require_short and report.short_signals_checked == 0:
            print("  RELEASE BLOCKED: no signal was checked while SHORT.")
            return 3
        return 0

    print(f"\nMismatches ({len(report.mismatches)}):")
    for m in report.mismatches[:25]:
        ts = format_ts_ict(m.ts, fmt="%m-%d %H:%M:%S")
        print(
            f"  {ts} #{m.seq} tick={m.tick_id} @ {m.price:.2f}\n"
            f"    live={m.live_action}  replay={m.replay_action}\n"
            f"    live:   {m.live_reason[:80]}\n"
            f"    replay: {m.replay_reason[:80]}"
        )
    if len(report.mismatches) > 25:
        print(f"  ... and {len(report.mismatches) - 25} more")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
