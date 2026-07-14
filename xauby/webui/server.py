"""Small WebUI server backed by runtime state files.

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
import secrets
import sqlite3
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from xauby.meta import load_bot_display_name, load_webui_avatar
from xauby.observability.health import HealthMonitor
from xauby.runtime.manual_orders import (
    MANUAL_ORDER_MAX_AGE_SECONDS,
    VALID_MANAGEMENT_MODES,
    VALID_MANUAL_ACTIONS,
    write_manual_order_request,
)
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


def _csv_env(name: str) -> set[str]:
    return {
        item.strip().lower()
        for item in os.environ.get(name, "").split(",")
        if item.strip()
    }


def _google_auth_from_env() -> Dict[str, Any]:
    allowed_emails = _csv_env("XAUBY_GOOGLE_ALLOWED_EMAILS")
    allowed_domains = {item.lstrip("@") for item in _csv_env("XAUBY_GOOGLE_ALLOWED_DOMAINS")}
    client_id = os.environ.get("XAUBY_GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("XAUBY_GOOGLE_CLIENT_SECRET", "").strip()
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": os.environ.get("XAUBY_GOOGLE_REDIRECT_URI", "").strip(),
        "allowed_emails": allowed_emails,
        "allowed_domains": allowed_domains,
        "enabled": bool(client_id and client_secret and (allowed_emails or allowed_domains)),
    }


_SESSION_COOKIE = "xauby_session"
_OAUTH_STATE_COOKIE = "xauby_oauth_state"
_SESSION_TTL_SEC = 7 * 24 * 3600
_OAUTH_STATE_TTL_SEC = 10 * 60
# Static paths a browser needs before signing in. Everything else — including
# /app.js and the operator avatar — stays behind auth so nothing personal or
# behavioral leaks pre-login.
_PREAUTH_STATIC = {
    "/login",
    "/login.css",
    "/login.js",
    "/logout",
    "/auth/config",
    "/auth/google/start",
    "/auth/google/callback",
    "/style.css",
    "/xau-logo.svg",
}
_MANUAL_TRADE_MIN_CODE_LENGTH = 6
_MANUAL_ORDER_MAX_STATE_AGE_SECONDS = 120.0
_JSON_POST_MAX_BYTES = 4096
WEBUI_CHART_CANDLE_COUNT = 32


def _session_secret_from_env() -> bytes:
    """Random per-process secret; XAUBY_WEBUI_SESSION_SECRET keeps sessions
    across restarts. Never derived from the password — a stolen cookie must
    not become an offline brute-force oracle for it."""
    configured = os.environ.get("XAUBY_WEBUI_SESSION_SECRET", "")
    if configured:
        return configured.encode("utf-8")
    return secrets.token_bytes(32)


def _sign_session(secret: bytes, expires_at: int) -> str:
    msg = str(int(expires_at)).encode("ascii")
    sig = hmac.new(secret, msg, "sha256").hexdigest()
    return f"{int(expires_at)}.{sig}"


def _verify_session(secret: bytes, cookie_value: str) -> bool:
    raw_exp, _, sig = str(cookie_value or "").partition(".")
    try:
        expires_at = int(raw_exp)
    except (TypeError, ValueError):
        return False
    if expires_at < time.time():
        return False
    expected = hmac.new(secret, str(expires_at).encode("ascii"), "sha256").hexdigest()
    return hmac.compare_digest(sig, expected)


def _sign_oauth_state(secret: bytes, state: str, expires_at: int) -> str:
    msg = f"{state}.{int(expires_at)}".encode("utf-8")
    sig = hmac.new(secret, msg, "sha256").hexdigest()
    return f"{state}.{int(expires_at)}.{sig}"


def _verify_oauth_state(secret: bytes, cookie_value: str, supplied_state: str) -> bool:
    raw_state, raw_exp, sig = str(cookie_value or "").rsplit(".", 2) if cookie_value.count(".") >= 2 else ("", "0", "")
    if not raw_state or not supplied_state or not hmac.compare_digest(raw_state, supplied_state):
        return False
    try:
        expires_at = int(raw_exp)
    except (TypeError, ValueError):
        return False
    if expires_at < time.time():
        return False
    expected = hmac.new(
        secret,
        f"{raw_state}.{expires_at}".encode("utf-8"),
        "sha256",
    ).hexdigest()
    return hmac.compare_digest(sig, expected)


def _google_request_json(url: str, data: Optional[bytes] = None, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    request = Request(url, data=data, headers=headers or {})
    with urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def _google_exchange_code(config: Dict[str, Any], code: str, redirect_uri: str) -> Dict[str, Any]:
    payload = urlencode(
        {
            "code": code,
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    return _google_request_json(
        "https://oauth2.googleapis.com/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


def _google_tokeninfo(id_token: str) -> Dict[str, Any]:
    return _google_request_json(
        "https://oauth2.googleapis.com/tokeninfo?" + urlencode({"id_token": id_token})
    )


def _google_email_allowed(config: Dict[str, Any], email: str) -> bool:
    normalized = str(email or "").strip().lower()
    if not normalized or "@" not in normalized:
        return False
    if normalized in config.get("allowed_emails", set()):
        return True
    domain = normalized.rsplit("@", 1)[1]
    return domain in config.get("allowed_domains", set())


def _validate_bind_security(host: str, username: str, password: str, bearer_token: str, google_auth: Optional[Dict[str, Any]] = None) -> None:
    if _is_loopback_host(host):
        return
    if password or bearer_token or (google_auth or {}).get("enabled") or _truthy_env("XAUBY_WEBUI_ALLOW_UNAUTH_REMOTE"):
        return
    raise ValueError(
        "Refusing to bind xAuby WebUI to a non-loopback host without auth. "
        "Set XAUBY_WEBUI_PASSWORD, XAUBY_WEBUI_TOKEN, Google OAuth env vars, or bind to 127.0.0.1."
    )


def _manual_trade_confirmation_code() -> str:
    """Return the configured manual-trade code, or empty when disabled."""
    code = (
        os.environ.get("XAUBY_WEBUI_TRADE_CONFIRMATION_CODE")
        or os.environ.get("XAUBY_WEBUI_TRADE_CODE")
        or ""
    ).strip()
    return code if len(code) >= _MANUAL_TRADE_MIN_CODE_LENGTH else ""


def _manual_trading_status(*, read_only: bool, age_sec: Any = None) -> Dict[str, Any]:
    configured = bool(_manual_trade_confirmation_code())
    try:
        age = float(age_sec)
    except (TypeError, ValueError):
        age = None
    state_fresh = bool(age is not None and 0 <= age <= _MANUAL_ORDER_MAX_STATE_AGE_SECONDS)
    disabled_reason = ""
    if not configured:
        disabled_reason = "confirmation_code_not_configured"
    elif read_only:
        disabled_reason = "read_only"
    elif not state_fresh:
        disabled_reason = "state_stale"
    return {
        "enabled": bool(configured and not read_only and state_fresh),
        "configured": configured,
        "requires_code": True,
        "state_fresh": state_fresh,
        "max_state_age_sec": _MANUAL_ORDER_MAX_STATE_AGE_SECONDS,
        "disabled_reason": disabled_reason,
    }


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
        strategy_config = (cfg.get("strategy") or {}).get("config") or {}
        legacy_config = cfg.get("strategies") or {}
        raw = (
            (strategy_config.get("xauby_actionzone") or {}).get("ap_smoothing")
            or (strategy_config.get("cdc_action_zone") or {}).get("ap_smoothing")
            or (legacy_config.get("xauby_actionzone") or {}).get("ap_smoothing")
            or (legacy_config.get("cdc_action_zone") or {}).get("ap_smoothing")
            or 1
        )
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


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if n == n else default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _clean_symbol(value: Any) -> str:
    return str(value or "").upper().replace("_", "")


def _position_open(position: Dict[str, Any]) -> bool:
    return str(position.get("state") or "").lower() == "bought"


def _safe_pct(value: Any) -> Optional[float]:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n else None


def _operator_detail_from_state(
    state: Dict[str, Any],
    focus: Dict[str, Any],
    *,
    age_sec: Any = None,
) -> Dict[str, Any]:
    position = focus.get("position") if isinstance(focus.get("position"), dict) else {}
    signal = focus.get("signal_meta") if isinstance(focus.get("signal_meta"), dict) else {}
    risk = focus.get("risk") if isinstance(focus.get("risk"), dict) else {}
    latency = focus.get("latency") if isinstance(focus.get("latency"), dict) else {}
    exchange = focus.get("exchange") if isinstance(focus.get("exchange"), dict) else {}
    aggregate = state.get("aggregate") if isinstance(state.get("aggregate"), dict) else {}
    read_only = bool(focus.get("read_only") or state.get("read_only") or aggregate.get("read_only"))
    position_open = _position_open(position)
    entry_price = _as_float(position.get("entry_price"))
    mark_price = _as_float(position.get("mark_price") or focus.get("current_price"))
    stop_loss = _as_float(position.get("stop_loss"))
    take_profit = _as_float(position.get("take_profit"))
    qty = _as_float(position.get("quantity"))
    side = str(position.get("position_side") or "LONG").upper()
    risk_pct = _as_float(risk.get("risk_pct")) * 100.0
    partial_trigger = _as_float(position.get("partial_tp_trigger_price"))
    partial_fraction = _as_float(position.get("partial_tp_fraction"))

    protection_items = [
        {
            "label": "Stop Loss",
            "value": stop_loss,
            "status": "ok" if stop_loss > 0 else ("warn" if position_open else "muted"),
        },
        {
            "label": "Take Profit",
            "value": take_profit,
            "status": "ok" if take_profit > 0 else "muted",
        },
        {
            "label": "Partial TP",
            "value": {
                "taken": bool(position.get("partial_tp_taken")),
                "trigger_price": partial_trigger,
                "fraction": partial_fraction,
                "pct": _as_float(position.get("partial_tp_pct")),
            },
            "status": "ok" if position.get("partial_tp_taken") else ("info" if partial_trigger > 0 else "muted"),
        },
        {
            "label": "Liq. Price",
            "value": _as_float(position.get("liquidation_price")),
            "status": "info" if _as_float(position.get("liquidation_price")) > 0 else "muted",
        },
    ]

    return {
        "mode": focus.get("execution_mode") or ("sim" if focus.get("simulate_only") else "live"),
        "read_only": read_only,
        "symbol": _clean_symbol(focus.get("symbol") or state.get("focus_symbol") or state.get("symbol")),
        "manual_trading": _manual_trading_status(read_only=read_only, age_sec=age_sec),
        "strategy": focus.get("strategy_name") or signal.get("strategy_name") or "",
        "strategy_version": focus.get("strategy_version") or "",
        "state_age_sec": age_sec,
        "exchange": {
            "id": exchange.get("id") or exchange.get("name") or "",
            "provider": exchange.get("provider") or "",
            "market_type": exchange.get("market_type") or position.get("market_type") or "",
            "fee_source": ((exchange.get("fees") or {}) if isinstance(exchange.get("fees"), dict) else {}).get("source") or "",
        },
        "position": {
            "open": position_open,
            "state": position.get("state") or "idle",
            "side": side if position_open else "",
            "quantity": qty,
            "entry_price": entry_price,
            "mark_price": mark_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "opened_at": position.get("opened_at") or "",
            "management_mode": position.get("management_mode") or "",
            "margin_mode": position.get("margin_mode") or "",
            "leverage": _as_float(position.get("leverage"), 1.0),
            "unrealized_pnl": _as_float(position.get("unrealized_pnl")),
            "unrealized_pnl_pct": _safe_pct(position.get("unrealized_pnl_pct")),
            "unrealized_pnl_gross": _as_float(position.get("unrealized_pnl_gross")),
            "estimated_entry_fee": _as_float(position.get("estimated_entry_fee")),
            "estimated_exit_fee": _as_float(position.get("estimated_exit_fee")),
            "estimated_total_fees": _as_float(position.get("estimated_total_fees")),
            "funding_paid": _as_float(position.get("funding_paid")),
            "feed_health": position.get("feed_health") or ("DEGRADED" if focus.get("degraded") else "OK"),
            "degraded": bool(focus.get("degraded")),
            "degrade_reason": focus.get("degrade_reason") or "",
            "stop_loss_order_id": position.get("stop_loss_order_id"),
            "protection": protection_items,
        },
        "risk": {
            "risk_pct": risk_pct,
            "sl_atr_mult": _as_float(risk.get("sl_atr_mult")),
            "trailing_atr_mult": _as_float(risk.get("trailing_atr_mult")),
            "breakeven_sl_enabled": bool(risk.get("breakeven_sl_enabled")),
            "require_fresh_zone": bool(risk.get("require_fresh_zone")),
            "fresh_zone_window": _as_int(risk.get("fresh_zone_window")),
        },
        "signal": {
            "action": signal.get("action") or "",
            "intent": signal.get("intent") or "",
            "reason": signal.get("reason") or "",
            "confidence": _safe_pct(signal.get("confidence")),
            "status_summary": signal.get("status_summary") or "",
            "checklist": signal.get("checklist") if isinstance(signal.get("checklist"), list) else [],
        },
        "latency": {
            "api_latency_ms": _as_int(latency.get("api_latency_ms") or focus.get("api_latency_ms")),
            "ws_tick_age_ms": _as_int(latency.get("ws_tick_age_ms")),
            "tick_duration_ms": _as_int(latency.get("tick_duration_ms")),
            "sync_candles_ms": _as_int(latency.get("sync_candles_ms")),
            "state_export_ms": _as_int(latency.get("state_export_ms")),
            "strategy_ms": _as_int(latency.get("strategy_ms")),
            "regime_ms": _as_int(latency.get("regime_ms")),
            "event_store_ms": _as_int(latency.get("event_store_ms")),
            "clock_offset_seconds": _as_float(latency.get("clock_offset_seconds") or focus.get("clock_offset_seconds")),
        },
    }


def _regime_detail_from_state(state: Dict[str, Any], focus: Dict[str, Any]) -> Dict[str, Any]:
    regime = focus.get("regime") if isinstance(focus.get("regime"), dict) else {}
    macro = focus.get("macro_guard") if isinstance(focus.get("macro_guard"), dict) else {}
    router = focus.get("regime_router") if isinstance(focus.get("regime_router"), dict) else {}
    bias = regime.get("strategy_bias") if isinstance(regime.get("strategy_bias"), dict) else {}
    features = regime.get("features") if isinstance(regime.get("features"), dict) else {}
    reasons = regime.get("reasons") if isinstance(regime.get("reasons"), list) else []
    return {
        "state": regime.get("regime") or "UNKNOWN",
        "trend": regime.get("trend") or "",
        "volatility": regime.get("volatility") or "",
        "macro_bias": regime.get("macro_bias") or "",
        "confidence": _safe_pct(regime.get("confidence")),
        "phase": regime.get("phase") or "",
        "risk_state": regime.get("risk_state") or "",
        "trend_strength": regime.get("trend_strength") or "",
        "volatility_state": regime.get("volatility_state") or "",
        "liquidity_state": regime.get("liquidity_state") or "",
        "transition_risk": regime.get("transition_risk") or "",
        "gold_score": _safe_pct(regime.get("gold_score")),
        "strategy_bias": {
            "family": bias.get("family") or "",
            "posture": bias.get("posture") or "",
            "allowed_actions": bias.get("allowed_actions") if isinstance(bias.get("allowed_actions"), list) else [],
            "preferred": bias.get("preferred") if isinstance(bias.get("preferred"), list) else [],
        },
        "reasons": [
            {
                "label": str(item.get("label") or ""),
                "supportive": bool(item.get("supportive")),
            }
            for item in reasons
            if isinstance(item, dict)
        ],
        "features": {
            key: features.get(key)
            for key in (
                "ema_spread_pct",
                "atr_pct",
                "atr_percentile",
                "volume_ratio",
                "momentum_5_pct",
                "momentum_20_pct",
                "trend_quality",
                "macro_alignment",
                "institutional_confidence",
            )
            if key in features
        },
        "macro_guard": {
            "enabled": bool(macro.get("enabled")),
            "blocks_buy": bool(macro.get("blocks_buy")),
            "scope": macro.get("scope") or "",
            "score": _safe_pct(macro.get("score")),
            "blocking_threshold": _safe_pct(macro.get("blocking_threshold")),
            "summary": macro.get("summary") or "",
            "dxy_score": _safe_pct(macro.get("dxy_score")),
            "dxy_price": _safe_pct(macro.get("dxy_price")),
            "fred_rate": _safe_pct(macro.get("fred_rate")),
            "news_reason": macro.get("news_reason") or "",
        },
        "router": {
            "enabled": bool(router.get("enabled")),
            "no_trade_state": router.get("no_trade_state") or "",
            "confirmed_regime": router.get("confirmed_regime") or "",
            "pending_regime": router.get("pending_regime") or "",
            "warning": router.get("warning") or "",
            "live_confirmed": bool(router.get("live_confirmed")),
        },
        "primary_timeframe": focus.get("primary_timeframe") or "4h",
        "confirm_timeframe": focus.get("confirm_timeframe") or "",
        "last_candle_timestamp": focus.get("last_candle_timestamp"),
    }


def dashboard_detail_payload(project_root: str) -> Dict[str, Any]:
    state_payload = load_state(project_root)
    if not state_payload["ok"]:
        return {
            **state_payload,
            "operator": {},
            "regime_detail": {},
            "activity": {"events": [], "trades": []},
        }
    state = state_payload["state"]
    focus = _focus_snapshot(state)
    symbol = _clean_symbol(focus.get("symbol") or state.get("focus_symbol") or state.get("symbol"))
    events = recent_events_payload(project_root).get("events") or []
    trades = trades_payload(project_root, limit=30, symbol=symbol).get("trades") or []
    return {
        "ok": True,
        "age_sec": state_payload.get("age_sec"),
        "stale": state_payload.get("stale", False),
        "operator": _operator_detail_from_state(state, focus, age_sec=state_payload.get("age_sec")),
        "regime_detail": _regime_detail_from_state(state, focus),
        "activity": {
            "events": events[-40:],
            "trades": trades[:30],
            "event_count": len(events),
            "trade_count": len(trades),
        },
    }


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
    limit: int = WEBUI_CHART_CANDLE_COUNT,
) -> Dict[str, Any]:
    db_path = _project_path(project_root, runtime_db_path())
    limit = max(1, min(int(limit or WEBUI_CHART_CANDLE_COUNT), 80))
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


def manual_order_payload(
    project_root: str,
    payload: Dict[str, Any],
) -> tuple[HTTPStatus, Dict[str, Any]]:
    """Validate and queue a confirmed manual order request.

    The WebUI never talks to the exchange. It writes the same short-lived local
    IPC request as the TUI; the engine still owns all execution/risk checks.
    """
    configured_code = _manual_trade_confirmation_code()
    if not configured_code:
        return (
            HTTPStatus.FORBIDDEN,
            {
                "ok": False,
                "error": "manual trading is disabled",
                "reason": "confirmation_code_not_configured",
                "min_code_length": _MANUAL_TRADE_MIN_CODE_LENGTH,
            },
        )

    supplied_code = str(
        payload.get("confirmation_code")
        or payload.get("confirm_code")
        or payload.get("code")
        or ""
    ).strip()
    if not supplied_code or not hmac.compare_digest(supplied_code, configured_code):
        return (
            HTTPStatus.FORBIDDEN,
            {"ok": False, "error": "invalid confirmation code", "reason": "bad_code"},
        )

    action = str(payload.get("action") or "").upper()
    if action not in VALID_MANUAL_ACTIONS:
        return (
            HTTPStatus.BAD_REQUEST,
            {
                "ok": False,
                "error": f"action must be one of {sorted(VALID_MANUAL_ACTIONS)}",
            },
        )

    management_mode = str(payload.get("management_mode") or "strategy").lower()
    if management_mode not in VALID_MANAGEMENT_MODES:
        return (
            HTTPStatus.BAD_REQUEST,
            {
                "ok": False,
                "error": f"management_mode must be one of {sorted(VALID_MANAGEMENT_MODES)}",
            },
        )

    state_payload = load_state(project_root)
    if not state_payload["ok"]:
        return (
            HTTPStatus.CONFLICT,
            {"ok": False, "error": "runtime state is unavailable", "reason": "state_missing"},
        )

    try:
        age = float(state_payload.get("age_sec"))
    except (TypeError, ValueError):
        age = None
    if age is None or age < 0 or age > _MANUAL_ORDER_MAX_STATE_AGE_SECONDS:
        return (
            HTTPStatus.CONFLICT,
            {
                "ok": False,
                "error": "runtime state is stale",
                "reason": "state_stale",
                "age_sec": state_payload.get("age_sec"),
                "max_state_age_sec": _MANUAL_ORDER_MAX_STATE_AGE_SECONDS,
            },
        )

    state = state_payload["state"]
    focus = _focus_snapshot(state)
    focus_symbol = _clean_symbol(focus.get("symbol") or state.get("focus_symbol") or state.get("symbol"))
    requested_symbol = _clean_symbol(payload.get("symbol") or focus_symbol)
    if not requested_symbol:
        return (
            HTTPStatus.BAD_REQUEST,
            {"ok": False, "error": "symbol is required", "reason": "symbol_required"},
        )
    if not focus_symbol or requested_symbol != focus_symbol:
        return (
            HTTPStatus.CONFLICT,
            {
                "ok": False,
                "error": "requested symbol is not the focused runtime symbol",
                "reason": "symbol_mismatch",
                "symbol": requested_symbol,
                "focus_symbol": focus_symbol,
            },
        )

    aggregate = state.get("aggregate") if isinstance(state.get("aggregate"), dict) else {}
    if bool(focus.get("read_only") or state.get("read_only") or aggregate.get("read_only")):
        return (
            HTTPStatus.CONFLICT,
            {"ok": False, "error": "engine is read-only", "reason": "read_only"},
        )

    position = focus.get("position") if isinstance(focus.get("position"), dict) else {}
    position_state = str(position.get("state") or "idle").lower()
    if action == "BUY" and position_state != "idle":
        return (
            HTTPStatus.CONFLICT,
            {
                "ok": False,
                "error": f"{requested_symbol} already has a tracked position",
                "reason": "position_open",
            },
        )
    if action == "SELL":
        if position_state != "bought":
            return (
                HTTPStatus.CONFLICT,
                {
                    "ok": False,
                    "error": f"{requested_symbol} has no tracked position",
                    "reason": "position_missing",
                },
            )
        position_mode = str(position.get("management_mode") or management_mode).lower()
        management_mode = position_mode if position_mode in VALID_MANAGEMENT_MODES else "strategy"

    try:
        request = write_manual_order_request(
            requested_symbol,
            action,
            management_mode=management_mode,
            source="webui",
            project_root=project_root,
        )
    except ValueError as exc:
        return (HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
    except Exception as exc:
        return (
            HTTPStatus.INTERNAL_SERVER_ERROR,
            {"ok": False, "error": f"manual order queue failed: {exc}"},
        )

    return (
        HTTPStatus.ACCEPTED,
        {
            "ok": True,
            "queued": True,
            "symbol": request["symbol"],
            "action": request["action"],
            "management_mode": request["management_mode"],
            "request_id": request["request_id"],
            "expires_in_sec": MANUAL_ORDER_MAX_AGE_SECONDS,
        },
    )


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
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "base-uri 'none'; "
            "frame-ancestors 'none'",
        )

    def _session_cookie_value(self) -> str:
        return self._cookie_value(_SESSION_COOKIE)

    def _oauth_state_cookie_value(self) -> str:
        return self._cookie_value(_OAUTH_STATE_COOKIE)

    def _cookie_value(self, name: str) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return ""
        morsel = cookie.get(name)
        return morsel.value if morsel else ""

    def _browser_auth_enabled(self) -> bool:
        return self._password_auth_enabled() or bool(getattr(self, "google_auth", {}).get("enabled"))

    def _is_authorized(self) -> bool:
        password = getattr(self, "basic_password", "")
        bearer_token = getattr(self, "bearer_token", "")
        if not password and not bearer_token and not self._browser_auth_enabled():
            return True

        if self._browser_auth_enabled() and _verify_session(
            getattr(self, "session_secret", b""), self._session_cookie_value()
        ):
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

    def _send_redirect(self, location: str, extra_headers: Optional[list] = None) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self._send_security_headers()
        self.send_header("Cache-Control", "no-store")
        for name, value in extra_headers or []:
            self.send_header(name, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _cookie_header(self, name: str, value: str, max_age: int, *, http_only: bool = True) -> tuple[str, str]:
        secure = "; Secure" if _truthy_env("XAUBY_WEBUI_COOKIE_SECURE") else ""
        http_only_flag = "HttpOnly; " if http_only else ""
        return (
            "Set-Cookie",
            f"{name}={value}; Path=/; Max-Age={max_age}; "
            f"{http_only_flag}SameSite=Lax{secure}",
        )

    def _session_cookie_header(self, value: str, max_age: int) -> tuple[str, str]:
        return self._cookie_header(_SESSION_COOKIE, value, max_age)

    def _oauth_state_cookie_header(self, value: str, max_age: int) -> tuple[str, str]:
        return self._cookie_header(_OAUTH_STATE_COOKIE, value, max_age)

    def _password_auth_enabled(self) -> bool:
        return bool(getattr(self, "basic_password", ""))

    def _google_auth_enabled(self) -> bool:
        return bool(getattr(self, "google_auth", {}).get("enabled"))

    def _request_origin(self) -> str:
        scheme = "https" if self.headers.get("X-Forwarded-Proto", "").lower() == "https" else "http"
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or ""
        return f"{scheme}://{host}"

    def _google_redirect_uri(self) -> str:
        configured = str(getattr(self, "google_auth", {}).get("redirect_uri") or "").strip()
        if configured:
            return configured
        return self._request_origin().rstrip("/") + "/auth/google/callback"

    def _send_google_start(self) -> None:
        config = getattr(self, "google_auth", {})
        if not config.get("enabled"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        state = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + _OAUTH_STATE_TTL_SEC
        signed_state = _sign_oauth_state(getattr(self, "session_secret", b""), state, expires_at)
        params = {
            "client_id": config["client_id"],
            "redirect_uri": self._google_redirect_uri(),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        }
        self._send_redirect(
            "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params),
            extra_headers=[self._oauth_state_cookie_header(signed_state, _OAUTH_STATE_TTL_SEC)],
        )

    def _send_google_callback(self, query: Dict[str, list[str]]) -> None:
        config = getattr(self, "google_auth", {})
        if not config.get("enabled"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if query.get("error"):
            self._send_redirect("/login?google_error=1")
            return
        code = (query.get("code") or [""])[0]
        state = (query.get("state") or [""])[0]
        if not code or not _verify_oauth_state(
            getattr(self, "session_secret", b""),
            self._oauth_state_cookie_value(),
            state,
        ):
            self._send_redirect("/login?google_error=state")
            return
        try:
            token_payload = _google_exchange_code(config, code, self._google_redirect_uri())
            id_token = str(token_payload.get("id_token") or "")
            if not id_token:
                raise ValueError("missing id_token")
            claims = _google_tokeninfo(id_token)
        except Exception:
            self._send_redirect("/login?google_error=token")
            return

        email = str(claims.get("email") or "").strip().lower()
        aud = str(claims.get("aud") or "")
        issuer = str(claims.get("iss") or "")
        email_verified = str(claims.get("email_verified") or "").lower() in {"1", "true", "yes"}
        if (
            aud != config.get("client_id")
            or issuer not in {"accounts.google.com", "https://accounts.google.com"}
            or not email_verified
            or not _google_email_allowed(config, email)
        ):
            self._send_redirect("/login?google_error=denied")
            return

        expires_at = int(time.time()) + _SESSION_TTL_SEC
        session_token = _sign_session(getattr(self, "session_secret", b""), expires_at)
        self._send_redirect(
            "/",
            extra_headers=[
                self._oauth_state_cookie_header("", 0),
                self._session_cookie_header(session_token, _SESSION_TTL_SEC),
            ],
        )

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

    def _post_origin_allowed(self) -> bool:
        origin = str(self.headers.get("Origin") or "").rstrip("/")
        if not origin:
            return True
        return hmac.compare_digest(origin, self._request_origin().rstrip("/"))

    def _read_json_post_body(self) -> tuple[Optional[Dict[str, Any]], Optional[HTTPStatus], str]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return {}, None, ""
        if length > _JSON_POST_MAX_BYTES:
            return None, HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body is too large"
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError as exc:
            return None, HTTPStatus.BAD_REQUEST, f"invalid JSON: {exc.msg}"
        if not isinstance(payload, dict):
            return None, HTTPStatus.BAD_REQUEST, "JSON body must be an object"
        return payload, None, ""

    def _handle_manual_order_post(self) -> None:
        if not self._post_origin_allowed():
            self._send_json(
                {"ok": False, "error": "origin is not allowed", "reason": "bad_origin"},
                HTTPStatus.FORBIDDEN,
            )
            return
        payload, error_status, error = self._read_json_post_body()
        if error_status is not None:
            self._send_json({"ok": False, "error": error}, error_status)
            return
        status, response = manual_order_payload(self.project_root, payload or {})
        self._send_json(response, status)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # Sign-in surface: reachable without auth so the login page can render.
        if path in _PREAUTH_STATIC:
            if path == "/login":
                if not self._browser_auth_enabled():
                    self._send_redirect("/")
                    return
                self._send_static("login.html")
                return
            if path == "/auth/config":
                self._send_json(
                    {
                        "google_enabled": self._google_auth_enabled(),
                        "password_enabled": self._password_auth_enabled(),
                    }
                )
                return
            if path == "/auth/google/start":
                self._send_google_start()
                return
            if path == "/auth/google/callback":
                self._send_google_callback(query)
                return
            if path == "/logout":
                self._send_redirect(
                    "/login" if self._browser_auth_enabled() else "/",
                    extra_headers=[
                        self._session_cookie_header("", 0),
                        self._oauth_state_cookie_header("", 0),
                    ],
                )
                return
            self._send_static(path)
            return

        if not self._is_authorized():
            # Programmatic clients keep the 401 + WWW-Authenticate contract;
            # browsers get the branded sign-in page.
            if path.startswith("/api/"):
                self._send_auth_required()
            else:
                self._send_redirect("/login")
            return
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
        if path == "/api/dashboard-detail":
            self._send_json(dashboard_detail_payload(self.project_root))
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
            raw_limit = (query.get("limit") or [str(WEBUI_CHART_CANDLE_COUNT)])[0]
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError):
                limit = WEBUI_CHART_CANDLE_COUNT
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

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/manual-order":
            if not self._is_authorized():
                self._send_auth_required()
                return
            self._handle_manual_order_post()
            return
        if path != "/login":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not self._password_auth_enabled():
            self._send_redirect("/")
            return
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 4096)
        except (TypeError, ValueError):
            length = 0
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace")) if length else {}
        supplied = (form.get("password") or [""])[0]
        password = getattr(self, "basic_password", "")
        if supplied and hmac.compare_digest(supplied, password):
            expires_at = int(time.time()) + _SESSION_TTL_SEC
            token = _sign_session(getattr(self, "session_secret", b""), expires_at)
            self._send_redirect(
                "/", extra_headers=[self._session_cookie_header(token, _SESSION_TTL_SEC)]
            )
            return
        # Cheap brute-force damper; real rate limiting is out of scope for a
        # private-network dashboard (documented in docs/webui.md).
        time.sleep(0.3)
        self._send_redirect("/login?error=1")


def create_server(host: str, port: int, project_root: str = ".") -> ThreadingHTTPServer:
    root = os.path.abspath(project_root)
    username, password, bearer_token = _webui_auth_from_env()
    google_auth = _google_auth_from_env()
    _validate_bind_security(host, username, password, bearer_token, google_auth)

    class Handler(XAubyWebUIHandler):
        pass

    Handler.project_root = root
    Handler.static_root = STATIC_ROOT
    Handler.basic_username = username
    Handler.basic_password = password
    Handler.bearer_token = bearer_token
    Handler.google_auth = google_auth
    Handler.session_secret = _session_secret_from_env()

    return ThreadingHTTPServer((host, int(port)), Handler)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the xAuby WebUI")
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
