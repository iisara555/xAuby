from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from xauby.engine.shadow import ShadowVirtualLedger
from xauby.saas.shadow_spec import SHADOW_FILL_MODEL
from xauby.shadow_evaluator import ShadowEvaluator, _sha256, run_worker


def _spec(tenant: str = "pilot-1") -> dict:
    identity = {
        "schema_version": 1,
        "tenant": tenant,
        "symbol": "BTCUSDT",
        "target_id": "okx-swap",
        "venue": "okx",
        "timeframe": "4h",
        "regime_timeframe": "",
        "fill_model": SHADOW_FILL_MODEL,
        "fees_pct": 0.05,
        "slippage_pct": 0.02,
        "initial_cash": 1_000.0,
        "allocation_fraction": 0.25,
        "max_bars": 420,
        "candidate_limit": 2,
        "research_only": True,
        "broker_access": False,
        "candidates": [
            {
                "candidate_id": "champion-a",
                "role": "champion",
                "strategy_name": "supertrend_ema200",
                "strategy_config": {"max_calc_bars": 420},
                "config_fingerprint": "a" * 64,
            },
            {
                "candidate_id": "challenger-b",
                "role": "challenger",
                "strategy_name": "supertrend_ema200",
                "strategy_config": {"max_calc_bars": 420, "supertrend_mult": 3.5},
                "config_fingerprint": "b" * 64,
            },
        ],
    }
    spec_hash = _sha256(identity)
    return {
        **identity,
        "spec_hash": spec_hash,
        "run_id": f"shadow-btcusdt-{spec_hash[:12]}",
    }


def _database(path: Path, *, count: int = 250, start: int = 1_700_000_000) -> int:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE prices ("
        "id INTEGER PRIMARY KEY, symbol TEXT, timestamp INTEGER, timeframe TEXT, "
        "open REAL, high REAL, low REAL, close REAL, volume REAL)"
    )
    rows = []
    for index in range(count):
        timestamp = start + index * 14_400
        price = 30_000 + index * 5
        rows.append(("BTCUSDT", timestamp, "4h", price, price + 20, price - 20, price + 5, 10))
    conn.executemany(
        "INSERT INTO prices(symbol,timestamp,timeframe,open,high,low,close,volume) "
        "VALUES(?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return rows[-1][1]


def _prepare(tmp_path: Path) -> tuple[Path, Path, Path, dict, int]:
    runtime_root = tmp_path / "runtime"
    runtime_dir = runtime_root / "pilot-1"
    shadow_dir = runtime_dir / "shadow" / "BTCUSDT"
    shadow_dir.mkdir(parents=True)
    spec = _spec()
    spec_path = shadow_dir / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    db_path = runtime_dir / "xauby.db"
    latest = _database(db_path)
    return runtime_root, runtime_dir, spec_path, spec, latest


def test_shadow_worker_is_durable_idempotent_and_never_mutates_source_db(tmp_path: Path) -> None:
    runtime_root, runtime_dir, _, spec, latest = _prepare(tmp_path)
    db_path = runtime_dir / "xauby.db"
    before_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()

    first = run_worker("pilot-1", runtime_root=runtime_root, now=latest + 14_401)
    assert first["status"] == "healthy"
    assert first["snapshot_count"] == 1
    assert first["research_only"] is True
    assert first["broker_access"] is False
    assert set(first["candidates"]) == {"champion-a", "challenger-b"}
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before_hash

    duplicate = run_worker("pilot-1", runtime_root=runtime_root, now=latest + 14_401)
    assert duplicate["snapshot_count"] == 1
    events_path = runtime_dir / "shadow" / "BTCUSDT" / "runs" / spec["run_id"] / "events.jsonl"
    assert len(events_path.read_text(encoding="utf-8").splitlines()) == 1

    conn = sqlite3.connect(db_path)
    next_timestamp = latest + 14_400
    conn.execute(
        "INSERT INTO prices(symbol,timestamp,timeframe,open,high,low,close,volume) "
        "VALUES(?,?,?,?,?,?,?,?)",
        ("BTCUSDT", next_timestamp, "4h", 32_000, 32_050, 31_950, 32_010, 11),
    )
    conn.commit()
    conn.close()

    second = run_worker("pilot-1", runtime_root=runtime_root, now=next_timestamp + 14_401)
    assert second["snapshot_count"] == 2
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert events[1]["prev_event_sha256"] == events[0]["event_sha256"]
    evaluator = ShadowEvaluator(spec, runtime_dir, db_path)
    restored = evaluator._load_state()
    assert restored["snapshot_count"] == 2
    assert restored["last_event_sha256"] == events[-1]["event_sha256"]


def test_shadow_worker_repairs_only_an_incomplete_tail(tmp_path: Path) -> None:
    runtime_root, runtime_dir, _, spec, latest = _prepare(tmp_path)
    run_worker("pilot-1", runtime_root=runtime_root, now=latest + 14_401)
    events_path = runtime_dir / "shadow" / "BTCUSDT" / "runs" / spec["run_id"] / "events.jsonl"
    with events_path.open("ab") as handle:
        handle.write(b'{"partial":')

    db_path = runtime_dir / "xauby.db"
    next_timestamp = latest + 14_400
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO prices(symbol,timestamp,timeframe,open,high,low,close,volume) "
        "VALUES(?,?,?,?,?,?,?,?)",
        ("BTCUSDT", next_timestamp, "4h", 32_000, 32_050, 31_950, 32_010, 11),
    )
    conn.commit()
    conn.close()

    status = run_worker("pilot-1", runtime_root=runtime_root, now=next_timestamp + 14_401)
    assert status["snapshot_count"] == 2
    assert len(events_path.read_text(encoding="utf-8").splitlines()) == 2


def test_shadow_worker_fails_closed_on_completed_hash_chain_tampering(tmp_path: Path) -> None:
    runtime_root, runtime_dir, _, spec, latest = _prepare(tmp_path)
    run_worker("pilot-1", runtime_root=runtime_root, now=latest + 14_401)
    events_path = runtime_dir / "shadow" / "BTCUSDT" / "runs" / spec["run_id"] / "events.jsonl"
    event = json.loads(events_path.read_text(encoding="utf-8"))
    event["snapshot_id"] = "tampered"
    events_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="hash chain"):
        run_worker("pilot-1", runtime_root=runtime_root, now=latest + 14_401)
    status = json.loads(
        (runtime_dir / "shadow" / "BTCUSDT" / "status.json").read_text(encoding="utf-8")
    )
    assert status["status"] == "degraded"
    assert status["broker_access"] is False


def test_shadow_worker_rejects_paths_outside_tenant(tmp_path: Path) -> None:
    runtime_root, _, _, _, _ = _prepare(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="escaped"):
        run_worker("pilot-1", runtime_root=runtime_root, spec_path=outside)


def test_shadow_worker_reports_stale_candle_feed_without_replaying(tmp_path: Path) -> None:
    runtime_root, _, _, _, latest = _prepare(tmp_path)
    first = run_worker("pilot-1", runtime_root=runtime_root, now=latest + 14_401)
    assert first["status"] == "healthy"

    stale = run_worker("pilot-1", runtime_root=runtime_root, now=latest + 43_200)
    assert stale["status"] == "degraded"
    assert stale["snapshot_count"] == 1
    assert "feed is stale" in stale["detail"]


def test_shadow_ledger_state_round_trip_preserves_open_position() -> None:
    ledger = ShadowVirtualLedger("candidate", initial_cash=1_000)
    ledger.apply(
        type("Signal", (), {"action": "BUY", "intent": "OPEN", "position_side": "LONG"})(),
        symbol="BTCUSDT",
        price=100,
        notional=250,
        fee_pct=0.05,
        timestamp=1,
    )
    restored = ShadowVirtualLedger.from_state(ledger.export_state())
    assert restored.export_state() == ledger.export_state()


def test_shadow_systemd_unit_has_no_live_capabilities_and_is_opt_in() -> None:
    project = Path(__file__).resolve().parents[1]
    service = (project / "deploy/systemd/xauby-shadow@.service").read_text(encoding="utf-8")
    timer = (project / "deploy/systemd/xauby-shadow@.timer").read_text(encoding="utf-8")
    installer = (project / "scripts/install_saas_host.sh").read_text(encoding="utf-8")
    provisioner = (project / "deploy/xauby-provision-tenant").read_text(encoding="utf-8")

    assert "User=xsh-%i" in service
    assert "PrivateNetwork=true" in service
    assert "ProtectProc=invisible" in service
    assert "InaccessiblePaths=/var/lib/xauby/runtime /etc/xauby" in service
    assert "BindReadOnlyPaths=/var/lib/xauby/runtime/%i:/run/xauby-shadow/%i" in service
    assert "BindPaths=/var/lib/xauby/runtime/%i/shadow:/run/xauby-shadow/%i/shadow" in service
    assert "EnvironmentFile" not in service
    assert 'shadow_user="xsh-${tenant}"' in provisioner
    assert 'u:"$shadow_user":r-x' in provisioner
    assert 'u:"$shadow_user":rwx,u:xauby-control:rwx' in provisioner
    assert "xauby-shadow@.service" in installer
    assert "xauby-shadow@.timer" in installer
    assert "OnUnitActiveSec=5min" in timer
    enabled = installer.split("systemctl enable", 1)[1]
    assert "xauby-shadow" not in enabled
