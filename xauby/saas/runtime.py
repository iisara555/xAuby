from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from xauby.saas.settings import SaaSSettings
from xauby.saas.supervisor import TenantSupervisor
from xauby.utils.currency import CurrencyRateUnavailable, get_thb_rate_quote

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,24}$")
_TIMEFRAMES = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}


class RuntimeGateway:
    """Tenant-scoped, read-only runtime data access for the browser console.

    Every tenant is read from its isolated runtime directory.
    """

    def __init__(
        self,
        settings: SaaSSettings,
        supervisor: TenantSupervisor,
    ) -> None:
        self.settings = settings
        self.supervisor = supervisor

    @staticmethod
    def _symbol(value: str) -> str:
        symbol = str(value or "").upper().replace("_", "").replace("/", "")
        if not _SYMBOL_RE.fullmatch(symbol):
            raise ValueError("invalid symbol")
        return symbol

    @staticmethod
    def _timeframe(value: str) -> str:
        timeframe = str(value or "4h").lower()
        if timeframe not in _TIMEFRAMES:
            raise ValueError("unsupported timeframe")
        return timeframe

    @staticmethod
    def _limit(value: int, *, default: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(1, min(parsed, maximum))

    def snapshot(self, slug: str) -> dict[str, Any]:
        path = self.supervisor.runtime_dir(slug) / "logs" / "xauby_bot_state.json"
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError("runtime state is not an object")
            age = max(0.0, time.time() - path.stat().st_mtime)
            return {
                "ok": True,
                "source": "tenant_engine",
                "read_only": False,
                "as_of": time.time(),
                "age_sec": round(age, 1),
                "stale": age > 120,
                "state": state,
                "currency": self._currency_from_state(
                    state,
                    runtime_dir=self.supervisor.runtime_dir(slug),
                ),
                "detail": {},
            }
        except (OSError, ValueError, json.JSONDecodeError):
            return self._unavailable("tenant_engine", read_only=False)

    def price(self, slug: str, *, symbol: str) -> dict[str, Any]:
        """Return a small, tenant-scoped live-price payload for dashboard polling."""
        clean_symbol = self._symbol(symbol)
        path = self.supervisor.runtime_dir(slug) / "logs" / "xauby_bot_state.json"
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError("runtime state is not an object")
            age = max(0.0, time.time() - path.stat().st_mtime)
            return self._price_from_state(
                state,
                clean_symbol,
                source="tenant_engine",
                read_only=False,
                age_sec=age,
                stale=age > 5.0,
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return {
                **self._unavailable("tenant_engine", read_only=False),
                "symbol": clean_symbol,
                "price": None,
                "bid": None,
                "ask": None,
            }

    def shadow_status(self, slug: str, *, symbol: str) -> dict[str, Any]:
        """Read the credential-free shadow worker status for one tenant pair."""
        clean_symbol = self._symbol(symbol)
        path = self.supervisor.runtime_dir(slug) / "shadow" / clean_symbol / "status.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(payload, dict)
                or payload.get("tenant") != slug
                or payload.get("symbol") != clean_symbol
            ):
                raise ValueError("invalid shadow status identity")
            if payload.get("research_only") is not True or payload.get("broker_access") is not False:
                raise ValueError("unsafe shadow capability status")
            checked_at = float(payload.get("checked_at") or 0.0)
            if not math.isfinite(checked_at) or checked_at <= 0:
                raise ValueError("shadow status has no heartbeat")
            current_time = time.time()
            if checked_at > current_time + 300:
                raise ValueError("shadow status heartbeat is in the future")
            age = max(0.0, current_time - checked_at)
            raw_status = str(payload.get("status") or "not_connected")
            if raw_status not in {"prepared", "healthy", "stale", "degraded"}:
                raise ValueError("invalid shadow worker status")
            status = "stale" if raw_status == "healthy" and age > 900 else raw_status
            raw_candidate_ids = payload.get("candidate_ids")
            raw_candidates = payload.get("candidates")
            if not isinstance(raw_candidate_ids, list) or len(raw_candidate_ids) != 2:
                raise ValueError("invalid shadow candidate ids")
            if not isinstance(raw_candidates, dict):
                raise ValueError("invalid shadow candidate status")
            candidate_ids = [
                value
                for value in raw_candidate_ids
                if isinstance(value, str) and 0 < len(value) <= 128
            ]
            if len(candidate_ids) != len(raw_candidate_ids) or len(set(candidate_ids)) != len(
                candidate_ids
            ):
                raise ValueError("invalid shadow candidate ids")
            if raw_status != "prepared" and set(raw_candidates) != set(candidate_ids):
                raise ValueError("shadow candidate status is incomplete")
            snapshot_count = int(payload.get("snapshot_count") or 0)
            last_timestamp = int(payload.get("last_timestamp") or 0)
            if snapshot_count < 0 or last_timestamp < 0:
                raise ValueError("invalid shadow progress counters")

            def safe_number(value: Any) -> float | None:
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    return None
                return number if math.isfinite(number) else None

            candidates: dict[str, Any] = {}
            for candidate_id, item in raw_candidates.items():
                if candidate_id not in candidate_ids or not isinstance(item, dict):
                    continue
                signal = item.get("last_signal") if isinstance(item.get("last_signal"), dict) else {}
                metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
                candidates[candidate_id] = {
                    "role": str(item.get("role") or "")[:16],
                    "strategy_name": str(item.get("strategy_name") or "")[:128],
                    "config_fingerprint": str(item.get("config_fingerprint") or "")[:64],
                    "healthy": bool(item.get("healthy")),
                    "last_signal": {
                        "action": str(signal.get("action") or "")[:16],
                        "intent": str(signal.get("intent") or "")[:16],
                        "position_side": (
                            str(signal.get("position_side"))[:16]
                            if signal.get("position_side") is not None
                            else None
                        ),
                        "confidence": safe_number(signal.get("confidence")),
                        "reason": str(signal.get("reason") or "")[:240],
                        "duration_ms": safe_number(signal.get("duration_ms")),
                        "healthy": bool(signal.get("healthy", True)),
                    },
                    "metrics": {
                        key: safe_number(metrics.get(key))
                        for key in (
                            "forward_days",
                            "trades",
                            "profit_factor",
                            "net_return_pct",
                            "max_drawdown_pct",
                            "equity",
                            "fees",
                        )
                    },
                }
            return {
                "ok": status in {"healthy", "prepared"},
                "source": "shadow_worker",
                "read_only": True,
                "research_only": True,
                "broker_access": False,
                "status": status,
                "as_of": checked_at,
                "age_sec": round(age, 1),
                "stale": status == "stale",
                "run_id": str(payload.get("run_id") or "")[:160],
                "spec_hash": str(payload.get("spec_hash") or "")[:64],
                "snapshot_count": snapshot_count,
                "last_timestamp": last_timestamp,
                "fill_model": str(payload.get("fill_model") or "")[:80],
                "candidate_ids": candidate_ids,
                "candidates": candidates,
                "detail": str(payload.get("detail") or "")[:240],
            }
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return {
                "ok": False,
                "source": "shadow_worker",
                "read_only": True,
                "research_only": True,
                "broker_access": False,
                "status": "not_connected",
                "as_of": None,
                "age_sec": None,
                "stale": True,
                "run_id": "",
                "spec_hash": "",
                "snapshot_count": 0,
                "last_timestamp": 0,
                "fill_model": "",
                "candidate_ids": [],
                "candidates": {},
                "detail": "shadow worker status is unavailable",
            }

    @classmethod
    def _price_from_state(
        cls,
        state: dict[str, Any],
        symbol: str,
        *,
        source: str,
        read_only: bool,
        age_sec: float,
        stale: bool,
    ) -> dict[str, Any]:
        focus = cls._focus_state(state, symbol)
        price = focus.get("current_price") or (focus.get("position") or {}).get("mark_price")
        return {
            "ok": price is not None,
            "source": source,
            "read_only": read_only,
            "as_of": time.time(),
            "age_sec": round(max(0.0, age_sec), 1),
            "stale": bool(stale),
            "symbol": symbol,
            "price": price,
            "bid": focus.get("bid"),
            "ask": focus.get("ask"),
            "timestamp": focus.get("timestamp") or state.get("timestamp"),
        }

    def _currency_from_state(
        self,
        state: dict[str, Any],
        *,
        runtime_dir: Path | None = None,
    ) -> dict[str, Any]:
        focus_symbol = str(state.get("focus_symbol") or state.get("symbol") or "")
        focus = self._focus_state(state, focus_symbol)
        breakdown = focus.get("equity_breakdown") if isinstance(focus.get("equity_breakdown"), dict) else {}
        portfolio = focus.get("portfolio") if isinstance(focus.get("portfolio"), dict) else {}
        equity = breakdown.get("portfolio_total_usdt") or focus.get("total_equity_usdt") or state.get("total_equity_usdt")
        cash = breakdown.get("usdt_balance_usdt") or portfolio.get("USDT") or portfolio.get("USD")
        try:
            rate_path = (
                runtime_dir / "usd_thb_rate.json"
                if runtime_dir is not None
                else self.settings.project_root / "core" / "usd_thb_rate.json"
            )
            rate_quote = get_thb_rate_quote(cache_file=str(rate_path))
        except CurrencyRateUnavailable:
            rate_quote = {}
        rate_value = rate_quote.get("rate")

        def convert(value: Any) -> float | None:
            try:
                amount = float(value)
            except (TypeError, ValueError):
                return None
            return amount * float(rate_value) if rate_value else None

        pnl = breakdown.get("unrealized_pnl_usdt")
        exposure = breakdown.get("symbol_exposure_usdt")
        return {
            "equity_usdt": equity,
            "equity_thb": convert(equity),
            "usd_thb_rate": rate_value,
            "rate_pair": rate_quote.get("pair"),
            "rate_source": rate_quote.get("source"),
            "rate_source_label": rate_quote.get("source_label"),
            "rate_source_url": rate_quote.get("source_url"),
            "rate_observed_at": rate_quote.get("observed_at"),
            "rate_fetched_at": rate_quote.get("fetched_at"),
            "rate_age_sec": rate_quote.get("age_sec"),
            "rate_stale": bool(rate_quote.get("stale")) if rate_quote else True,
            "usdt_balance_usdt": cash,
            "usdt_balance_thb": convert(cash),
            "base_asset": breakdown.get("base_asset") or "",
            "base_quantity": breakdown.get("base_quantity"),
            "base_value_usdt": breakdown.get("base_value_usdt"),
            "unrealized_pnl_usdt": pnl,
            "unrealized_pnl_thb": convert(pnl),
            "symbol_exposure_usdt": exposure,
            "symbol_exposure_thb": convert(exposure),
        }

    def candles(self, slug: str, *, symbol: str, timeframe: str, limit: int) -> dict[str, Any]:
        clean_symbol = self._symbol(symbol)
        clean_timeframe = self._timeframe(timeframe)
        # Strategy charts need enough warm-up history for their longest
        # indicator (the certified BTC preset uses EMA200 over up to 420 bars).
        clean_limit = self._limit(limit, default=48, maximum=500)
        return self._native_candles(slug, clean_symbol, clean_timeframe, clean_limit)

    def activity(self, slug: str, *, symbol: str, limit: int) -> dict[str, Any]:
        clean_symbol = self._symbol(symbol)
        clean_limit = self._limit(limit, default=30, maximum=100)
        state = self.snapshot(slug)
        focus = self._focus_state(state.get("state") or {}, clean_symbol)
        events = focus.get("recent_events") if isinstance(focus.get("recent_events"), list) else []
        return {
            "ok": bool(state.get("ok")),
            "source": "tenant_engine",
            "read_only": False,
            "as_of": time.time(),
            "events": events[-clean_limit:],
            "trades": self._native_trades(slug, clean_symbol, clean_limit),
        }

    def trades(
        self,
        slug: str,
        *,
        symbol: str | None = None,
        outcome: str | None = None,
        limit: int = 50,
        cursor: int | None = None,
    ) -> dict[str, Any]:
        clean_symbol = self._symbol(symbol) if symbol else None
        clean_outcome = str(outcome or "all").lower()
        if clean_outcome not in {"all", "win", "loss", "breakeven"}:
            raise ValueError("unsupported outcome")
        clean_limit = self._limit(limit, default=50, maximum=100)
        return self._native_trade_log(slug, clean_symbol, clean_outcome, clean_limit, cursor)

    @staticmethod
    def _unavailable(source: str, *, read_only: bool) -> dict[str, Any]:
        return {
            "ok": False,
            "source": source,
            "read_only": read_only,
            "as_of": time.time(),
            "age_sec": None,
            "stale": True,
            "state": {},
            "currency": {},
            "detail": {},
            "error": "runtime data is temporarily unavailable",
        }

    @staticmethod
    def _focus_state(state: dict[str, Any], symbol: str) -> dict[str, Any]:
        by_symbol = state.get("by_symbol")
        if isinstance(by_symbol, dict):
            # Once the engine publishes pair-scoped state, falling back to the
            # root snapshot for an unknown pair shows another market's price,
            # events, or equity. Return an empty view instead; callers then
            # fail closed until that pair has published its own state.
            return by_symbol[symbol] if isinstance(by_symbol.get(symbol), dict) else {}
        return state

    def _native_db(self, slug: str) -> Path:
        return self.supervisor.runtime_dir(slug) / "xauby.db"

    def _native_candles(
        self, slug: str, symbol: str, timeframe: str, limit: int
    ) -> dict[str, Any]:
        path = self._native_db(slug)
        if not path.exists():
            return {**self._unavailable("tenant_engine", read_only=False), "candles": []}
        try:
            conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                # Compute indicators with the engine's normal 420-bar window,
                # then return only the number of candles the caller requested.
                history_limit = max(limit, 420)
                rows = conn.execute(
                    "SELECT timestamp,open,high,low,close,volume FROM prices "
                    "WHERE symbol=? AND timeframe=? ORDER BY timestamp DESC LIMIT ?",
                    (symbol, timeframe, history_limit),
                ).fetchall()
            finally:
                conn.close()
            candles = [dict(row) for row in reversed(rows)]
            strategy_name, chart, candles = self._strategy_chart_payload(
                slug, symbol, candles
            )
            candles = candles[-limit:]
            return {
                "ok": True,
                "source": "tenant_engine",
                "read_only": False,
                "symbol": symbol,
                "timeframe": timeframe,
                "strategy_name": strategy_name,
                "chart": chart,
                "candles": candles,
            }
        except sqlite3.Error:
            return {**self._unavailable("tenant_engine", read_only=False), "candles": []}

    def _strategy_chart_payload(
        self,
        slug: str,
        symbol: str,
        candles: list[dict[str, Any]],
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        """Enrich candle rows using the same indicator registry as the engine."""
        if not candles:
            return "", {}, candles
        try:
            import math

            import pandas as pd
            import yaml

            from xauby.runtime.trading_config import strategy_name_for_symbol
            from xauby.ui.chart_registry import chart_display_metadata, compute_chart_dataframe

            config_path = self.supervisor.config_dir(slug) / "bot_config.yaml"
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            if not isinstance(cfg, dict):
                cfg = {}

            strategy_name = ""
            state_path = self.supervisor.runtime_dir(slug) / "logs" / "xauby_bot_state.json"
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                focus = self._focus_state(state if isinstance(state, dict) else {}, symbol)
                signal_meta = focus.get("signal_meta") if isinstance(focus.get("signal_meta"), dict) else {}
                strategy_name = str(
                    signal_meta.get("strategy_name") or focus.get("strategy_name") or ""
                )
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            if not strategy_name:
                strategy_name = strategy_name_for_symbol(cfg, symbol)

            frame = compute_chart_dataframe(
                pd.DataFrame(candles),
                symbol,
                strategy_name=strategy_name,
                config=cfg,
            )
            metadata = chart_display_metadata(strategy_name, cfg)
            line_keys = [
                str(item.get("key"))
                for item in metadata.get("lines") or []
                if isinstance(item, dict) and item.get("key")
            ]
            zone_key = str(metadata.get("zone_column") or "")
            export_keys = set(line_keys)
            if zone_key:
                export_keys.add(zone_key)

            def json_value(value: Any) -> Any:
                if value is None or value is pd.NA:
                    return None
                if hasattr(value, "item"):
                    value = value.item()
                if isinstance(value, float) and not math.isfinite(value):
                    return None
                return value

            enriched: list[dict[str, Any]] = []
            for index, candle in enumerate(candles):
                item = dict(candle)
                for key in export_keys:
                    if key in frame.columns:
                        item[key] = json_value(frame.iloc[index][key])
                enriched.append(item)

            # Tuples are valid JSON through FastAPI, but lists make the payload
            # explicit and simpler for non-FastAPI callers and tests.
            for group in ("zones", "lines"):
                for item in metadata.get(group) or []:
                    if isinstance(item, dict) and isinstance(item.get("color"), tuple):
                        item["color"] = list(item["color"])
                    if isinstance(item, dict) and isinstance(item.get("bg_color"), tuple):
                        item["bg_color"] = list(item["bg_color"])
            return strategy_name, metadata, enriched
        except Exception:
            # Chart decoration is read-only presentation. Raw candles remain
            # available if a plugin or a tenant config is temporarily invalid.
            return "", {}, candles

    def _native_trades(self, slug: str, symbol: str, limit: int) -> list[dict[str, Any]]:
        path = self._native_db(slug)
        if not path.exists():
            return []
        try:
            conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT * FROM closed_trades WHERE symbol=? ORDER BY closed_at DESC LIMIT ?",
                    (symbol, limit),
                ).fetchall()
            finally:
                conn.close()
            return [dict(row) for row in rows]
        except sqlite3.Error:
            return []

    @staticmethod
    def _filter_trade_outcome(items: list[dict[str, Any]], outcome: str) -> list[dict[str, Any]]:
        if outcome == "all":
            return items
        def matches(item: dict[str, Any]) -> bool:
            pnl = float(item.get("net_pnl") or 0.0)
            return (outcome == "win" and pnl > 0) or (outcome == "loss" and pnl < 0) or (outcome == "breakeven" and pnl == 0)
        return [item for item in items if matches(item)]

    @staticmethod
    def _trade_response(
        items: list[dict[str, Any]], *, source: str, read_only: bool, next_cursor: int | None = None
    ) -> dict[str, Any]:
        pnls = [float(item.get("net_pnl") or 0.0) for item in items]
        wins = sum(1 for pnl in pnls if pnl > 0)
        losses = sum(1 for pnl in pnls if pnl < 0)
        return {
            "ok": True,
            "source": source,
            "read_only": read_only,
            "as_of": time.time(),
            "items": items,
            "next_cursor": next_cursor,
            "summary": {
                "total": len(items),
                "wins": wins,
                "losses": losses,
                "breakeven": len(items) - wins - losses,
                "net_pnl": sum(pnls),
                "fees": sum(float(item.get("total_fees") or 0.0) for item in items),
                "win_rate": (wins / (wins + losses) * 100.0) if wins + losses else 0.0,
            },
        }

    def _native_trade_log(
        self,
        slug: str,
        symbol: str | None,
        outcome: str,
        limit: int,
        cursor: int | None,
    ) -> dict[str, Any]:
        path = self._native_db(slug)
        if not path.exists():
            return {**self._unavailable("tenant_engine", read_only=False), "items": [], "summary": {}}
        clauses: list[str] = []
        params: list[Any] = []
        if symbol:
            clauses.append("symbol=?")
            params.append(symbol)
        if cursor is not None:
            clauses.append("id<?")
            params.append(max(1, int(cursor)))
        if outcome == "win":
            clauses.append("net_pnl>0")
        elif outcome == "loss":
            clauses.append("net_pnl<0")
        elif outcome == "breakeven":
            clauses.append("net_pnl=0")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        try:
            conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    f"SELECT * FROM closed_trades {where} ORDER BY id DESC LIMIT ?",
                    (*params, limit + 1),
                ).fetchall()
            finally:
                conn.close()
            has_more = len(rows) > limit
            items = [dict(row) for row in rows[:limit]]
            next_cursor = int(items[-1]["id"]) if has_more and items else None
            return self._trade_response(
                items, source="tenant_engine", read_only=False, next_cursor=next_cursor
            )
        except (sqlite3.Error, ValueError):
            return {**self._unavailable("tenant_engine", read_only=False), "items": [], "summary": {}}
