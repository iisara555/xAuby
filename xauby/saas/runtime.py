from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

import requests

from xauby.saas.settings import SaaSSettings
from xauby.saas.supervisor import TenantSupervisor

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,24}$")
_TIMEFRAMES = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"}
_MAX_RESPONSE_BYTES = 2_000_000


class RuntimeGateway:
    """Tenant-scoped, read-only runtime data access for the browser console.

    A pilot owner may temporarily remain on the legacy engine. In that case the
    gateway reads the private WebUI API with a server-only bearer token. Other
    tenants are always read from their isolated runtime directory.
    """

    def __init__(
        self,
        settings: SaaSSettings,
        supervisor: TenantSupervisor,
        *,
        http_get: Callable[..., Any] = requests.get,
    ) -> None:
        self.settings = settings
        self.supervisor = supervisor
        self.http_get = http_get

    def uses_legacy(self, slug: str) -> bool:
        return bool(
            self.settings.legacy_owner_slug
            and slug == self.settings.legacy_owner_slug
            and self.settings.legacy_webui_url
            and self.settings.legacy_webui_token
        )

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

    def _legacy_get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not path.startswith("/api/") or ".." in path:
            raise ValueError("legacy path is not allowed")
        response = self.http_get(
            urljoin(f"{self.settings.legacy_webui_url}/", path.lstrip("/")),
            params=params or {},
            headers={"Authorization": f"Bearer {self.settings.legacy_webui_token}"},
            timeout=(1.0, self.settings.legacy_webui_timeout_seconds),
            allow_redirects=False,
            stream=True,
        )
        if response.status_code != 200:
            raise RuntimeError(f"legacy WebUI returned HTTP {response.status_code}")
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            size += len(chunk)
            if size > _MAX_RESPONSE_BYTES:
                raise RuntimeError("legacy WebUI response is too large")
            chunks.append(chunk)
        payload = json.loads(b"".join(chunks).decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("legacy WebUI response is not an object")
        return payload

    def snapshot(self, slug: str) -> dict[str, Any]:
        if self.uses_legacy(slug):
            try:
                state = self._legacy_get("/api/state")
                detail = self._legacy_get("/api/dashboard-detail")
                return {
                    "ok": bool(state.get("ok")),
                    "source": "legacy_webui",
                    "read_only": True,
                    "as_of": time.time(),
                    "age_sec": state.get("age_sec"),
                    "stale": bool(state.get("stale")),
                    "state": state.get("state") if isinstance(state.get("state"), dict) else {},
                    "currency": state.get("currency") if isinstance(state.get("currency"), dict) else {},
                    "detail": detail,
                }
            except (OSError, ValueError, RuntimeError, requests.RequestException, json.JSONDecodeError):
                return self._unavailable("legacy_webui", read_only=True)

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
                "currency": {},
                "detail": {},
            }
        except (OSError, ValueError, json.JSONDecodeError):
            return self._unavailable("tenant_engine", read_only=False)

    def candles(self, slug: str, *, symbol: str, timeframe: str, limit: int) -> dict[str, Any]:
        clean_symbol = self._symbol(symbol)
        clean_timeframe = self._timeframe(timeframe)
        clean_limit = self._limit(limit, default=48, maximum=120)
        if self.uses_legacy(slug):
            try:
                payload = self._legacy_get(
                    "/api/candles",
                    params={"symbol": clean_symbol, "timeframe": clean_timeframe, "limit": clean_limit},
                )
                payload.update({"source": "legacy_webui", "read_only": True})
                return payload
            except (OSError, ValueError, RuntimeError, requests.RequestException, json.JSONDecodeError):
                return {**self._unavailable("legacy_webui", read_only=True), "candles": []}
        return self._native_candles(slug, clean_symbol, clean_timeframe, clean_limit)

    def activity(self, slug: str, *, symbol: str, limit: int) -> dict[str, Any]:
        clean_symbol = self._symbol(symbol)
        clean_limit = self._limit(limit, default=30, maximum=100)
        if self.uses_legacy(slug):
            try:
                events = self._legacy_get("/api/recent-events")
                trades = self._legacy_get(
                    "/api/trades", params={"symbol": clean_symbol, "limit": clean_limit}
                )
                return {
                    "ok": bool(events.get("ok") or trades.get("ok")),
                    "source": "legacy_webui",
                    "read_only": True,
                    "as_of": time.time(),
                    "events": list(events.get("events") or [])[-clean_limit:],
                    "trades": list(trades.get("trades") or [])[:clean_limit],
                }
            except (OSError, ValueError, RuntimeError, requests.RequestException, json.JSONDecodeError):
                return {
                    **self._unavailable("legacy_webui", read_only=True),
                    "events": [],
                    "trades": [],
                }
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
        if isinstance(by_symbol, dict) and isinstance(by_symbol.get(symbol), dict):
            return by_symbol[symbol]
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
                rows = conn.execute(
                    "SELECT timestamp,open,high,low,close,volume FROM prices "
                    "WHERE symbol=? AND timeframe=? ORDER BY timestamp DESC LIMIT ?",
                    (symbol, timeframe, limit),
                ).fetchall()
            finally:
                conn.close()
            return {
                "ok": True,
                "source": "tenant_engine",
                "read_only": False,
                "symbol": symbol,
                "timeframe": timeframe,
                "candles": [dict(row) for row in reversed(rows)],
            }
        except sqlite3.Error:
            return {**self._unavailable("tenant_engine", read_only=False), "candles": []}

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
