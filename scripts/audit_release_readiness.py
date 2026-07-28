#!/usr/bin/env python3
"""Fail-closed Phase 0/1 release-readiness audit.

This is intentionally read-only.  Run the static checks in CI against the repo
config, then run it again against the tenant config/runtime DB after staging a
release.  A replay request is evidence-bearing: an empty/skipped run is a
failure, not a green result.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _enabled(block: Any) -> bool:
    return isinstance(block, dict) and block.get("enabled") is True


def audit_static(config_path: str, whitelist_path: Optional[str] = None) -> list[Check]:
    from xauby.runtime.exits import validate_exit_config
    from xauby.runtime.trading_config import (
        canonical_runtime_config,
        validate_open_positions_config,
        validate_risk_config,
    )
    from xauby.saas.catalog import PRESETS
    from xauby.saas.certification import load_record
    from xauby.strategies import load_strategy
    from xauby.strategies.base import strategy_maturity

    config_path = os.path.abspath(config_path)
    root = os.path.dirname(config_path)
    with open(config_path, "r", encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle) or {}

    checks: list[Check] = []

    def add(name: str, condition: Any, detail: str) -> None:
        checks.append(Check(name, bool(condition), detail))

    observability = cfg.get("observability") or {}
    add(
        "durable_events",
        observability.get("durable_high_frequency_events") is True,
        "observability.durable_high_frequency_events must be true",
    )
    retention = cfg.get("event_retention") or {}
    bounded_retention = (
        _enabled(retention)
        and 0 < int(retention.get("jsonl_retention_days") or 0) <= 90
        and 0 < int(retention.get("sqlite_retention_days") or 0) <= 180
    )
    add(
        "bounded_event_retention",
        bounded_retention,
        "event retention must be enabled and bounded (JSONL <=90d, SQLite <=180d)",
    )
    add(
        "self_audit_scheduled",
        _enabled(cfg.get("self_audit"))
        and (cfg.get("self_audit") or {}).get("send_telegram") is True,
        "self_audit must be enabled and alert through Telegram",
    )
    monthly = cfg.get("monthly_report") or {}
    add(
        "monthly_track_record",
        _enabled(monthly)
        and monthly.get("save_to_file") is True
        and monthly.get("send_telegram") is True,
        "monthly_report must persist an artifact and alert through Telegram",
    )
    add(
        "monthly_realised_parity",
        float(monthly.get("parity_tolerance_bps") or 0.0) > 0,
        "monthly_report.parity_tolerance_bps must be positive",
    )
    architecture = cfg.get("architecture") or {}
    add(
        "api_circuit_breaker",
        architecture.get("api_circuit_breaker_enabled") is True,
        "architecture.api_circuit_breaker_enabled must be true",
    )

    try:
        runtime = canonical_runtime_config(
            cfg,
            project_root=root,
            config_path=config_path,
            whitelist_path=whitelist_path,
            for_live=True,
        )
        live_count = sum(
            1 for symbol in runtime.symbols.values()
            if symbol.get("enabled") and symbol.get("execution_mode") == "live"
        )
        validate_risk_config(cfg)
        validate_open_positions_config(cfg, live_pair_count=live_count)
        add("tenant_risk_guards", True, f"validated for {live_count} live pair(s)")
        for symbol, item in runtime.symbols.items():
            if not item.get("enabled"):
                continue
            strategy_cfg = dict(item.get("strategy") or {})
            validate_exit_config(strategy_cfg, symbol=symbol)
            strategy = load_strategy(
                str(item.get("strategy_name") or ""), strategy_cfg, strict=True
            )
            maturity = strategy_maturity(strategy)
            is_live = item.get("execution_mode") == "live"
            add(
                f"strategy:{symbol}",
                not is_live or maturity == "production",
                f"{item.get('strategy_name')} maturity={maturity or 'undeclared'}; "
                f"mode={item.get('execution_mode')}",
            )
    except Exception as exc:
        add("tenant_config", False, f"{type(exc).__name__}: {exc}")

    live_presets = [preset for preset in PRESETS if preset.get("live_certified")]
    for preset in live_presets:
        preset_id = str(preset.get("id") or "")
        status = str(preset.get("certification_status") or "not_assessed")
        evidence = preset.get("backtest") or {}
        record = load_record(preset_id) or {}
        exchange = str(preset.get("exchange_id") or "").lower()
        source = str(record.get("data_source") or "")
        protocol = record.get("protocol") or {}
        traceable = (
            status != "not_assessed"
            and evidence.get("status") == "validated"
            and protocol.get("script") == "scripts/certify_preset.py"
            and (not exchange or source.lower().startswith(exchange))
        )
        add(
            f"certificate:{preset_id}",
            traceable,
            f"status={status}, evidence={evidence.get('status')}, source={source or 'missing'}",
        )

    return checks


def audit_runtime(
    *,
    config_path: str,
    db_path: str,
    run_id: Optional[str] = None,
    symbol: Optional[str] = None,
    require_short: bool = False,
) -> list[Check]:
    from xauby.database.db import LiteDB, SCHEMA_VERSION
    from xauby.observability.replay_validation import validate_run
    from xauby.observability.store import EventStore

    checks: list[Check] = []
    db = LiteDB(db_path, readonly=True)
    conn = db._get_connection()
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()
    checks.append(Check(
        "runtime_schema", version >= SCHEMA_VERSION,
        f"runtime schema={version}, release requires >= {SCHEMA_VERSION}",
    ))
    if run_id:
        report = validate_run(
            EventStore(db=db), db, run_id,
            symbol=symbol, config_path=config_path,
        )
        checked = report.matched + report.mismatched
        checks.append(Check(
            "runtime_replay",
            report.ok and checked > 0,
            f"matched={report.matched}, mismatched={report.mismatched}, "
            f"skipped={report.skipped}",
        ))
        if require_short:
            checks.append(Check(
                "runtime_short_replay",
                report.short_signals_checked > 0,
                f"short_signals_checked={report.short_signals_checked}",
            ))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="bot_config.yaml")
    parser.add_argument("--whitelist", default=None)
    parser.add_argument("--db", default=None, help="also audit the staged/runtime DB")
    parser.add_argument("--run-id", default=None, help="also require a non-empty replay")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--require-short", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.run_id and not args.db:
        parser.error("--run-id requires --db")

    checks = audit_static(args.config, args.whitelist)
    if args.db:
        checks.extend(audit_runtime(
            config_path=args.config,
            db_path=args.db,
            run_id=args.run_id,
            symbol=args.symbol,
            require_short=args.require_short,
        ))
    if args.json:
        print(json.dumps({
            "ok": all(check.ok for check in checks),
            "checks": [asdict(check) for check in checks],
        }, indent=2, ensure_ascii=False))
    else:
        for check in checks:
            print(f"{'PASS' if check.ok else 'BLOCK'} {check.name}: {check.detail}")
        print("READY" if all(check.ok for check in checks) else "RELEASE BLOCKED")
    return 0 if all(check.ok for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
