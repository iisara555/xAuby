"""Durable, credential-free closed-candle shadow evaluator.

The worker reads candles from the tenant engine's SQLite database in read-only
mode, evaluates exactly two isolated strategy candidates, and writes only below
``runtime/<tenant>/shadow``. It never imports an execution broker and its
artifacts are explicitly research-only.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from xauby.engine.shadow import (
    ShadowCandidateSpec,
    ShadowRunnerPool,
    ShadowVirtualLedger,
)
from xauby.saas.security import validate_tenant_slug
from xauby.saas.shadow_spec import SHADOW_FILL_MODEL, SHADOW_SPEC_VERSION
from xauby.strategies.context import MarketContext
from xauby.strategies.timeframes import timeframe_seconds
from xauby.utils.atomic_io import atomic_json_write

EVENT_VERSION = 1
STATUS_VERSION = 1
MAX_CATCH_UP_CANDLES = 64


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _finite(value: Any, name: str, *, minimum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError(f"{name} is outside its allowed range")
    return result


def _validate_spec(raw: Mapping[str, Any], tenant: str) -> dict[str, Any]:
    spec = dict(raw)
    if int(spec.get("schema_version") or 0) != SHADOW_SPEC_VERSION:
        raise ValueError("unsupported shadow spec version")
    if str(spec.get("tenant") or "") != tenant:
        raise ValueError("shadow spec tenant does not match worker tenant")
    symbol = str(spec.get("symbol") or "").upper()
    if not symbol.isalnum() or len(symbol) > 24:
        raise ValueError("invalid shadow symbol")
    timeframe = str(spec.get("timeframe") or "").lower()
    if timeframe_seconds(timeframe, default=0) <= 0:
        raise ValueError("invalid shadow timeframe")
    regime_timeframe = str(spec.get("regime_timeframe") or "").lower()
    if regime_timeframe and timeframe_seconds(regime_timeframe, default=0) <= 0:
        raise ValueError("invalid shadow regime timeframe")
    candidates = spec.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 2:
        raise ValueError("shadow MVP requires exactly two candidates")
    candidate_ids = set()
    for item in candidates:
        if not isinstance(item, dict):
            raise ValueError("shadow candidate must be an object")
        candidate_id = str(item.get("candidate_id") or "")
        strategy_name = str(item.get("strategy_name") or "")
        if not candidate_id or not strategy_name or candidate_id in candidate_ids:
            raise ValueError("shadow candidates require unique ids and strategies")
        candidate_ids.add(candidate_id)
        if not isinstance(item.get("strategy_config"), dict):
            raise ValueError("shadow strategy config must be an object")
        if not str(item.get("config_fingerprint") or ""):
            raise ValueError("shadow candidate config fingerprint is required")
    if spec.get("fill_model") != SHADOW_FILL_MODEL:
        raise ValueError("unsupported shadow fill model")
    if spec.get("research_only") is not True or spec.get("broker_access") is not False:
        raise ValueError("shadow worker must be research-only with broker access disabled")
    fees_pct = _finite(spec.get("fees_pct"), "fees_pct", minimum=0.0)
    slippage_pct = _finite(spec.get("slippage_pct"), "slippage_pct", minimum=0.0)
    if fees_pct > 5 or slippage_pct > 5:
        raise ValueError("research costs cannot exceed 5 percent per fill")
    _finite(spec.get("initial_cash"), "initial_cash", minimum=0.01)
    allocation = _finite(spec.get("allocation_fraction"), "allocation_fraction", minimum=0.0)
    if allocation <= 0 or allocation > 1:
        raise ValueError("allocation_fraction must be in (0, 1]")
    max_bars = int(spec.get("max_bars") or 0)
    if max_bars < 100 or max_bars > 2_000:
        raise ValueError("max_bars must be between 100 and 2000")
    identity = {key: value for key, value in spec.items() if key not in {"run_id", "spec_hash"}}
    expected_hash = _sha256(identity)
    if str(spec.get("spec_hash") or "") != expected_hash:
        raise ValueError("shadow spec hash does not match its contents")
    expected_run = f"shadow-{symbol.lower()}-{expected_hash[:12]}"
    if str(spec.get("run_id") or "") != expected_run:
        raise ValueError("shadow run id does not match its spec")
    return spec


class SQLiteCandleSource:
    """Read tenant candles through SQLite URI read-only mode."""

    def __init__(self, path: Path) -> None:
        if not path.is_file():
            raise FileNotFoundError(f"tenant candle database not found: {path}")
        self.path = path.resolve()
        self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=2.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA query_only=ON")

    def close(self) -> None:
        self.conn.close()

    def pending_timestamps(
        self,
        symbol: str,
        timeframe: str,
        last_timestamp: int,
        now: float,
    ) -> list[int]:
        tf_seconds = timeframe_seconds(timeframe)
        if last_timestamp <= 0:
            rows = self.conn.execute(
                "SELECT timestamp FROM prices WHERE symbol=? AND timeframe=? "
                "ORDER BY timestamp DESC LIMIT 4",
                (symbol, timeframe),
            ).fetchall()
            for row in rows:
                timestamp = int(row["timestamp"])
                if timestamp + tf_seconds <= now:
                    return [timestamp]
            return []
        rows = self.conn.execute(
            "SELECT timestamp FROM prices WHERE symbol=? AND timeframe=? AND timestamp>? "
            "ORDER BY timestamp ASC LIMIT ?",
            (symbol, timeframe, last_timestamp, MAX_CATCH_UP_CANDLES),
        ).fetchall()
        return [int(row["timestamp"]) for row in rows if int(row["timestamp"]) + tf_seconds <= now]

    def history(
        self,
        symbol: str,
        timeframe: str,
        latest_open_timestamp: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT timestamp,open,high,low,close,volume FROM prices "
            "WHERE symbol=? AND timeframe=? AND timestamp<=? "
            "ORDER BY timestamp DESC LIMIT ?",
            (symbol, timeframe, latest_open_timestamp, limit),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def regime_history(
        self,
        symbol: str,
        timeframe: str,
        decision_close_timestamp: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        latest_open = decision_close_timestamp - timeframe_seconds(timeframe)
        return self.history(symbol, timeframe, latest_open, limit)


def _frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    for column in ("timestamp", "open", "high", "low", "close", "volume"):
        if column not in frame.columns:
            raise ValueError(f"candle snapshot is missing {column}")
    frame["datetime"] = pd.to_datetime(frame["timestamp"], unit="s", utc=True)
    frame.set_index("datetime", inplace=True)
    return frame


def _initial_stats(initial_cash: float) -> dict[str, Any]:
    return {
        "trades": 0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "peak_equity": initial_cash,
        "max_drawdown_pct": 0.0,
        "healthy_evaluations": 0,
        "failed_evaluations": 0,
    }


def _metrics(
    stats: Mapping[str, Any],
    ledger: ShadowVirtualLedger,
    symbol: str,
    first_timestamp: int,
    last_timestamp: int,
) -> dict[str, Any]:
    equity = float(ledger.snapshot(symbol=symbol)["equity"])
    gross_profit = float(stats.get("gross_profit") or 0.0)
    gross_loss = float(stats.get("gross_loss") or 0.0)
    profit_factor = (
        gross_profit / gross_loss if gross_loss > 0 else (1000.0 if gross_profit > 0 else 0.0)
    )
    return {
        "forward_days": round(max(0, last_timestamp - first_timestamp) / 86_400.0, 4),
        "trades": int(stats.get("trades") or 0),
        "profit_factor": round(min(1000.0, profit_factor), 6),
        "net_return_pct": round((equity / ledger.initial_cash - 1.0) * 100.0, 6),
        "max_drawdown_pct": round(float(stats.get("max_drawdown_pct") or 0.0), 6),
        "equity": round(equity, 8),
        "fees": round(ledger.fees, 8),
    }


class ShadowEvaluator:
    def __init__(self, spec: dict[str, Any], runtime_dir: Path, db_path: Path) -> None:
        self.spec = spec
        self.runtime_dir = Path(runtime_dir)
        self.db_path = Path(db_path)
        self.symbol = str(spec["symbol"])
        self.shadow_dir = self.runtime_dir / "shadow" / self.symbol
        self.run_dir = self.shadow_dir / "runs" / str(spec["run_id"])
        self.events_path = self.run_dir / "events.jsonl"
        self.status_path = self.shadow_dir / "status.json"
        self.lock_path = self.shadow_dir / ".worker.lock"
        self.shadow_dir.mkdir(parents=True, exist_ok=True, mode=0o770)
        self.run_dir.mkdir(parents=True, exist_ok=True, mode=0o770)

    def _new_state(self) -> dict[str, Any]:
        initial_cash = float(self.spec["initial_cash"])
        return {
            "run_id": self.spec["run_id"],
            "spec_hash": self.spec["spec_hash"],
            "last_timestamp": 0,
            "first_timestamp": 0,
            "snapshot_count": 0,
            "last_event_sha256": "",
            "candidates": {
                item["candidate_id"]: {
                    "ledger": ShadowVirtualLedger(
                        item["candidate_id"], initial_cash=initial_cash
                    ).export_state(),
                    "stats": _initial_stats(initial_cash),
                }
                for item in self.spec["candidates"]
            },
        }

    def _load_state(self) -> dict[str, Any]:
        state = self._new_state()
        if not self.events_path.exists():
            return state
        data = self.events_path.read_bytes()
        lines = data.splitlines()
        complete_tail = data.endswith(b"\n")
        previous = ""
        for index, line in enumerate(lines):
            try:
                event = json.loads(line)
            except ValueError as exc:
                if index == len(lines) - 1 and not complete_tail:
                    break
                raise ValueError("shadow event log contains invalid JSON") from exc
            if not isinstance(event, dict) or event.get("schema_version") != EVENT_VERSION:
                raise ValueError("shadow event log contains an unsupported event")
            if (
                event.get("run_id") != self.spec["run_id"]
                or event.get("spec_hash") != self.spec["spec_hash"]
            ):
                raise ValueError("shadow event log identity mismatch")
            event_hash = str(event.get("event_sha256") or "")
            unsigned = {key: value for key, value in event.items() if key != "event_sha256"}
            if event.get("prev_event_sha256") != previous or _sha256(unsigned) != event_hash:
                raise ValueError("shadow event hash chain is invalid")
            candidate_state = event.get("state")
            if not isinstance(candidate_state, dict):
                raise ValueError("shadow event is missing durable state")
            state = dict(candidate_state)
            state["last_event_sha256"] = event_hash
            previous = event_hash
        if (
            state.get("run_id") != self.spec["run_id"]
            or state.get("spec_hash") != self.spec["spec_hash"]
        ):
            raise ValueError("shadow durable state identity mismatch")
        expected_candidates = {str(item["candidate_id"]) for item in self.spec["candidates"]}
        durable_candidates = state.get("candidates")
        if (
            not isinstance(durable_candidates, Mapping)
            or set(durable_candidates.keys()) != expected_candidates
        ):
            raise ValueError("shadow durable state candidate set mismatch")
        return state

    def _repair_partial_tail(self) -> None:
        if not self.events_path.exists():
            return
        with self.events_path.open("r+b") as handle:
            data = handle.read()
            if not data or data.endswith(b"\n"):
                return
            newline = data.rfind(b"\n")
            handle.truncate(newline + 1 if newline >= 0 else 0)
            handle.flush()
            os.fsync(handle.fileno())

    def _append_event(self, event: dict[str, Any]) -> str:
        self._repair_partial_tail()
        unsigned = {key: value for key, value in event.items() if key != "event_sha256"}
        event["event_sha256"] = _sha256(unsigned)
        payload = _json_bytes(event) + b"\n"
        fd = os.open(self.events_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o660)
        try:
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("shadow event append made no progress")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        directory_fd = os.open(self.run_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return str(event["event_sha256"])

    def _status(self, state: Mapping[str, Any], *, status: str, detail: str) -> dict[str, Any]:
        candidate_status: dict[str, Any] = {}
        first_timestamp = int(state.get("first_timestamp") or 0)
        last_timestamp = int(state.get("last_timestamp") or 0)
        for item in self.spec["candidates"]:
            candidate_id = str(item["candidate_id"])
            current = (state.get("candidates") or {}).get(candidate_id) or {}
            ledger = ShadowVirtualLedger.from_state(current.get("ledger") or {})
            stats = current.get("stats") or {}
            last_signal = current.get("last_signal") or {}
            candidate_status[candidate_id] = {
                "role": item["role"],
                "strategy_name": item["strategy_name"],
                "config_fingerprint": item["config_fingerprint"],
                "healthy": bool(last_signal.get("healthy", True)),
                "last_signal": last_signal,
                "metrics": _metrics(
                    stats,
                    ledger,
                    self.symbol,
                    first_timestamp,
                    last_timestamp,
                ),
            }
        return {
            "schema_version": STATUS_VERSION,
            "status": status,
            "research_only": True,
            "broker_access": False,
            "source": "tenant_sqlite_readonly",
            "tenant": self.spec["tenant"],
            "symbol": self.symbol,
            "venue": self.spec["venue"],
            "timeframe": self.spec["timeframe"],
            "run_id": self.spec["run_id"],
            "spec_hash": self.spec["spec_hash"],
            "fill_model": self.spec["fill_model"],
            "fees_pct": self.spec["fees_pct"],
            "slippage_pct": self.spec["slippage_pct"],
            "checked_at": time.time(),
            "last_timestamp": last_timestamp,
            "snapshot_count": int(state.get("snapshot_count") or 0),
            "artifact_event_sha256": str(state.get("last_event_sha256") or ""),
            "candidate_ids": [item["candidate_id"] for item in self.spec["candidates"]],
            "candidates": candidate_status,
            "detail": detail,
        }

    def _write_status(self, payload: dict[str, Any]) -> None:
        atomic_json_write(str(self.status_path), payload, indent=2, mode=0o660)

    def write_failure_status(self, exc: Exception) -> None:
        """Publish a bounded diagnostic without changing the event artifact."""
        try:
            state = self._load_state()
        except Exception:
            state = self._new_state()
        payload = self._status(
            state,
            status="degraded",
            detail=f"{type(exc).__name__}: {str(exc)[:240]}",
        )
        self._write_status(payload)

    @staticmethod
    def _fill_price(signal: Any, close: float, slippage_pct: float) -> float:
        rate = slippage_pct / 100.0
        action = str(getattr(signal, "action", "HOLD") or "HOLD").upper()
        if action == "BUY":
            return close * (1.0 + rate)
        if action == "SELL":
            return close * (1.0 - rate)
        return close

    def _evaluate_timestamp(
        self,
        source: SQLiteCandleSource,
        pool: ShadowRunnerPool,
        state: dict[str, Any],
        timestamp: int,
    ) -> dict[str, Any]:
        timeframe = str(self.spec["timeframe"])
        max_bars = int(self.spec["max_bars"])
        primary = source.history(self.symbol, timeframe, timestamp, max_bars)
        if not primary or int(primary[-1]["timestamp"]) != timestamp:
            raise ValueError("closed-candle snapshot is incomplete")
        decision_close = timestamp + timeframe_seconds(timeframe)
        regime_timeframe = str(self.spec.get("regime_timeframe") or "")
        regime = (
            source.regime_history(self.symbol, regime_timeframe, decision_close, max_bars)
            if regime_timeframe
            else []
        )
        frame = _frame(primary)
        regime_frame = _frame(regime) if regime else None
        close = _finite(primary[-1]["close"], "candle close", minimum=0.00000001)

        ledgers: dict[str, ShadowVirtualLedger] = {}
        contexts: dict[str, MarketContext] = {}
        for item in self.spec["candidates"]:
            candidate_id = str(item["candidate_id"])
            current = state["candidates"][candidate_id]
            ledger = ShadowVirtualLedger.from_state(current["ledger"])
            ledgers[candidate_id] = ledger
            contexts[candidate_id] = MarketContext(
                symbol=self.symbol,
                timeframe_primary=timeframe,
                df_primary=frame,
                current_price=close,
                has_position=ledger.position is not None,
                position_side=ledger.position.side if ledger.position else None,
                timeframe_regime=regime_timeframe or None,
                df_regime=regime_frame,
                config=dict(item["strategy_config"]),
                engine_config={"strategy": {"use_closed_candles": True}},
                extras={
                    "last_bar_is_forming": False,
                    "shadow_runtime": True,
                    "fill_model": self.spec["fill_model"],
                },
            )

        records = pool.run_with_contexts(contexts)
        candidate_events: dict[str, Any] = {}
        for record in records:
            candidate_id = record.candidate_id
            ledger = ledgers[candidate_id]
            current = state["candidates"][candidate_id]
            stats = dict(current.get("stats") or _initial_stats(ledger.initial_cash))
            before_realized = ledger.realized_pnl
            before_side = ledger.position.side if ledger.position else None
            before_equity = float(ledger.snapshot(symbol=self.symbol)["equity"])
            notional = max(0.0, before_equity * float(self.spec["allocation_fraction"]))
            fill_price = self._fill_price(record, close, float(self.spec["slippage_pct"]))
            snapshot = ledger.apply(
                record,
                symbol=self.symbol,
                price=fill_price,
                notional=notional,
                fee_pct=float(self.spec["fees_pct"]),
                timestamp=float(decision_close),
            )
            after_side = ledger.position.side if ledger.position else None
            realized_delta = ledger.realized_pnl - before_realized
            if before_side is not None and before_side != after_side:
                stats["trades"] = int(stats.get("trades") or 0) + 1
                if realized_delta >= 0:
                    stats["gross_profit"] = float(stats.get("gross_profit") or 0.0) + realized_delta
                else:
                    stats["gross_loss"] = float(stats.get("gross_loss") or 0.0) + abs(
                        realized_delta
                    )
            equity = float(snapshot["equity"])
            peak = max(float(stats.get("peak_equity") or ledger.initial_cash), equity)
            stats["peak_equity"] = peak
            drawdown = ((peak - equity) / peak * 100.0) if peak > 0 else 0.0
            stats["max_drawdown_pct"] = max(float(stats.get("max_drawdown_pct") or 0.0), drawdown)
            health_key = "healthy_evaluations" if record.healthy else "failed_evaluations"
            stats[health_key] = int(stats.get(health_key) or 0) + 1
            signal = record.to_dict()
            current.update(
                {
                    "ledger": ledger.export_state(),
                    "stats": stats,
                    "last_signal": signal,
                }
            )
            candidate_events[candidate_id] = {
                "signal": signal,
                "fill_price": fill_price,
                "ledger": snapshot,
                "metrics": _metrics(
                    stats,
                    ledger,
                    self.symbol,
                    int(state.get("first_timestamp") or timestamp),
                    timestamp,
                ),
            }

        state["last_timestamp"] = timestamp
        state["first_timestamp"] = int(state.get("first_timestamp") or timestamp)
        state["snapshot_count"] = int(state.get("snapshot_count") or 0) + 1
        market_snapshot = {
            "symbol": self.symbol,
            "timeframe": timeframe,
            "regime_timeframe": regime_timeframe,
            "decision_open_timestamp": timestamp,
            "decision_close_timestamp": decision_close,
            "primary": primary,
            "regime": regime,
        }
        durable_state = {key: value for key, value in state.items() if key != "last_event_sha256"}
        event = {
            "schema_version": EVENT_VERSION,
            "event": "closed_candle_evaluated",
            "run_id": self.spec["run_id"],
            "spec_hash": self.spec["spec_hash"],
            "prev_event_sha256": str(state.get("last_event_sha256") or ""),
            "snapshot_id": f"{self.symbol}:{timeframe}:{timestamp}",
            "snapshot_sha256": _sha256(market_snapshot),
            "evaluated_at": time.time(),
            "market_snapshot": market_snapshot,
            "candidates": candidate_events,
            "state": durable_state,
        }
        state["last_event_sha256"] = self._append_event(event)
        return state

    def run_once(self, *, now: float | None = None) -> dict[str, Any]:
        current_time = float(time.time() if now is None else now)
        self.lock_path.touch(mode=0o660, exist_ok=True)
        with self.lock_path.open("r+") as lock_handle:
            try:
                fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("another shadow worker holds the tenant lock") from exc
            state = self._load_state()
            source = SQLiteCandleSource(self.db_path)
            try:
                pool = ShadowRunnerPool(
                    [
                        ShadowCandidateSpec(
                            candidate_id=str(item["candidate_id"]),
                            strategy_name=str(item["strategy_name"]),
                            strategy_config=dict(item["strategy_config"]),
                        )
                        for item in self.spec["candidates"]
                    ],
                    strict=True,
                )
                pending = source.pending_timestamps(
                    self.symbol,
                    str(self.spec["timeframe"]),
                    int(state.get("last_timestamp") or 0),
                    current_time,
                )
                for timestamp in pending:
                    state = self._evaluate_timestamp(source, pool, state, timestamp)
                last_timestamp = int(state.get("last_timestamp") or 0)
                timeframe = str(self.spec["timeframe"])
                feed_stale = bool(
                    last_timestamp
                    and current_time - last_timestamp > 2.5 * timeframe_seconds(timeframe)
                )
                no_snapshot = int(state.get("snapshot_count") or 0) == 0
                if pending:
                    detail = f"evaluated {len(pending)} closed candle(s)"
                elif no_snapshot:
                    detail = "no closed candle is available for the prepared pair"
                elif feed_stale:
                    detail = "tenant candle feed is stale for the configured timeframe"
                else:
                    detail = "no new closed candle; durable state is current"
                all_healthy = all(
                    bool(
                        ((state.get("candidates") or {}).get(item["candidate_id"]) or {})
                        .get("last_signal", {})
                        .get("healthy", True)
                    )
                    for item in self.spec["candidates"]
                )
                status = self._status(
                    state,
                    status=(
                        "healthy"
                        if all_healthy and not no_snapshot and not feed_stale
                        else "degraded"
                    ),
                    detail=detail,
                )
                self._write_status(status)
                return status
            finally:
                source.close()


def run_worker(
    tenant: str,
    *,
    runtime_root: Path,
    spec_path: Path | None = None,
    db_path: Path | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    slug = validate_tenant_slug(tenant)
    root = Path(runtime_root).resolve()
    runtime_dir = (root / slug).resolve()
    if runtime_dir.parent != root or not runtime_dir.is_dir():
        raise ValueError("tenant runtime directory is unavailable")
    selected_spec = Path(spec_path) if spec_path else None
    if selected_spec is None:
        specs = sorted((runtime_dir / "shadow").glob("*/spec.json"))
        if len(specs) != 1:
            raise ValueError("worker requires exactly one prepared shadow pair")
        selected_spec = specs[0]
    selected_spec = selected_spec.resolve()
    try:
        selected_spec.relative_to(runtime_dir)
    except ValueError as exc:
        raise ValueError("shadow spec escaped the tenant runtime directory") from exc
    raw = json.loads(selected_spec.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("shadow spec must be an object")
    spec = _validate_spec(raw, slug)
    selected_db = (Path(db_path) if db_path else runtime_dir / "xauby.db").resolve()
    try:
        selected_db.relative_to(runtime_dir)
    except ValueError as exc:
        raise ValueError("shadow database escaped the tenant runtime directory") from exc
    evaluator = ShadowEvaluator(spec, runtime_dir, selected_db)
    try:
        return evaluator.run_once(now=now)
    except Exception as exc:
        evaluator.write_failure_status(exc)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the xAuby research-only shadow evaluator")
    parser.add_argument("--tenant", required=True, help="Hosted tenant slug")
    parser.add_argument(
        "--runtime-root",
        default=os.environ.get("XAUBY_TENANT_RUNTIME_ROOT", "/var/lib/xauby/runtime"),
    )
    parser.add_argument("--spec", default="", help="Optional tenant-contained spec path")
    parser.add_argument("--db", default="", help="Optional tenant-contained SQLite path")
    args = parser.parse_args(argv)
    try:
        status = run_worker(
            args.tenant,
            runtime_root=Path(args.runtime_root),
            spec_path=Path(args.spec) if args.spec else None,
            db_path=Path(args.db) if args.db else None,
        )
    except Exception as exc:
        print(f"[ERR] shadow evaluator failed closed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
