"""Small read-only WebUI server backed by runtime state files.

The server intentionally uses only the Python standard library. It is designed
to bind to localhost on the VPS and be reached through SSH tunnel or a private
overlay network such as Tailscale.
"""
from __future__ import annotations

import argparse
import base64
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import sqlite3
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from xauby.meta import load_bot_display_name, load_webui_avatar
from xauby.observability.health import HealthMonitor
from xauby.runtime.paths import bot_state_path, db_path as runtime_db_path, usd_thb_rate_path
from xauby.strategies.cdc_action_zone.indicators import classify_zone


STATIC_ROOT = Path(__file__).resolve().parent / "static"
_SENSITIVE_JSON_KEY_RE = re.compile(
    r"(api[_-]?key|api[_-]?secret|secret|token|password|passphrase|private[_-]?key|"
    r"access[_-]?key|client[_-]?secret|webhook)",
    re.IGNORECASE,
)


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _is_loopback_host(host: str) -> bool:
    normalized = str(host or "").strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            key_text = str(key)
            redacted[key_text] = (
                "[REDACTED]" if _SENSITIVE_JSON_KEY_RE.search(key_text) else _redact_sensitive(item)
            )
        return redacted
    if isinstance(value, (list, tuple)):
        return [_redact_sensitive(item) for item in value]
    return value


def _webui_auth_from_env() -> tuple[str, str, str]:
    username = os.environ.get("XAUBY_WEBUI_USERNAME", "xauby")
    password = os.environ.get("XAUBY_WEBUI_PASSWORD", "")
    bearer_token = os.environ.get("XAUBY_WEBUI_TOKEN", "")
    return username, password, bearer_token


def _validate_bind_security(host: str, username: str, password: str, bearer_token: str) -> None:
    if _is_loopback_host(host):
        return
    if password or bearer_token or _truthy_env("XAUBY_WEBUI_ALLOW_UNAUTH_REMOTE"):
        return
    raise ValueError(
        "Refusing to bind xAuby WebUI to a non-loopback host without auth. "
        "Set XAUBY_WEBUI_PASSWORD or XAUBY_WEBUI_TOKEN, or bind to 127.0.0.1."
    )


def _ema_sma_seeded(values: list[float], length: int) -> list[Optional[float]]:
    """pandas_ta-compatible EMA: SMA seed, then recursive EMA."""
    if length <= 0:
        return [None for _ in values]
    out: list[Optional[float]] = [None for _ in values]
    if len(values) < length:
        return out
    seed = sum(values[:length]) / length
    out[length - 1] = seed
    alpha = 2.0 / (length + 1.0)
    prev = seed
    for idx in range(length, len(values)):
        prev = values[idx] * alpha + prev * (1.0 - alpha)
        out[idx] = prev
    return out


def _cdc_ap_smoothing(project_root: str) -> int:
    config_path = _project_path(project_root, "bot_config.yaml")
    try:
        import yaml

        with open(config_path, "r", encoding="utf-8") as handle:
            cfg = yaml.safe_load(handle) or {}
        raw = ((cfg.get("strategies") or {}).get("cdc_action_zone") or {}).get("ap_smoothing", 1)
        return max(1, int(raw or 1))
    except Exception:
        return 1


def _with_cdc_indicators(
    rows: list[Dict[str, Any]],
    project_root: str,
) -> list[Dict[str, Any]]:
    if not rows:
        return rows
    closes: list[float] = []
    for row in rows:
        try:
            closes.append(float(row.get("close") or 0.0))
        except (TypeError, ValueError):
            closes.append(0.0)

    ap_smoothing = _cdc_ap_smoothing(project_root)
    ap_values: list[Optional[float]]
    if ap_smoothing >= 2:
        ap_values = _ema_sma_seeded(closes, ap_smoothing)
        ap_source = [
            float(ap) if ap is not None else close
            for ap, close in zip(ap_values, closes)
        ]
    else:
        ap_values = list(closes)
        ap_source = list(closes)

    ema12 = _ema_sma_seeded(ap_source, 12)
    ema26 = _ema_sma_seeded(ap_source, 26)
    out: list[Dict[str, Any]] = []
    for idx, row in enumerate(rows):
        enriched = dict(row)
        ap = ap_values[idx]
        fast = ema12[idx]
        slow = ema26[idx]
        close = closes[idx]
        enriched["ap"] = ap if ap is not None else close
        enriched["ema12"] = fast
        enriched["ema26"] = slow
        enriched["zone"] = (
            classify_zone(float(enriched["ap"]), float(fast), float(slow))
            if fast is not None and slow is not None
            else "UNKNOWN"
        )
        out.append(enriched)
    return out


def _project_path(project_root: str, path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(project_root, path)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _read_json(path: str) -> tuple[Optional[Dict[str, Any]], str]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data, ""
        return None, "state JSON root is not an object"
    except FileNotFoundError:
        return None, "state file not found"
    except json.JSONDecodeError as exc:
        return None, f"state file is invalid JSON: {exc}"
    except OSError as exc:
        return None, str(exc)


def load_state(project_root: str) -> Dict[str, Any]:
    path = _project_path(project_root, bot_state_path())
    data, error = _read_json(path)
    if data is None:
        return {
            "ok": False,
            "state": {},
            "path": path,
            "age_sec": None,
            "error": error,
        }
    try:
        age = round(time.time() - os.path.getmtime(path), 1)
    except OSError:
        age = None
    return {
        "ok": True,
        "state": data,
        "path": path,
        "age_sec": age,
        "stale": bool(age is not None and age > 120),
        "currency": _state_currency_payload(data, project_root),
    }


def _focus_snapshot(state: Dict[str, Any]) -> Dict[str, Any]:
    by_symbol = state.get("by_symbol") or {}
    if isinstance(by_symbol, dict) and by_symbol:
        focus = str(state.get("focus_symbol") or state.get("symbol") or "").upper().replace("_", "")
        if focus and isinstance(by_symbol.get(focus), dict):
            return by_symbol[focus]
        first = next(iter(by_symbol.values()))
        return first if isinstance(first, dict) else state
    return state


def _state_currency_payload(state: Dict[str, Any], project_root: str) -> Dict[str, Any]:
    focus = _focus_snapshot(state)
    equity = (
        focus.get("total_equity_usdt")
        or state.get("total_equity_usdt")
        or (state.get("aggregate") or {}).get("total_equity_usdt")
        or 0
    )
    try:
        equity_usdt = float(equity or 0)
    except (TypeError, ValueError):
        equity_usdt = 0.0
    rate_path = _project_path(project_root, usd_thb_rate_path())
    try:
        with open(rate_path, "r", encoding="utf-8") as handle:
            rate_data = json.load(handle)
        rate = float(rate_data.get("rate") or 0)
        if rate <= 0:
            raise ValueError("invalid cached rate")
        return {
            "usd_thb_rate": rate,
            "equity_thb": equity_usdt * rate,
            "rate_path": rate_path,
        }
    except Exception as exc:
        return {
            "usd_thb_rate": None,
            "equity_thb": None,
            "currency_error": exc.__class__.__name__,
            "rate_path": rate_path,
        }


def meta_payload(project_root: str) -> Dict[str, Any]:
    return {
        "ok": True,
        "display_name": load_bot_display_name(project_root),
        "avatar_url": load_webui_avatar(project_root),
    }


def recent_events_payload(project_root: str) -> Dict[str, Any]:
    state_payload = load_state(project_root)
    if not state_payload["ok"]:
        return {**state_payload, "events": []}
    state = state_payload["state"]
    focus = _focus_snapshot(state)
    events = focus.get("recent_events") or state.get("recent_events") or []
    if not isinstance(events, list):
        events = []
    return {"ok": True, "events": events[-40:], "age_sec": state_payload.get("age_sec")}


def health_payload(project_root: str) -> Dict[str, Any]:
    monitor = HealthMonitor(project_root=project_root)
    resources = monitor.get_server_resources()
    process = monitor.check_process_status()
    event_store = monitor.check_event_store()
    logs = monitor.scan_recent_logs()
    # Counts only over HTTP: raw log lines can carry exchange error bodies,
    # filesystem paths, and order details the dashboard never renders.
    logs = {k: v for k, v in logs.items() if k not in ("errors_found", "warnings_found")}
    anomalies = []
    disk = (resources.get("disk") or {}) if isinstance(resources, dict) else {}
    if float(disk.get("pct_used") or 0.0) > 90.0:
        anomalies.append(f"Disk space warning: {disk.get('pct_used')}% used")
    mem = (resources.get("memory") or {}) if isinstance(resources, dict) else {}
    if float(mem.get("pct_used") or 0.0) > 90.0:
        anomalies.append(f"RAM warning: {mem.get('pct_used')}% used")
    if process.get("status") == "OFFLINE":
        anomalies.append("Engine is offline")
    elif process.get("ws_stale"):
        anomalies.append(f"State file stale ({process.get('state_file_age_sec')}s)")
    if int(logs.get("errors_count") or 0) > 0:
        anomalies.append(f"Recent log errors: {logs.get('errors_count')}")
    return {
        "ok": not anomalies,
        "status": "OK" if not anomalies else "WARN",
        "timestamp": time.time(),
        "resources": resources,
        "process_status": process,
        "event_store": event_store,
        "log_scan": logs,
        "anomalies": anomalies,
    }


def trades_payload(project_root: str, limit: int = 20, symbol: str = "") -> Dict[str, Any]:
    db_path = _project_path(project_root, runtime_db_path())
    limit = max(1, min(int(limit or 20), 100))
    if not os.path.exists(db_path):
        return {"ok": False, "trades": [], "error": "database not found", "path": db_path}
    uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            if symbol:
                cur.execute(
                    "SELECT * FROM closed_trades WHERE symbol = ? ORDER BY closed_at DESC LIMIT ?",
                    (symbol.upper().replace("_", ""), limit),
                )
            else:
                cur.execute("SELECT * FROM closed_trades ORDER BY closed_at DESC LIMIT ?", (limit,))
            rows = [dict(row) for row in cur.fetchall()]
            return {"ok": True, "trades": rows, "limit": limit}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {"ok": False, "trades": [], "error": str(exc), "path": db_path}


def candles_payload(
    project_root: str,
    symbol: str,
    timeframe: str = "4h",
    limit: int = 24,
) -> Dict[str, Any]:
    db_path = _project_path(project_root, runtime_db_path())
    limit = max(1, min(int(limit or 24), 80))
    warmup_limit = min(max(limit + 200, 240), 600)
    symbol = str(symbol or "").upper().replace("_", "")
    timeframe = str(timeframe or "4h").lower()
    if not os.path.exists(db_path):
        return {"ok": False, "candles": [], "error": "database not found", "path": db_path}
    if not symbol:
        return {"ok": False, "candles": [], "error": "symbol is required", "path": db_path}
    uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT timestamp, open, high, low, close, volume
                FROM prices
                WHERE symbol = ? AND timeframe = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (symbol, timeframe, warmup_limit),
            )
            rows = [dict(row) for row in cur.fetchall()]
            rows.reverse()
            enriched = _with_cdc_indicators(rows, project_root)
            candles = enriched[-limit:]
            return {
                "ok": True,
                "symbol": symbol,
                "timeframe": timeframe,
                "limit": limit,
                "warmup": len(rows),
                "candles": candles,
            }
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {"ok": False, "candles": [], "error": str(exc), "path": db_path}


class XAubyWebUIHandler(BaseHTTPRequestHandler):
    project_root = os.getcwd()
    static_root = STATIC_ROOT

    def log_message(self, fmt: str, *args: Any) -> None:
        if os.environ.get("XAUBY_WEBUI_ACCESS_LOG", "").lower() in {"1", "true", "yes"}:
            super().log_message(fmt, *args)

    def _send_json(self, payload: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(_json_safe(_redact_sensitive(payload)), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._send_security_headers()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "base-uri 'none'; "
            "frame-ancestors 'none'",
        )

    def _is_authorized(self) -> bool:
        password = getattr(self, "basic_password", "")
        bearer_token = getattr(self, "bearer_token", "")
        if not password and not bearer_token:
            return True

        header = self.headers.get("Authorization", "")
        if bearer_token and header.startswith("Bearer "):
            supplied = header[len("Bearer "):].strip()
            return hmac.compare_digest(supplied, bearer_token)

        if password and header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[len("Basic "):].strip(), validate=True).decode("utf-8")
                supplied_user, supplied_password = decoded.split(":", 1)
            except Exception:
                return False
            expected_user = getattr(self, "basic_username", "xauby")
            return hmac.compare_digest(supplied_user, expected_user) and hmac.compare_digest(
                supplied_password,
                password,
            )
        return False

    def _send_auth_required(self) -> None:
        body = b"authentication required\n"
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="xAuby WebUI"')
        self._send_security_headers()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, path: str) -> None:
        rel = "index.html" if path in {"", "/"} else path.lstrip("/")
        rel_path = Path(rel)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        file_path = (self.static_root / rel_path).resolve()
        try:
            file_path.relative_to(self.static_root.resolve())
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = file_path.read_bytes()
        ctype = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self._send_security_headers()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if not self._is_authorized():
            self._send_auth_required()
            return
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/api/state":
            self._send_json(load_state(self.project_root))
            return
        if path == "/api/meta":
            self._send_json(meta_payload(self.project_root))
            return
        if path == "/api/health":
            self._send_json(health_payload(self.project_root))
            return
        if path == "/api/recent-events":
            self._send_json(recent_events_payload(self.project_root))
            return
        if path == "/api/trades":
            raw_limit = (query.get("limit") or ["20"])[0]
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError):
                limit = 20
            symbol = (query.get("symbol") or [""])[0]
            self._send_json(trades_payload(self.project_root, limit=limit, symbol=symbol))
            return
        if path == "/api/candles":
            raw_limit = (query.get("limit") or ["24"])[0]
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError):
                limit = 24
            symbol = (query.get("symbol") or [""])[0]
            timeframe = (query.get("timeframe") or ["4h"])[0]
            self._send_json(
                candles_payload(
                    self.project_root,
                    symbol=symbol,
                    timeframe=timeframe,
                    limit=limit,
                )
            )
            return
        self._send_static(path)


def create_server(host: str, port: int, project_root: str = ".") -> ThreadingHTTPServer:
    root = os.path.abspath(project_root)
    username, password, bearer_token = _webui_auth_from_env()
    _validate_bind_security(host, username, password, bearer_token)

    class Handler(XAubyWebUIHandler):
        pass

    Handler.project_root = root
    Handler.static_root = STATIC_ROOT
    Handler.basic_username = username
    Handler.basic_password = password
    Handler.bearer_token = bearer_token

    return ThreadingHTTPServer((host, int(port)), Handler)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the read-only xAuby WebUI")
    parser.add_argument("--host", default=os.environ.get("WEBUI_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("WEBUI_PORT", "8787")))
    parser.add_argument("--project-root", default=os.getcwd())
    args = parser.parse_args(argv)

    server = create_server(args.host, args.port, project_root=args.project_root)
    host, port = server.server_address
    print(f"xAuby WebUI listening on http://{host}:{port}")
    print("Bind to 127.0.0.1 and use SSH tunnel or Tailscale for remote access.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
