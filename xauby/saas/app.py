from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import math
import re
import secrets
import stat
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from xauby.runtime.trading_config import bounded_position_fraction
from xauby.saas.catalog import preset_by_id, public_catalog, target_by_id, validate_profile
from xauby.saas.certification import config_fingerprint
from xauby.saas.credentials import CredentialKeyring
from xauby.saas.mailer import Mailer
from xauby.saas.order_sizing import resolve_pair_atr, risk_based_stop_distance
from xauby.saas.runtime import RuntimeGateway
from xauby.saas.security import (
    AttemptThrottle,
    new_totp_secret,
    sign_state,
    verify_password,
    verify_state,
    verify_totp,
)
from xauby.saas.settings import SaaSSettings
from xauby.saas.store import ControlPlaneStore
from xauby.saas.strategy_pool import (
    MAX_CANDIDATES,
    append_candidate,
    append_evaluation,
    candidate,
    candidate_evaluation_provenance_reasons,
    evaluations_comparable,
    new_pool,
    normalize_pool,
    normalize_symbol,
    preset_for_candidate,
    promotion_eligibility,
)
from xauby.saas.supervisor import TenantSupervisor, attach_tenant_loaders
from xauby.utils.atomic_io import atomic_bytes_write

logger = logging.getLogger("xauby.saas")

SESSION_COOKIE = "xauby_saas_session"
OAUTH_STATE_COOKIE = "xauby_saas_oauth_state"
# Starlette buffers the whole body into memory before Pydantic's field
# max_length ever runs, so an unbounded body is a memory-exhaustion path into
# the service's systemd MemoryMax on unauthenticated routes. The largest
# legitimate body is the 1.5 MB avatar data URL in ProfileAppearanceBody.
MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024
TRADE_PIN_PATTERN = r"^[0-9]{8,12}$"
TELEGRAM_TOKEN_PATTERN = r"^[0-9]{5,16}:[A-Za-z0-9_-]{35}$"
TELEGRAM_CHAT_PATTERN = r"^(-?[0-9]{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31})$"
TELEGRAM_API_ROOT = "https://api.telegram.org"
WITHDRAWAL_LIVE_GATE_DETAIL = (
    "the exchange must verify that withdrawal permission is disabled before Live"
)
# Legacy consoles accepted a broader secret for the old/current value. Accept
# it only during rotation; every newly saved PIN and sensitive action keeps the
# stronger 8-12 digit requirement.


class ExchangeConnectBody(BaseModel):
    target_id: str = Field(min_length=3, max_length=64)
    api_key: str = Field(min_length=4, max_length=512)
    api_secret: str = Field(min_length=4, max_length=512)
    passphrase: str = Field(default="", max_length=512)
    withdraw_disabled_attested: bool


def withdrawal_permission_verified(connection: dict[str, Any] | None) -> bool:
    """Return true only for a current venue-issued withdrawal safety verdict."""
    capabilities = (connection or {}).get("capabilities") or {}
    return (
        capabilities.get("withdraw_permission_checked") is True
        and capabilities.get("withdraw_disabled_verified") is True
    )


class TelegramConnectBody(BaseModel):
    bot_token: str = Field(pattern=TELEGRAM_TOKEN_PATTERN, max_length=128)
    chat_id: str = Field(pattern=TELEGRAM_CHAT_PATTERN, max_length=64)


class TelegramPreferencesBody(BaseModel):
    trade_lifecycle: bool | None = None
    risk_safety: bool | None = None
    system_health: bool | None = None
    periodic_reports: bool | None = None
    commands_enabled: bool | None = None


class InviteBody(BaseModel):
    email: str = Field(min_length=5, max_length=254)


class InviteAcceptBody(BaseModel):
    token: str = Field(min_length=16, max_length=512)
    password: str = Field(min_length=12, max_length=128)


class LiveActivateBody(BaseModel):
    trade_pin: str = Field(pattern=TRADE_PIN_PATTERN)
    risk_acknowledged: bool


class TradePinBody(BaseModel):
    pin: str = Field(pattern=TRADE_PIN_PATTERN)
    current_pin: str | None = Field(default=None, min_length=1, max_length=128)


class TradePinResetBody(BaseModel):
    new_pin: str = Field(pattern=TRADE_PIN_PATTERN)
    current_password: str = Field(default="", max_length=128)
    totp_code: str = Field(min_length=1, max_length=128)


class ProfileAppearanceBody(BaseModel):
    display_name: str = Field(min_length=1, max_length=40)
    avatar_data_url: str | None = Field(default=None, max_length=1_500_000)
    remove_avatar: bool = False


class OrderPreviewBody(BaseModel):
    symbol: str = Field(min_length=3, max_length=32)
    intent: str = Field(pattern="^(OPEN_LONG|OPEN_SHORT|CLOSE_POSITION)$")


class OrderConfirmBody(BaseModel):
    trade_pin: str = Field(pattern=TRADE_PIN_PATTERN)
    idempotency_key: str = Field(min_length=8, max_length=128)


class ConfigPatchBody(BaseModel):
    risk_pct: float | None = None
    max_position_per_trade_pct: float | None = None
    max_daily_loss_pct: float | None = None


class SignupBody(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=12, max_length=128)


class LoginBody(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=1, max_length=128)
    totp_code: str = Field(default="", max_length=12)


class EmailBody(BaseModel):
    email: str = Field(min_length=5, max_length=254)


class TokenBody(BaseModel):
    token: str = Field(min_length=16, max_length=512)


class ResetPasswordBody(TokenBody):
    password: str = Field(min_length=12, max_length=128)


class ChangePasswordBody(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class TotpBody(BaseModel):
    code: str = Field(pattern=r"^[0-9]{6}$")


class TotpSetupBody(BaseModel):
    current_code: str = Field(default="", max_length=12)


class TradingProfileBody(BaseModel):
    preset_ids: list[str] = Field(min_length=1, max_length=3)
    active_preset_id: str
    risk: dict[str, Any]


class StrategyCandidateBody(BaseModel):
    preset_id: str = Field(min_length=3, max_length=128)


class StrategyPromotionBody(BaseModel):
    challenger_id: str = Field(min_length=3, max_length=128)
    trade_pin: str = Field(pattern=TRADE_PIN_PATTERN)


class StrategyEvaluationBody(BaseModel):
    forward_days: int = Field(ge=0, le=3650)
    trades: int = Field(ge=0, le=100000)
    profit_factor: float = Field(ge=0, le=1000, allow_inf_nan=False)
    net_return_pct: float = Field(ge=-100, le=100000, allow_inf_nan=False)
    max_drawdown_pct: float = Field(ge=0, le=100, allow_inf_nan=False)
    # A worker retry with the same run_id is idempotent.  Empty values are
    # accepted for diagnostics, but such a record can never be promotion-ready.
    run_id: str = Field(default="", max_length=256)
    artifact_sha256: str = Field(default="", max_length=128)
    config_fingerprint: str = Field(default="", max_length=128)
    venue: str = Field(default="", max_length=128)
    timeframe: str = Field(default="", max_length=32)
    data_window_start: str = Field(default="", max_length=128)
    data_window_end: str = Field(default="", max_length=128)
    fill_model: str = Field(default="", max_length=128)
    fees_pct: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    slippage_pct: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    # Kept for wire compatibility with the MVP client.  The server ignores it
    # and derives the consecutive streak from immutable evaluation history.
    winning_evaluations: int = Field(default=0, ge=0, le=1000)


class AccountStatusBody(BaseModel):
    status: str = Field(pattern="^(active|rejected|suspended)$")


class RemovePilotBody(BaseModel):
    confirm_email: str = Field(min_length=5, max_length=254)


LIVE_ADDITIVE_RISK_KEYS = (
    "risk_pct",
    "max_position_per_trade_pct",
    "max_daily_loss_pct",
    "max_leverage",
)


def _is_safe_live_pair_addition(
    existing: dict[str, Any] | None,
    profile: dict[str, Any],
) -> bool:
    """Allow a certified same-exchange pair to join Live without stopping it."""
    if not existing or existing.get("target_id") != profile.get("target_id"):
        return False
    old_ids = set(existing.get("preset_ids") or [])
    new_ids = set(profile.get("preset_ids") or [])
    added_ids = new_ids - old_ids
    if not old_ids or not added_ids or not old_ids < new_ids:
        return False

    # The stored preset objects are catalog snapshots, not user-editable input.
    # They may predate a certified catalog revision (for example LONG-only ->
    # LONG/SHORT or the XAU 65% allocation). Comparing those snapshots made a
    # plain pair addition look like a strategy edit and incorrectly stopped
    # Live. IDs, target, bounded risk and the current certified catalog are the
    # authoritative gates for this additive-only path.
    new_presets = {item.get("id"): item for item in profile.get("presets") or []}
    if any(not new_presets.get(preset_id, {}).get("live_certified") for preset_id in added_ids):
        return False

    old_risk = existing.get("risk") or {}
    new_risk = profile.get("risk") or {}
    if any(float(old_risk.get(key, 0)) != float(new_risk.get(key, 0)) for key in LIVE_ADDITIVE_RISK_KEYS):
        return False

    # Certified allocations share one account. Never hot-add a set whose fixed
    # catalog allocations consume more than the 95% deployable cash envelope.
    allocations = [item.get("allocation_pct") for item in profile.get("presets") or []]
    return all(value is not None for value in allocations) and sum(
        float(value) for value in allocations
    ) <= 95.0


class _BodyTooLarge(Exception):
    """Internal signal raised while reading an oversized request body."""


class BodySizeLimitMiddleware:
    """Reject oversized request bodies before they are buffered into memory.

    Content-Length alone would not enforce anything here: the Next.js proxy
    streams the body through and drops content-length as a hop-by-hop header,
    so proxied requests arrive chunked with no declared size. The limit has to
    be counted while reading, which is why this is raw ASGI rather than an
    http middleware — the latter only runs once the body is already buffered.
    """

    def __init__(self, app: Any, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = int(max_bytes)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope.get("headers") or {}).get(b"content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(send)
            return

        received = 0
        overflowed = False
        answered = False

        async def limited_receive() -> dict[str, Any]:
            nonlocal received, overflowed
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > self.max_bytes:
                    overflowed = True
                    raise _BodyTooLarge
            return message

        async def guarded_send(message: dict[str, Any]) -> None:
            nonlocal answered
            # Once the cap is hit the app's own answer is misleading — FastAPI
            # turns the read failure into a generic 400 "error parsing the
            # body". Replace it so the caller learns the actual reason, and
            # swallow whatever the app streams afterwards.
            if overflowed:
                if not answered:
                    answered = True
                    await self._reject(send)
                return
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLarge:
            if not answered:
                answered = True
                await self._reject(send)

    @staticmethod
    async def _reject(send: Any) -> None:
        body = json.dumps({"detail": "request body is too large"}).encode()
        await send({
            "type": "http.response.start", "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})


def _safe_slug_from_email(email: str, fallback: str) -> str:
    local = str(email).split("@", 1)[0].lower()
    # Keep room for the ``xauby-`` prefix used by the tenant-specific Linux
    # service account (Linux usernames are limited to 32 characters).
    slug = re.sub(r"[^a-z0-9]+", "-", local).strip("-")[:25]
    return slug if slug else f"user-{fallback[:8]}"


def _explicit_flat_runtime_snapshot(
    snapshot: dict[str, Any] | None,
    symbol: str,
    *,
    max_age_seconds: float = 30.0,
) -> bool:
    """Validate the minimum runtime evidence needed before a handoff.

    This is intentionally independent of the tenant supervisor so it can be
    regression-tested with a captured snapshot.  It is not an exchange
    reconciliation substitute; unknown data is always rejected.
    """
    payload = snapshot or {}
    try:
        age = float(payload.get("age_sec"))
        age_limit = float(max_age_seconds)
    except (TypeError, ValueError):
        return False
    if (
        not payload.get("ok")
        or payload.get("read_only")
        or payload.get("stale")
        or not math.isfinite(age)
        or age < 0
        or not math.isfinite(age_limit)
        or age > age_limit
    ):
        return False
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    by_symbol = state.get("by_symbol")
    if not isinstance(by_symbol, dict):
        return False
    compact = normalize_symbol(symbol)
    value = next(
        (
            item for key, item in by_symbol.items()
            if normalize_symbol(str(key)) == compact and isinstance(item, dict)
        ),
        None,
    )
    if value is None:
        return False
    position = value.get("position") if isinstance(value.get("position"), dict) else value
    position_state = str(position.get("state") or value.get("state") or "").strip().lower()
    if position_state not in {"idle", "flat", "closed", "none"}:
        return False
    try:
        quantity = float(position.get("quantity", value.get("quantity", 0.0)) or 0.0)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(quantity) or quantity < 0 or quantity > 1e-12:
        return False
    if position.get("exchange_position_id") or value.get("exchange_position_id"):
        return False
    open_orders = position.get("open_orders", value.get("open_orders"))
    if open_orders is not None and open_orders != [] and open_orders != {}:
        return False
    return True


def _finite_float(value: Any) -> float | None:
    """Parse an untrusted runtime/config number without admitting NaN/Inf."""
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def create_app(
    settings: SaaSSettings | None = None,
    *,
    store: ControlPlaneStore | None = None,
    supervisor: TenantSupervisor | None = None,
    mailer: Mailer | None = None,
    runtime: RuntimeGateway | None = None,
) -> FastAPI:
    settings = settings or SaaSSettings.from_env()
    settings.ensure_directories()
    store = store or ControlPlaneStore(settings.database_path)
    store.migrate()
    supervisor = supervisor or TenantSupervisor(settings)
    cipher = CredentialKeyring(
        settings.credential_master_key,
        active_version=settings.credential_master_key_version,
        previous_keys=settings.credential_previous_keys,
        fallback_secret=settings.session_secret if settings.dev_login_enabled else "",
    )

    # Both loaders come from one definition shared with the systemd
    # ExecStartPre helper; two copies is how the helper ended up wiring
    # only half of them.
    attach_tenant_loaders(supervisor, store, cipher)
    mailer = mailer or Mailer(settings)
    runtime = runtime or RuntimeGateway(settings, supervisor)
    # The pool state is guarded in-process as well as by the database revision.
    # The revision remains the authority when multiple control-plane workers
    # are deployed, while this lock avoids needless local read/modify races.
    pool_locks: dict[str, Any] = {}
    pool_locks_guard = threading.Lock()
    login_throttle = AttemptThrottle(max_attempts=8, window_seconds=300, lockout_seconds=900)
    email_throttle = AttemptThrottle(max_attempts=5, window_seconds=900, lockout_seconds=900)
    trade_pin_reset_throttle = AttemptThrottle(max_attempts=5, window_seconds=300, lockout_seconds=900)
    # The only tenant-triggered outbound HTTP in the app besides OAuth.
    telegram_test_throttle = AttemptThrottle(max_attempts=5, window_seconds=300, lockout_seconds=300)
    app = FastAPI(title="xAuby SaaS Control Plane", version="0.1.0")
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_REQUEST_BODY_BYTES)
    app.state.settings = settings
    app.state.store = store
    app.state.supervisor = supervisor
    app.state.mailer = mailer
    app.state.runtime = runtime
    avatar_root = settings.data_root / "avatars"
    avatar_root.mkdir(parents=True, exist_ok=True)

    def _json_response(payload: dict[str, Any], status_code: int,
                       headers: dict[str, str] | None = None) -> Response:
        return Response(
            content=json.dumps(payload), status_code=status_code,
            media_type="application/json", headers=headers,
        )

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> Response:
        """Record the trace, return an opaque body.

        _telegram_error below already establishes that raw exception text must
        never reach a client — a bot token travels in a URL that requests
        embeds in its exception strings. The same rule applies everywhere else,
        so the trace goes to the log and the caller gets a request id to quote.
        """
        request_id = getattr(request.state, "request_id", "")
        logger.exception(
            "unhandled error on %s %s (request_id=%s)",
            request.method, request.url.path, request_id,
        )
        return _json_response(
            {"detail": "internal error", "request_id": request_id}, 500,
            {"X-Request-Id": request_id} if request_id else None,
        )

    @app.middleware("http")
    async def observability(request: Request, call_next):
        request_id = secrets.token_hex(8)
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        # One structured line per request: without it an incident leaves
        # nothing behind but uvicorn's access log.
        logger.info(
            "request method=%s path=%s status=%s duration_ms=%s request_id=%s",
            request.method, request.url.path, response.status_code,
            duration_ms, request_id,
        )
        response.headers["X-Request-Id"] = request_id
        return response

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        maintenance = settings.data_root / "maintenance"
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and maintenance.exists():
            return Response(
                content=json.dumps({"detail": "system is in deployment maintenance"}),
                status_code=503, media_type="application/json", headers={"Retry-After": "60"},
            )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    static_root = Path(__file__).with_name("static")
    if static_root.exists():
        app.mount("/static", StaticFiles(directory=str(static_root)), name="static")

    def current_user(request: Request) -> dict[str, Any]:
        user = store.session(request.cookies.get(SESSION_COOKIE, ""))
        if user is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return user

    def csrf_user(
        request: Request,
        user: dict[str, Any] = Depends(current_user),
        x_csrf_token: str = Header(default=""),
    ) -> dict[str, Any]:
        if not x_csrf_token or not secrets.compare_digest(
            str(user["csrf_token"]), str(x_csrf_token)
        ):
            raise HTTPException(status_code=403, detail="invalid CSRF token")
        origin = str(request.headers.get("origin") or "").rstrip("/")
        if origin and origin != settings.public_base_url:
            raise HTTPException(status_code=403, detail="origin is not allowed")
        return user

    def require_admin(user: dict[str, Any]) -> dict[str, Any]:
        if user.get("role") != "platform_admin":
            raise HTTPException(status_code=403, detail="platform admin required")
        if (not user.get("totp_enabled") or not user.get("mfa_verified")) and not settings.dev_login_enabled:
            raise HTTPException(status_code=403, detail="verified TOTP is required for admin actions")
        return user

    def admin_read_user(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        return require_admin(user)

    def admin_user(user: dict[str, Any] = Depends(csrf_user)) -> dict[str, Any]:
        return require_admin(user)

    def ensure_user_workspace(user: dict[str, Any]) -> dict[str, Any]:
        """Provision or repair an active user's workspace before login.

        Invite acceptance is committed in SQLite before the privileged helper
        can create the OS user and tenant files.  A helper failure must remain
        recoverable: keep the accepted account, refuse to issue a session, and
        let the next authenticated login retry this idempotent boundary.
        """
        tenant, created = store.ensure_tenant(
            str(user["id"]), _safe_slug_from_email(str(user["email"]), str(user["id"]))
        )
        was_failed = tenant.get("status") == "provision_failed"
        if supervisor.workspace_ready(tenant["slug"]):
            if was_failed:
                tenant = store.update_tenant(tenant["id"], status="queued")
                store.audit(
                    "tenant_provision_repaired", tenant_id=tenant["id"],
                    user_id=str(user["id"]), payload={"source": "login_preflight"},
                )
            return tenant

        try:
            supervisor.provision(tenant["slug"])
            if not supervisor.workspace_ready(tenant["slug"]):
                raise RuntimeError("tenant provisioner returned without required workspace files")
        except Exception as exc:
            failed_status = (
                "degraded"
                if tenant.get("status") in {"starting", "running", "degraded"}
                else "provision_failed"
            )
            try:
                store.update_tenant(tenant["id"], status=failed_status)
                store.audit(
                    "tenant_provision_failed", tenant_id=tenant["id"],
                    user_id=str(user["id"]),
                    payload={"error_type": type(exc).__name__, "source": "login_preflight"},
                )
            except Exception:
                logger.exception(
                    "failed to persist tenant provisioning failure for tenant=%s",
                    tenant["slug"],
                )
            logger.exception("tenant workspace provisioning failed for tenant=%s", tenant["slug"])
            raise HTTPException(
                status_code=503,
                detail="workspace provisioning failed; sign in again to retry or contact support",
            ) from exc

        if was_failed:
            tenant = store.update_tenant(tenant["id"], status="queued")
        store.audit(
            "tenant_provisioned" if created else "tenant_provision_repaired",
            tenant_id=tenant["id"], user_id=str(user["id"]),
            payload={"source": "login_preflight"},
        )
        return tenant

    def own_tenant(user: dict[str, Any]) -> dict[str, Any]:
        if user.get("account_status") != "active":
            raise HTTPException(status_code=403, detail=f"account is {user.get('account_status')}")
        tenant = store.tenant_for_user(str(user["id"]))
        if tenant is None:
            raise HTTPException(status_code=409, detail="tenant is not provisioned")
        return tenant

    def _pool_for_symbol(tenant_id: str, symbol: str) -> dict[str, Any] | None:
        pool = store.strategy_pool(tenant_id, normalize_symbol(symbol))
        return normalize_pool(pool) if pool is not None else None

    def _pool_lock(tenant_id: str, symbol: str):
        key = f"{tenant_id}:{normalize_symbol(symbol)}"
        with pool_locks_guard:
            lock = pool_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                pool_locks[key] = lock
            return lock

    def _pool_response(pool: dict[str, Any]) -> dict[str, Any]:
        """Add current catalog evidence without storing mutable catalog data."""
        result = json.loads(json.dumps(pool))
        for item in result.get("candidates", []):
            try:
                preset = preset_by_id(str(item.get("preset_id") or ""))
            except ValueError:
                item["catalog_status"] = "removed"
                continue
            item["backtest"] = preset.get("backtest")
            item["strategy_traits"] = preset.get("strategy_traits") or []
            item["certification_note"] = preset.get("certification_note")
            item["certification_status"] = preset.get("certification_status")
            item["live_certified"] = bool(preset.get("live_certified"))
            if item.get("mode") == "shadow":
                # A pool candidate is not a running shadow process yet.  Keep
                # this explicit so the UI never presents metadata as telemetry.
                item.setdefault("shadow_runtime_status", "not_connected")
        return result

    def _current_candidate_preset(item: dict[str, Any]) -> dict[str, Any]:
        """Require a pool entry to still match the certified catalogue."""
        try:
            preset = preset_for_candidate(str(item.get("preset_id") or ""))
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail="candidate certificate is no longer valid; refresh the pair pool",
            ) from exc
        expected = str(item.get("certificate_config_fingerprint") or "").strip()
        if expected and expected != config_fingerprint(preset):
            raise HTTPException(
                status_code=409,
                detail="candidate certificate configuration changed; re-add the strategy",
            )
        return preset

    def _pair_is_flat(tenant: dict[str, Any], symbol: str) -> bool:
        """Return true only for a fresh, explicit flat runtime snapshot.

        Missing, stale, ambiguous, or stopped-engine state is deliberately not
        treated as flat.  This endpoint cannot prove exchange reconciliation by
        itself, so the safe default is to refuse promotion until the engine has
        published a fresh position snapshot.
        """
        if tenant.get("status") not in {"running", "degraded", "starting"}:
            return False
        return _explicit_flat_runtime_snapshot(runtime.snapshot(tenant["slug"]), symbol)

    def _profile_files_snapshot(slug: str) -> dict[str, tuple[bytes, int] | None]:
        """Capture tenant profile files before a promotion can rewrite them."""
        supervisor.provision(slug)
        snapshot: dict[str, tuple[bytes, int] | None] = {}
        for name in ("bot_config.yaml", "coin_whitelist.json"):
            path = supervisor.config_dir(slug) / name
            try:
                mode = stat.S_IMODE(path.stat().st_mode)
                snapshot[name] = (path.read_bytes(), mode)
            except OSError:
                snapshot[name] = None
        return snapshot

    def _restore_profile_files(slug: str, snapshot: dict[str, tuple[bytes, int] | None]) -> None:
        for name, item in snapshot.items():
            if item is None:
                continue
            data, mode = item
            atomic_bytes_write(str(supervisor.config_dir(slug) / name), data, mode=mode)

    def manual_order_context(tenant: dict[str, Any], symbol: str, intent: str) -> dict[str, Any]:
        if not settings.manual_trading_enabled:
            raise HTTPException(status_code=404, detail="manual trading is disabled")
        if tenant.get("status") not in {"running", "degraded"} or supervisor.status(tenant["slug"]) != "active":
            raise HTTPException(status_code=409, detail="tenant engine must be running")
        snapshot = runtime.snapshot(tenant["slug"])
        if not snapshot.get("ok") or snapshot.get("read_only") or snapshot.get("stale"):
            raise HTTPException(status_code=409, detail="fresh tenant-engine data is required")
        snapshot_age = _finite_float(snapshot.get("age_sec"))
        if snapshot_age is None or snapshot_age < 0 or snapshot_age > 30:
            raise HTTPException(status_code=409, detail="market snapshot is too old")
        state = snapshot.get("state") if isinstance(snapshot.get("state"), dict) else {}
        clean_symbol = RuntimeGateway._symbol(symbol)
        profile = store.trading_profile(tenant["id"])
        if not profile:
            raise HTTPException(status_code=409, detail="a trading profile is required")
        preset = next(
            (
                item
                for item in profile.get("presets", [])
                if str(item.get("symbol") or "").upper() == clean_symbol
            ),
            None,
        )
        if not preset:
            raise HTTPException(status_code=409, detail="symbol is not a configured trading pair")
        focus = RuntimeGateway._focus_state(state, clean_symbol)
        position = focus.get("position") if isinstance(focus.get("position"), dict) else {}
        is_open = str(position.get("state") or "idle").strip().lower() == "bought"
        if intent == "CLOSE_POSITION" and not is_open:
            raise HTTPException(status_code=409, detail="there is no tracked position to close")
        if intent != "CLOSE_POSITION" and is_open:
            raise HTTPException(status_code=409, detail="close the current position before opening another")
        target = target_by_id(str(profile.get("target_id") or preset.get("target_id") or ""))
        requested_side = "short" if intent == "OPEN_SHORT" else "long"
        if intent != "CLOSE_POSITION" and requested_side not in target.get("manual_allowed_sides", []):
            raise HTTPException(status_code=409, detail=f"{requested_side.upper()} is unavailable for this market")
        live = tenant.get("live_status") == "active"
        certification = f"manual_{requested_side}_live_certified"
        if live and intent != "CLOSE_POSITION" and not target.get(certification):
            raise HTTPException(status_code=409, detail=f"Live {requested_side.upper()} is not certified")
        raw_mark = focus.get("current_price")
        if raw_mark is None or raw_mark == "":
            raw_mark = position.get("mark_price")
        mark = _finite_float(raw_mark)
        if mark is None or mark <= 0:
            raise HTTPException(status_code=409, detail="a valid market price is required")
        return {
            "snapshot": snapshot, "focus": focus, "position": position,
            "profile": profile, "preset": preset, "target": target,
            "symbol": clean_symbol, "intent": intent, "live": live, "mark_price": mark,
        }

    @app.get("/")
    def index():
        return {"service": "xauby-control", "frontend": settings.public_base_url}

    @app.get("/login")
    def login_page():
        return RedirectResponse(f"{settings.public_base_url}/login")

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "time": time.time()}

    def set_session_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            SESSION_COOKIE, token, httponly=True, secure=settings.cookie_secure,
            samesite="lax", max_age=604800, path="/",
        )

    @app.post("/auth/signup", status_code=202)
    def signup(body: SignupBody):
        raise HTTPException(status_code=404, detail="public signup is disabled")

    @app.get("/auth/invite")
    def invite_details(token: str):
        invite = store.invite_for_token(token)
        if not invite:
            raise HTTPException(status_code=404, detail="invitation is invalid or expired")
        return {"email": invite["email"], "expires_at": invite["expires_at"]}

    @app.post("/auth/invite/accept")
    def invite_accept(body: InviteAcceptBody, response: Response):
        if store.user_count() >= settings.max_users:
            raise HTTPException(status_code=409, detail="pilot capacity is full")
        try:
            user = store.accept_invite(body.token, password=body.password)
            ensure_user_workspace(user)
            token, csrf = store.create_session(user["id"])
            set_session_cookie(response, token)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True, "csrf_token": csrf, "status": "active"}

    @app.post("/auth/verify-email")
    def verify_email(body: TokenBody):
        try:
            user = store.verify_email_token(body.token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "status": user["account_status"]}

    def throttle_key(request: Request, email: str) -> str:
        client = request.client.host if request.client else "unknown"
        return f"{client}|{str(email or '').strip().lower()}"

    def require_not_throttled(throttle: AttemptThrottle, key: str) -> None:
        retry_after = throttle.retry_after(key)
        if retry_after:
            raise HTTPException(
                status_code=429,
                detail="too many attempts; try again later",
                headers={"Retry-After": str(retry_after)},
            )

    @app.post("/auth/login")
    def password_login(body: LoginBody, request: Request, response: Response):
        key = throttle_key(request, body.email)
        require_not_throttled(login_throttle, key)
        # The throttle above is keyed on the client address, which reaches us
        # through a reverse proxy; this one lives on the account row, so it
        # holds even if the forwarding chain is ever misconfigured.
        account_retry_after = store.login_retry_after(body.email)
        if account_retry_after:
            raise HTTPException(
                status_code=429,
                detail="too many attempts; try again later",
                headers={"Retry-After": str(account_retry_after)},
            )
        user = store.authenticate_password(body.email, body.password)
        if not user:
            login_throttle.record_failure(key)
            raise HTTPException(status_code=401, detail="email or password is incorrect")
        if not user.get("email_verified"):
            raise HTTPException(status_code=403, detail="email verification is required")
        if user.get("account_status") in {"rejected", "suspended"}:
            raise HTTPException(status_code=403, detail=f"account is {user['account_status']}")
        mfa_ok = not bool(user.get("totp_enabled"))
        if user.get("totp_enabled"):
            mfa_ok = verify_totp(str(user.get("totp_secret") or ""), body.totp_code)
            if not mfa_ok and body.totp_code:
                # A lost authenticator is recovered with one of the single-use
                # codes issued at enrolment; each match is consumed atomically.
                mfa_ok = store.use_recovery_code(user["id"], body.totp_code)
            if not mfa_ok:
                login_throttle.record_failure(key)
                raise HTTPException(status_code=403, detail="valid TOTP or recovery code is required")
        if user.get("account_status") == "active":
            ensure_user_workspace(user)
        login_throttle.clear(key)
        token, csrf = store.create_session(user["id"], mfa_verified=mfa_ok)
        set_session_cookie(response, token)
        store.audit("login", user_id=user["id"])
        return {"ok": True, "csrf_token": csrf, "status": user["account_status"]}

    @app.post("/auth/forgot-password")
    def forgot_password(body: EmailBody, request: Request):
        key = throttle_key(request, body.email)
        require_not_throttled(email_throttle, key)
        email_throttle.record_failure(key)
        user = store.user_by_email(body.email)
        if user and user.get("password_hash"):
            token = store.create_auth_token(user["id"], "password_reset", ttl_seconds=1800)
            try:
                mailer.send(
                    user["email"], "Reset your xAuby password",
                    f"This link is valid for 30 minutes:\n{settings.public_base_url}/reset-password?token={quote(token)}",
                )
            except RuntimeError:
                pass
        return {"ok": True}

    @app.post("/auth/reset-password")
    def reset_password(body: ResetPasswordBody):
        try:
            user, _ = store.consume_auth_token(body.token, "password_reset")
            store.set_password(user["id"], body.password)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/auth/change-password")
    def change_password(body: ChangePasswordBody, user: dict[str, Any] = Depends(csrf_user)):
        if not user.get("password_hash") or not verify_password(
            str(user["password_hash"]), body.current_password
        ):
            raise HTTPException(status_code=403, detail="current password is incorrect")
        try:
            store.set_password(user["id"], body.new_password)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True, "reauthentication_required": True}

    @app.post("/auth/change-email")
    def change_email(body: EmailBody, user: dict[str, Any] = Depends(csrf_user)):
        try:
            token = store.begin_email_change(user["id"], body.email)
            mailer.send(
                body.email, "Confirm your new xAuby email",
                f"Confirm within one hour:\n{settings.public_base_url}/confirm-email?token={quote(token)}",
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/auth/confirm-email")
    def confirm_email(body: TokenBody):
        try:
            store.confirm_email_change(body.token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "reauthentication_required": True}

    @app.post("/auth/totp/setup")
    def totp_setup(body: TotpSetupBody | None = None,
                   user: dict[str, Any] = Depends(csrf_user)):
        if user.get("totp_enabled"):
            # Re-enrolment silently disables the existing factor, so a stolen
            # session must not be enough to rotate it.
            supplied = str(body.current_code if body else "").strip()
            code_ok = verify_totp(str(user.get("totp_secret") or ""), supplied)
            if not code_ok and supplied:
                code_ok = store.use_recovery_code(str(user["id"]), supplied)
            if not code_ok:
                raise HTTPException(
                    status_code=403,
                    detail="current TOTP or recovery code is required to reset the authenticator",
                )
        secret = new_totp_secret()
        store.set_totp_secret(user["id"], secret)
        label = quote(f"xAuby:{user['email']}")
        return {"secret": secret, "otpauth_uri": f"otpauth://totp/{label}?secret={secret}&issuer=xAuby"}

    @app.post("/auth/totp/enable")
    def totp_enable(body: TotpBody, request: Request,
                    user: dict[str, Any] = Depends(csrf_user)):
        pending_secret = str(user.get("pending_totp_secret") or "")
        if not pending_secret or not verify_totp(pending_secret, body.code):
            raise HTTPException(status_code=400, detail="TOTP code is invalid")
        codes = [secrets.token_hex(5).upper() for _ in range(8)]
        try:
            store.enable_totp(user["id"], codes)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        store.mark_session_mfa(request.cookies.get(SESSION_COOKIE, ""))
        return {"ok": True, "recovery_codes": codes}

    @app.post("/auth/totp/challenge")
    def totp_challenge(body: TotpBody, request: Request,
                       user: dict[str, Any] = Depends(csrf_user)):
        if not user.get("totp_enabled") or not verify_totp(user.get("totp_secret", ""), body.code):
            raise HTTPException(status_code=403, detail="TOTP code is invalid")
        store.mark_session_mfa(request.cookies.get(SESSION_COOKIE, ""))
        return {"ok": True}

    @app.get("/auth/google/start")
    def google_start(invite: str = ""):
        if not settings.google_client_id or not settings.google_client_secret:
            raise HTTPException(status_code=503, detail="Google sign-in is not configured")
        nonce = secrets.token_urlsafe(24)
        if invite and not store.invite_for_token(invite):
            raise HTTPException(status_code=404, detail="invitation is invalid or expired")
        signed = sign_state(
            settings.session_secret, {"nonce": nonce, "invite": invite}, ttl_seconds=600
        )
        params = {
            "client_id": settings.google_client_id,
            "redirect_uri": f"{settings.public_base_url}/auth/google/callback",
            "response_type": "code",
            "scope": "openid email profile",
            "state": nonce,
            "prompt": "select_account",
        }
        response = RedirectResponse(
            "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
        )
        response.set_cookie(
            OAUTH_STATE_COOKIE, signed, httponly=True, secure=settings.cookie_secure,
            samesite="lax", max_age=600,
        )
        return response

    @app.get("/auth/google/callback")
    def google_callback(request: Request, code: str = "", state: str = ""):
        signed = request.cookies.get(OAUTH_STATE_COOKIE, "")
        state_payload = verify_state(settings.session_secret, signed)
        if not code or not state_payload or state_payload.get("nonce") != state:
            raise HTTPException(status_code=400, detail="invalid OAuth state")
        token_response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code, "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": f"{settings.public_base_url}/auth/google/callback",
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
        token_response.raise_for_status()
        id_token = str(token_response.json().get("id_token") or "")
        claims_response = requests.get(
            "https://oauth2.googleapis.com/tokeninfo", params={"id_token": id_token}, timeout=10
        )
        claims_response.raise_for_status()
        claims = claims_response.json()
        if (
            claims.get("aud") != settings.google_client_id
            or claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}
            or str(claims.get("email_verified")).lower() not in {"1", "true", "yes"}
        ):
            raise HTTPException(status_code=403, detail="Google identity was not verified")
        email = str(claims.get("email") or "").lower()
        existing = store.user_by_email(email)
        if existing is None and store.user_count() >= settings.max_users:
            raise HTTPException(status_code=409, detail="pilot capacity is full")
        if existing is None:
            invite_token = str(state_payload.get("invite") or "")
            invite = store.invite_for_token(invite_token)
            if not invite or invite["email"] != email:
                raise HTTPException(status_code=403, detail="an invitation is required")
            user = store.accept_invite(
                invite_token, google_sub=str(claims.get("sub") or "")
            )
        else:
            user, _ = store.upsert_google_user(email, str(claims.get("sub") or ""))
        if user.get("account_status") in {"rejected", "suspended"}:
            # Password login enforces this; the OAuth path must not become a
            # side door for disabled accounts.
            raise HTTPException(status_code=403, detail=f"account is {user['account_status']}")
        tenant = (
            ensure_user_workspace(user)
            if user.get("account_status") == "active"
            else store.tenant_for_user(str(user["id"]))
        )
        token, _ = store.create_session(str(user["id"]), mfa_verified=not bool(user.get("totp_enabled")))
        store.audit("login", tenant_id=tenant["id"] if tenant else None, user_id=user["id"])
        response = RedirectResponse(f"{settings.public_base_url}/app")
        response.delete_cookie(OAUTH_STATE_COOKIE)
        set_session_cookie(response, token)
        return response

    @app.post("/auth/dev-login")
    def dev_login(response: Response, email: str):
        if not settings.dev_login_enabled:
            raise HTTPException(status_code=404)
        user, _ = store.upsert_google_user(email, f"dev:{email.lower()}")
        user = store.set_account_status(user["id"], "active", user["id"])
        ensure_user_workspace(user)
        token, csrf = store.create_session(user["id"], mfa_verified=True)
        # Through the shared helper like every other login path. Setting the
        # cookie inline here meant it carried neither `secure` nor `path`, so if
        # dev_login_enabled were ever true in production this route would issue
        # a non-Secure, MFA-bypassing session. The 404 above is the real guard;
        # this removes the second, weaker copy behind it.
        set_session_cookie(response, token)
        return {"ok": True, "csrf_token": csrf}

    @app.post("/auth/logout")
    def logout(request: Request, response: Response, user: dict[str, Any] = Depends(csrf_user)):
        store.revoke_session(request.cookies.get(SESSION_COOKIE, ""))
        response.delete_cookie(SESSION_COOKIE)
        store.audit("logout", user_id=user["id"])
        return {"ok": True}

    @app.get("/api/v1/me")
    def me(user: dict[str, Any] = Depends(current_user)):
        tenant = store.tenant_for_user(str(user["id"]))
        avatar_version = int(user.get("avatar_version") or 0)
        return {
            "id": user["id"], "email": user["email"], "role": user["role"],
            "display_name": user.get("display_name") or "",
            "avatar_url": (
                f"/api/v1/profile/avatar?v={avatar_version}"
                if user.get("avatar_ext") else None
            ),
            "csrf_token": user["csrf_token"], "tenant": tenant,
            "account_status": user.get("account_status"),
            "email_verified": bool(user.get("email_verified")),
            "password_configured": bool(user.get("password_hash")),
            "totp_enabled": bool(user.get("totp_enabled")),
            "mfa_verified": bool(user.get("mfa_verified")),
            "trade_pin_configured": bool(user.get("trade_pin_hash")),
        }

    @app.patch("/api/v1/profile/appearance")
    def profile_appearance(
        body: ProfileAppearanceBody,
        user: dict[str, Any] = Depends(csrf_user),
    ):
        display_name = re.sub(r"\s+", " ", body.display_name).strip()
        if not display_name:
            raise HTTPException(status_code=422, detail="display name is required")
        current_ext = str(user.get("avatar_ext") or "") or None
        avatar_ext = current_ext
        avatar_changed = False
        if body.remove_avatar:
            avatar_ext = None
            avatar_changed = current_ext is not None
            for ext in ("png", "jpg", "webp"):
                (avatar_root / f"{user['id']}.{ext}").unlink(missing_ok=True)
        elif body.avatar_data_url:
            try:
                header, encoded = body.avatar_data_url.split(",", 1)
                media_type = header.removeprefix("data:").split(";", 1)[0].lower()
                ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[media_type]
                image = base64.b64decode(encoded, validate=True)
            except (ValueError, KeyError, binascii.Error) as exc:
                raise HTTPException(status_code=422, detail="avatar must be PNG, JPEG or WebP") from exc
            if not 1 <= len(image) <= 1_000_000:
                raise HTTPException(status_code=422, detail="avatar must be 1 MB or smaller")
            valid_magic = (
                (ext == "png" and image.startswith(b"\x89PNG\r\n\x1a\n"))
                or (ext == "jpg" and image.startswith(b"\xff\xd8\xff"))
                or (ext == "webp" and image.startswith(b"RIFF") and image[8:12] == b"WEBP")
            )
            if not valid_magic:
                raise HTTPException(status_code=422, detail="avatar file signature is invalid")
            atomic_bytes_write(str(avatar_root / f"{user['id']}.{ext}"), image)
            for old_ext in ("png", "jpg", "webp"):
                if old_ext != ext:
                    (avatar_root / f"{user['id']}.{old_ext}").unlink(missing_ok=True)
            avatar_ext = ext
            avatar_changed = True
        updated = store.update_user_appearance(
            user["id"],
            display_name=display_name,
            avatar_ext=avatar_ext,
            avatar_changed=avatar_changed,
        )
        return {
            "ok": True,
            "display_name": updated.get("display_name") or "",
            "avatar_url": (
                f"/api/v1/profile/avatar?v={int(updated.get('avatar_version') or 0)}"
                if updated.get("avatar_ext") else None
            ),
        }

    @app.get("/api/v1/profile/avatar")
    def profile_avatar(user: dict[str, Any] = Depends(current_user)):
        ext = str(user.get("avatar_ext") or "")
        media_type = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}.get(ext)
        path = avatar_root / f"{user['id']}.{ext}"
        if not media_type or not path.is_file():
            raise HTTPException(status_code=404, detail="profile image is not configured")
        return Response(
            content=path.read_bytes(),
            media_type=media_type,
            headers={"Cache-Control": "private, max-age=300", "Content-Disposition": "inline"},
        )

    @app.get("/api/v1/catalog")
    def catalog(user: dict[str, Any] = Depends(current_user)):
        result = public_catalog()
        result["features"]["manual_trading"] = settings.manual_trading_enabled
        return result

    @app.get("/api/v1/profile")
    def profile_get(user: dict[str, Any] = Depends(current_user)):
        tenant = own_tenant(user)
        return {"profile": store.trading_profile(tenant["id"]),
                "compiled": supervisor.read_curated_config(tenant["slug"])}

    @app.put("/api/v1/profile")
    def profile_put(body: TradingProfileBody, user: dict[str, Any] = Depends(csrf_user)):
        tenant = own_tenant(user)
        try:
            profile = validate_profile(body.preset_ids, body.active_preset_id, body.risk)
            existing = store.trading_profile(tenant["id"])
            profile_changed = existing != profile
            live_was_enabled = tenant["live_status"] in {"requested", "approved", "active"}

            # Saving the already-active preset is an idempotent action.  The old
            # path rebuilt the tenant files and unconditionally called
            # ``set_sim_mode`` even when nothing had changed.  That made a
            # harmless second click stop a Live engine and silently downgrade it
            # to paper trading.  Actual profile changes still take the existing
            # fail-closed re-approval path below.
            if not profile_changed:
                return {
                    "ok": True,
                    "profile": existing,
                    "mode": "live" if tenant["live_status"] == "active" else "simulation",
                    "live_reapproval_required": False,
                    "profile_changed": False,
                }

            exchange_switched = bool(
                existing and existing.get("target_id")
                and existing.get("target_id") != profile["target_id"]
            )
            live_addition = bool(
                tenant["live_status"] == "active"
                and _is_safe_live_pair_addition(existing, profile)
            )
            if live_was_enabled and not live_addition:
                supervisor.stop(tenant["slug"])
                store.update_tenant(tenant["id"], status="stopped")
                store.reset_live_approval(tenant["id"], user["id"], "trading profile changed")
            elif exchange_switched and tenant.get("status") == "running":
                # A running sim engine still holds the old exchange config —
                # stop it so the next start picks up the new venue cleanly.
                supervisor.stop(tenant["slug"])
                store.update_tenant(tenant["id"], status="stopped")
            compiled = supervisor.apply_profile(
                tenant["slug"], body.preset_ids, body.active_preset_id, body.risk,
                preserve_live=live_addition,
            )
            store.save_trading_profile(tenant["id"], user["id"], profile)
            target = target_by_id(profile["target_id"])
            store.update_tenant(
                tenant["id"], exchange_id=target["exchange_id"],
                market_type=target["market_type"],
            )
            if live_addition:
                store.audit(
                    "live_pairs_hot_added",
                    tenant_id=tenant["id"],
                    user_id=user["id"],
                    payload={
                        "added_preset_ids": sorted(
                            set(profile["preset_ids"]) - set(existing["preset_ids"])
                        ),
                        "max_open_positions": profile["risk"]["max_open_positions"],
                    },
                )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        connection = store.exchange_connection(tenant["id"])
        reconnect_required = exchange_switched or bool(
            connection and connection.get("target_id")
            and connection.get("target_id") != profile["target_id"]
        )
        return {
            "ok": True,
            "profile": compiled,
            "mode": "live" if live_addition else "simulation",
            "live_reapproval_required": live_was_enabled and not live_addition,
            "live_preserved": live_addition,
            "hot_reload_eta_seconds": 30 if live_addition else None,
            "profile_changed": True,
            "exchange_switched": exchange_switched,
            "reconnect_required": reconnect_required,
        }

    @app.get("/api/v1/strategy-pools")
    def strategy_pools_get(user: dict[str, Any] = Depends(current_user)):
        tenant = own_tenant(user)
        # Existing profiles get an Arena automatically.  This keeps the first
        # rollout additive: opening the page never changes the running engine.
        profile = store.trading_profile(tenant["id"])
        if profile:
            for preset in profile.get("presets", []):
                if preset.get("certification_status") != "certified":
                    continue
                symbol = normalize_symbol(str(preset.get("symbol") or ""))
                if not symbol or _pool_for_symbol(tenant["id"], symbol):
                    continue
                try:
                    with _pool_lock(tenant["id"], symbol):
                        if _pool_for_symbol(tenant["id"], symbol) is None:
                            store.save_strategy_pool(
                                tenant["id"],
                                user["id"],
                                new_pool(symbol, str(preset.get("target_id") or ""), preset),
                            )
                except ControlPlaneStore.StrategyPoolConflict:
                    # Another browser or worker created the same lazy pool.
                    # The subsequent read is authoritative.
                    pass
        pools = [_pool_response(pool) for pool in store.strategy_pools(tenant["id"])]
        return {
            "pools": pools,
            "promotion_mode": "manual",
            "max_candidates": MAX_CANDIDATES,
            "shadow_runtime": "not_connected",
            "tenant_live_status": tenant.get("live_status", "not_requested"),
        }

    @app.get("/api/v1/strategy-pools/{symbol}")
    def strategy_pool_get(symbol: str, user: dict[str, Any] = Depends(current_user)):
        tenant = own_tenant(user)
        pool = _pool_for_symbol(tenant["id"], symbol)
        if pool is None:
            raise HTTPException(status_code=404, detail="strategy pool not found")
        return {
            "pool": _pool_response(pool),
            "promotion_mode": "manual",
            "shadow_runtime": "not_connected",
            "tenant_live_status": tenant.get("live_status", "not_requested"),
        }

    @app.post("/api/v1/strategy-pools/{symbol}/candidates", status_code=201)
    def strategy_candidate_add(
        symbol: str,
        body: StrategyCandidateBody,
        user: dict[str, Any] = Depends(csrf_user),
    ):
        tenant = own_tenant(user)
        try:
            preset = preset_for_candidate(body.preset_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        compact = normalize_symbol(symbol)
        if normalize_symbol(str(preset.get("symbol") or "")) != compact:
            raise HTTPException(status_code=422, detail="strategy symbol does not match the pair")
        profile = store.trading_profile(tenant["id"])
        if profile and str(profile.get("target_id")) != str(preset.get("target_id")):
            raise HTTPException(status_code=422, detail="strategy target does not match the saved profile")
        try:
            with _pool_lock(tenant["id"], compact):
                pool = _pool_for_symbol(tenant["id"], compact)
                if pool is None:
                    pool = new_pool(compact, str(preset.get("target_id") or ""), preset)
                else:
                    append_candidate(pool, preset)
                pool.setdefault("history", []).append({
                    "event": "candidate_added",
                    "preset_id": preset["id"],
                    "at": time.time(),
                    "by": user["id"],
                })
                saved = store.save_strategy_pool(tenant["id"], user["id"], pool)
        except ControlPlaneStore.StrategyPoolConflict as exc:
            raise HTTPException(status_code=409, detail="strategy pool changed; refresh and retry") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "pool": _pool_response(saved)}

    @app.patch("/api/v1/strategy-pools/{symbol}/candidates/{candidate_id}/evaluation")
    def strategy_candidate_evaluate(
        symbol: str,
        candidate_id: str,
        body: StrategyEvaluationBody,
        user: dict[str, Any] = Depends(admin_user),
    ):
        """Record a reviewed evaluator result.

        Heavy replay/backtest stays on the runner.  This endpoint stores the
        run provenance and is admin-gated; missing or mismatched provenance is
        retained for diagnostics but can never make a candidate eligible.
        """
        tenant = own_tenant(user)
        compact = normalize_symbol(symbol)
        try:
            with _pool_lock(tenant["id"], compact):
                pool = _pool_for_symbol(tenant["id"], compact)
                if pool is None:
                    raise HTTPException(status_code=404, detail="strategy pool not found")
                item = candidate(pool, candidate_id)
                if item is None:
                    raise HTTPException(status_code=404, detail="strategy candidate not found")
                _current_candidate_preset(item)
                metrics = body.model_dump(exclude={"winning_evaluations"})
                pool, record, inserted = append_evaluation(pool, candidate_id, metrics)
                if not inserted:
                    return {
                        "ok": True,
                        "idempotent": True,
                        "eligible": bool(record.get("eligible_for_promotion")),
                        "reasons": list(record.get("eligibility_reasons") or []),
                        "pool": _pool_response(pool),
                    }
                saved = store.save_strategy_pool(tenant["id"], user["id"], pool)
        except ControlPlaneStore.StrategyPoolConflict as exc:
            raise HTTPException(status_code=409, detail="strategy pool changed; refresh and retry") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "ok": True,
            "idempotent": False,
            "eligible": bool(record.get("eligible_for_promotion")),
            "reasons": list(record.get("eligibility_reasons") or []),
            "evaluation": record,
            "pool": _pool_response(saved),
        }

    @app.post("/api/v1/strategy-pools/{symbol}/promote")
    def strategy_pool_promote(
        symbol: str,
        body: StrategyPromotionBody,
        user: dict[str, Any] = Depends(csrf_user),
    ):
        tenant = own_tenant(user)
        ok, reason = store.check_trade_pin(user["id"], body.trade_pin)
        if not ok:
            raise HTTPException(status_code=403, detail=reason)
        compact = normalize_symbol(symbol)
        with _pool_lock(tenant["id"], compact):
            pool = _pool_for_symbol(tenant["id"], compact)
            if pool is None:
                raise HTTPException(status_code=404, detail="strategy pool not found")
            challenger = candidate(pool, body.challenger_id)
            if challenger is None:
                raise HTTPException(status_code=404, detail="strategy candidate not found")
            challenger_preset = _current_candidate_preset(challenger)
            if challenger.get("role") == "champion":
                return {"ok": True, "pool": _pool_response(pool), "changed": False}
            champion = candidate(pool, str(pool.get("champion_id") or ""))
            champion_score = None if champion is None else float(
                (champion.get("evaluation") or {}).get("score", -9999.0)
            )
            comparable = None if champion is None else evaluations_comparable(
                challenger.get("evaluation"), champion.get("evaluation")
            )
            eligible, reasons = promotion_eligibility(
                challenger.get("evaluation"),
                policy=pool.get("policy"),
                champion_score=champion_score,
                winning_evaluations=int(challenger.get("winning_evaluations") or 0),
                comparable_champion=comparable,
                require_provenance=True,
            )
            for provenance_reason in candidate_evaluation_provenance_reasons(
                challenger, challenger.get("evaluation")
            ):
                if provenance_reason not in reasons:
                    reasons.append(provenance_reason)
            eligible = not reasons
            if not eligible:
                raise HTTPException(
                    status_code=409,
                    detail={"message": "candidate is not ready", "reasons": reasons},
                )
            if not _pair_is_flat(tenant, compact):
                raise HTTPException(
                    status_code=409,
                    detail="fresh explicit flat runtime state is required before promotion",
                )

            live_was_enabled = tenant["live_status"] in {"requested", "approved", "active"}
            if live_was_enabled and not bool(challenger_preset.get("live_certified")):
                raise HTTPException(
                    status_code=409,
                    detail="a live tenant may only promote a live-certified strategy",
                )
            profile = store.trading_profile(tenant["id"])
            if not profile:
                raise HTTPException(status_code=409, detail="save the pair in Settings before promoting it")
            current_ids = list(profile.get("preset_ids") or [])
            old_id = champion.get("preset_id") if champion else None
            if old_id in current_ids:
                current_ids[current_ids.index(old_id)] = body.challenger_id
            elif body.challenger_id not in current_ids:
                raise HTTPException(
                    status_code=409,
                    detail="the pair is not active in the saved trading profile",
                )
            active_id = (
                body.challenger_id
                if profile.get("active_preset_id") == old_id
                else profile.get("active_preset_id")
            )
            try:
                replacement = validate_profile(current_ids, active_id, profile.get("risk") or {})
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

            # Files and the control-plane rows live in different stores.  Keep a
            # rollback target and force simulation on a partial live failure so
            # an exception cannot leave a stopped service with live credentials.
            old_profile = deepcopy(profile)
            file_snapshot = _profile_files_snapshot(tenant["slug"])
            mutation_started = False
            try:
                if live_was_enabled:
                    # A strategy handoff remains a safe re-approval boundary;
                    # it never hot-switches a running Live engine.
                    supervisor.stop(tenant["slug"])
                    store.update_tenant(tenant["id"], status="stopped")
                    store.reset_live_approval(
                        tenant["id"], user["id"], "strategy champion promoted"
                    )
                mutation_started = True
                supervisor.apply_profile(
                    tenant["slug"], replacement["preset_ids"], replacement["active_preset_id"],
                    replacement["risk"], preserve_live=False,
                )
                store.save_trading_profile(tenant["id"], user["id"], replacement)

                next_pool = deepcopy(pool)
                next_champion = candidate(next_pool, str(next_pool.get("champion_id") or ""))
                next_challenger = candidate(next_pool, body.challenger_id)
                if next_challenger is None:
                    raise ValueError("strategy candidate disappeared during promotion")
                if next_champion:
                    next_champion["role"] = "challenger"
                    next_champion["mode"] = "shadow"
                    next_champion["status"] = "active"
                next_challenger["role"] = "champion"
                next_challenger["mode"] = "live" if next_challenger.get("live_certified") else "shadow"
                next_challenger["status"] = "active"
                next_challenger["winning_evaluations"] = 0
                next_challenger["eligible_for_promotion"] = False
                next_challenger["eligibility_reasons"] = ["already Champion"]
                next_pool["champion_id"] = body.challenger_id
                next_pool["promotion"] = {
                    "from": old_id,
                    "to": body.challenger_id,
                    "status": (
                        "applied_requires_live_reapproval"
                        if live_was_enabled
                        else "applied_simulation"
                    ),
                    "at": time.time(),
                    "by": user["id"],
                }
                next_pool.setdefault("history", []).append(next_pool["promotion"])
                saved = store.save_strategy_pool(tenant["id"], user["id"], next_pool)
            except ControlPlaneStore.StrategyPoolConflict as exc:
                if mutation_started:
                    if live_was_enabled:
                        try:
                            # Put the previous strategy back first, then force
                            # every execution gate to simulation.  Restoring
                            # the old files alone could accidentally re-enable
                            # Live credentials after the approval was reset.
                            _restore_profile_files(tenant["slug"], file_snapshot)
                        except Exception:
                            logger.exception("failed to restore tenant profile after pool conflict")
                        try:
                            supervisor.set_sim_mode(tenant["slug"])
                        except Exception:
                            logger.exception("failed to force simulation after pool conflict")
                    else:
                        try:
                            _restore_profile_files(tenant["slug"], file_snapshot)
                        except Exception:
                            logger.exception("failed to restore tenant profile after pool conflict")
                    try:
                        # Do not overwrite a profile changed concurrently by a
                        # settings request while this handoff was unwinding.
                        current_profile = store.trading_profile(tenant["id"])
                        if current_profile == replacement:
                            store.save_trading_profile(tenant["id"], user["id"], old_profile)
                    except Exception:
                        logger.exception("failed to restore trading profile after pool conflict")
                raise HTTPException(
                    status_code=409,
                    detail="strategy pool changed; no promotion was committed, refresh and retry",
                ) from exc
            except Exception as exc:
                if mutation_started:
                    if live_was_enabled:
                        try:
                            _restore_profile_files(tenant["slug"], file_snapshot)
                        except Exception:
                            logger.exception("failed to restore tenant profile after promotion failure")
                        try:
                            supervisor.set_sim_mode(tenant["slug"])
                        except Exception:
                            logger.exception("failed to force simulation after promotion failure")
                    else:
                        try:
                            _restore_profile_files(tenant["slug"], file_snapshot)
                        except Exception:
                            logger.exception("failed to restore tenant profile after promotion failure")
                    try:
                        current_profile = store.trading_profile(tenant["id"])
                        if current_profile == replacement:
                            store.save_trading_profile(tenant["id"], user["id"], old_profile)
                    except Exception:
                        logger.exception("failed to restore trading profile after promotion failure")
                logger.exception("strategy promotion failed safely for %s", compact)
                raise HTTPException(
                    status_code=502,
                    detail="promotion failed safely; Live remains disabled until re-approval",
                ) from exc
            try:
                store.audit(
                    "strategy_champion_promoted",
                    tenant_id=tenant["id"],
                    user_id=user["id"],
                    payload={"symbol": compact, "from": old_id, "to": body.challenger_id},
                )
            except Exception:
                # The pool write is already committed; do not report a failed
                # handoff that could cause a caller to retry and promote twice.
                logger.exception("promotion committed but audit append failed for %s", compact)
            return {
                "ok": True,
                "changed": True,
                "requires_live_reapproval": live_was_enabled,
                "pool": _pool_response(saved),
            }

    @app.get("/api/v1/bot")
    def bot(user: dict[str, Any] = Depends(current_user)):
        tenant = own_tenant(user)
        return {
            "tenant": tenant,
            "service_status": supervisor.status(tenant["slug"]),
            "state": supervisor.read_state(tenant["slug"]),
            "exchange_connection": store.exchange_connection(tenant["id"]),
            "telegram_connection": store.telegram_connection(tenant["id"]),
            "api_whitelist_ips": settings.whitelist_ip_list(),
        }

    @app.get("/api/v1/runtime/snapshot")
    def runtime_snapshot(user: dict[str, Any] = Depends(current_user)):
        tenant = own_tenant(user)
        return runtime.snapshot(tenant["slug"])

    @app.get("/api/v1/runtime/price")
    def runtime_price(
        symbol: str,
        user: dict[str, Any] = Depends(current_user),
    ):
        tenant = own_tenant(user)
        try:
            return runtime.price(tenant["slug"], symbol=symbol)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/runtime/candles")
    def runtime_candles(
        symbol: str,
        timeframe: str = "4h",
        limit: int = 48,
        user: dict[str, Any] = Depends(current_user),
    ):
        tenant = own_tenant(user)
        try:
            return runtime.candles(
                tenant["slug"], symbol=symbol, timeframe=timeframe, limit=limit
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/runtime/activity")
    def runtime_activity(
        symbol: str = "XAUUSDT",
        limit: int = 30,
        user: dict[str, Any] = Depends(current_user),
    ):
        tenant = own_tenant(user)
        try:
            return runtime.activity(tenant["slug"], symbol=symbol, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/runtime/trades")
    def runtime_trades(
        symbol: str | None = None,
        outcome: str = "all",
        limit: int = 50,
        cursor: int | None = None,
        user: dict[str, Any] = Depends(current_user),
    ):
        tenant = own_tenant(user)
        try:
            return runtime.trades(
                tenant["slug"], symbol=symbol, outcome=outcome, limit=limit, cursor=cursor
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/bot/start")
    def bot_start(user: dict[str, Any] = Depends(csrf_user)):
        tenant = own_tenant(user)
        if not store.reserve_engine_slot(tenant["id"], settings.max_active_engines):
            raise HTTPException(status_code=409, detail="engine capacity is full; tenant queued")
        try:
            result = supervisor.start(tenant["slug"])
        except Exception as exc:
            store.update_tenant(tenant["id"], status="error")
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        store.update_tenant(tenant["id"], status="running")
        store.audit("engine_started", tenant_id=tenant["id"], user_id=user["id"])
        return result

    @app.post("/api/v1/bot/stop")
    def bot_stop(user: dict[str, Any] = Depends(csrf_user)):
        tenant = own_tenant(user)
        try:
            result = supervisor.stop(tenant["slug"])
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        store.update_tenant(tenant["id"], status="stopped")
        store.audit("engine_stopped", tenant_id=tenant["id"], user_id=user["id"])
        return result

    @app.post("/api/v1/bot/restart")
    def bot_restart(user: dict[str, Any] = Depends(csrf_user)):
        """Apply config the engine only reads at construction (e.g. Telegram).

        supervisor.restart re-runs materialize_credentials first, so the engine
        comes back with the current env file. A restart of a stopped/error
        tenant is also a start in practice, so reserve capacity before calling
        the supervisor; otherwise this endpoint could bypass max_active_engines.
        """
        tenant = own_tenant(user)
        if tenant.get("status") not in {"starting", "running", "degraded"} \
                and not store.reserve_engine_slot(tenant["id"], settings.max_active_engines):
            raise HTTPException(status_code=409, detail="engine capacity is full; tenant queued")
        try:
            result = supervisor.restart(tenant["slug"])
        except Exception as exc:
            store.update_tenant(tenant["id"], status="error")
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        store.update_tenant(tenant["id"], status="running")
        store.audit("engine_restarted", tenant_id=tenant["id"], user_id=user["id"])
        return result

    @app.get("/api/v1/bot/config")
    def config_get(user: dict[str, Any] = Depends(current_user)):
        return supervisor.read_curated_config(own_tenant(user)["slug"])

    @app.patch("/api/v1/bot/config")
    def config_patch(body: ConfigPatchBody, user: dict[str, Any] = Depends(csrf_user)):
        tenant = own_tenant(user)
        changes = body.model_dump(exclude_none=True)
        try:
            config = supervisor.update_curated_config(tenant["slug"], changes)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        revision = store.record_config_revision(tenant["id"], user["id"], config)
        return {"ok": True, "revision": revision, "config": config, "restart_required": True}

    @app.post("/api/v1/trade-pin")
    def trade_pin(body: TradePinBody, user: dict[str, Any] = Depends(csrf_user)):
        if user.get("trade_pin_hash"):
            ok, reason = store.check_trade_pin(user["id"], body.current_pin or "")
            if not ok:
                raise HTTPException(status_code=403, detail=reason)
        store.set_trade_pin(user["id"], body.pin)
        return {"ok": True}

    @app.post("/api/v1/trade-pin/reset")
    def trade_pin_reset(
        body: TradePinResetBody,
        request: Request,
        user: dict[str, Any] = Depends(csrf_user),
    ):
        """Reset a forgotten Trade PIN only after a fresh step-up check."""
        client = request.client.host if request.client else "unknown"
        throttle_key = f"{client}|{user['id']}"
        require_not_throttled(trade_pin_reset_throttle, throttle_key)

        if user.get("password_hash"):
            if not body.current_password or not verify_password(
                str(user["password_hash"]), body.current_password
            ):
                trade_pin_reset_throttle.record_failure(throttle_key)
                raise HTTPException(status_code=403, detail="current password is incorrect")

        if not user.get("totp_enabled"):
            trade_pin_reset_throttle.record_failure(throttle_key)
            raise HTTPException(status_code=403, detail="Authenticator must be enabled before resetting Trade PIN")

        supplied = str(body.totp_code or "").strip()
        mfa_ok = verify_totp(str(user.get("totp_secret") or ""), supplied)
        if not mfa_ok and supplied:
            mfa_ok = store.use_recovery_code(user["id"], supplied)
        if not mfa_ok:
            trade_pin_reset_throttle.record_failure(throttle_key)
            raise HTTPException(status_code=403, detail="valid Authenticator or recovery code is required")

        store.set_trade_pin(user["id"], body.new_pin)
        store.audit("trade_pin_reset", user_id=user["id"])
        trade_pin_reset_throttle.clear(throttle_key)
        return {"ok": True}

    @app.post("/api/v1/exchange/connect")
    def exchange_connect(body: ExchangeConnectBody, user: dict[str, Any] = Depends(csrf_user)):
        if not body.withdraw_disabled_attested:
            raise HTTPException(status_code=422, detail="withdraw permission must be disabled")
        tenant = own_tenant(user)
        try:
            target = target_by_id(body.target_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if tenant["live_status"] in {"active", "approved"}:
            try:
                supervisor.stop(tenant["slug"])
            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail="could not stop live engine before credential rotation",
                ) from exc
        credentials = {
            "api_key": body.api_key,
            "api_secret": body.api_secret,
            "passphrase": body.passphrase,
        }
        if any("\n" in value or "\r" in value for value in credentials.values()):
            raise HTTPException(status_code=422, detail="credentials may not contain line breaks")
        profile = store.trading_profile(tenant["id"])
        if profile and profile.get("target_id") and profile["target_id"] != target["id"]:
            raise HTTPException(
                status_code=422,
                detail="save a trading profile for this exchange before connecting its keys",
            )
        envelope = cipher.encrypt(tenant["id"], target["id"], credentials)
        supervisor.configure_exchange(tenant["slug"], target["id"])
        supervisor.set_sim_mode(tenant["slug"])
        store.reset_live_approval(tenant["id"], user["id"], "exchange credentials changed")
        store.update_tenant(
            tenant["id"], exchange_id=target["exchange_id"], market_type=target["market_type"],
            status="stopped" if tenant["live_status"] in {"active", "approved"} else tenant["status"],
        )
        connection = store.set_exchange_connection(
            tenant["id"], target["exchange_id"], body.api_key[-4:],
            target_id=target["id"], credential_blob=envelope,
            key_version=cipher.active_version,
            capabilities={"withdraw_disabled_attested": True},
        )
        return {"ok": True, "connection": connection}

    @app.post("/api/v1/exchange/test")
    def exchange_test(user: dict[str, Any] = Depends(csrf_user)):
        tenant = own_tenant(user)
        connection = store.exchange_connection(tenant["id"])
        if not connection:
            raise HTTPException(status_code=409, detail="exchange credentials are not configured")
        try:
            encrypted = store.encrypted_credentials_with_version(tenant["id"])
            if not encrypted:
                raise ValueError("encrypted exchange credentials are missing")
            target_id, envelope, key_version = encrypted
            credentials = cipher.decrypt(
                tenant["id"], target_id, envelope, key_version=key_version
            )
            result = supervisor.probe_exchange(tenant["slug"], credentials)
        except Exception as exc:
            store.set_exchange_connection(
                tenant["id"], tenant["exchange_id"], connection["key_last4"],
                target_id=connection["target_id"], status="failed"
            )
            logger.exception("exchange probe failed for tenant %s", tenant["slug"])
            raise HTTPException(
                status_code=502,
                detail="exchange connection test failed; check credentials and venue",
            ) from exc
        capabilities = dict(result.get("capabilities") or {})
        # The venue's answer, kept apart from the user's claim. This used to set
        # withdraw_disabled_attested = True unconditionally and store it beside
        # probed capabilities, so a checkbox from onboarding was displayed as a
        # verified property of the key. None means "could not determine" and is
        # rendered as unverified — never as a pass.
        capabilities["withdraw_disabled_verified"] = result.get("withdraw_disabled_verified")
        capabilities["withdraw_permission_checked"] = bool(
            result.get("withdraw_permission_checked")
        )
        capabilities["withdraw_permission_detail"] = str(
            result.get("withdraw_permission_detail") or ""
        )
        withdraw_enabled = (
            capabilities["withdraw_permission_checked"] is True
            and capabilities["withdraw_disabled_verified"] is False
        )
        updated = store.set_exchange_connection(
            tenant["id"], tenant["exchange_id"], connection["key_last4"],
            target_id=connection["target_id"],
            status="failed" if withdraw_enabled else "tested",
            capabilities=capabilities,
        )
        if withdraw_enabled:
            raise HTTPException(
                status_code=409,
                detail="the exchange reports that withdrawal permission is enabled",
            )
        return {"ok": True, "connection": updated, "probe": result}

    def _telegram_error(exc: Exception, status: int | None, body: dict[str, Any]) -> str:
        """Fixed message map — never surface the raw exception.

        The bot token travels in the Telegram URL path, and requests embeds the
        URL in its exception strings, so str(exc) here would leak the whole
        token into the response body and into any log that captured it.
        """
        description = str(body.get("description") or "").lower()
        if status in {401, 404}:
            return "Telegram rejected this bot token."
        if status == 400 and "chat not found" in description:
            return "Telegram could not find that chat id. Send /start to your bot first."
        if status == 403:
            return "Your bot is blocked or is not a member of that chat."
        if isinstance(exc, requests.Timeout) or isinstance(exc, requests.ConnectionError):
            return "Could not reach Telegram. Try again."
        return "Telegram test failed."

    @app.post("/api/v1/telegram/connect")
    def telegram_connect(body: TelegramConnectBody,
                         user: dict[str, Any] = Depends(csrf_user)):
        tenant = own_tenant(user)
        # The regex cannot be trusted to reject a trailing newline ($ matches
        # before it), and an injected line would land in the 0600 env file.
        if any("\n" in value or "\r" in value for value in (body.bot_token, body.chat_id)):
            raise HTTPException(
                status_code=422, detail="telegram credentials may not contain line breaks"
            )
        envelope = cipher.encrypt(tenant["id"], "telegram", {"bot_token": body.bot_token})
        # getUpdates reports numeric chat ids, so the poller's exact-match
        # authorization can never succeed for an @channel id.
        commands_supported = body.chat_id.lstrip("-").isdigit()
        connection = store.set_telegram_connection(
            tenant["id"], body.chat_id, body.bot_token[-4:],
            status="stored", enabled=True, credential_blob=envelope,
            key_version=cipher.active_version,
        )
        preferences = supervisor.update_notification_config(tenant["slug"], {
            # alert_channel must be forced: "console" short-circuits delivery
            # entirely (xauby/engine/alerts.py:15).
            "alert_channel": "telegram",
            "alerts_enabled": True,
            "commands_enabled": commands_supported,
        })
        store.audit("telegram_connected", tenant_id=tenant["id"], user_id=str(user["id"]))
        return {"ok": True, "connection": connection, "preferences": preferences,
                "commands_supported": commands_supported, "restart_required": True}

    @app.post("/api/v1/telegram/test")
    def telegram_test(request: Request, user: dict[str, Any] = Depends(csrf_user)):
        tenant = own_tenant(user)
        connection = store.telegram_connection(tenant["id"])
        if not connection:
            raise HTTPException(status_code=409, detail="telegram is not connected")
        throttle_key = f"tg-test:{tenant['id']}:{request.client.host if request.client else '-'}"
        retry_after = telegram_test_throttle.retry_after(throttle_key)
        if retry_after:
            raise HTTPException(
                status_code=429, detail="too many telegram tests",
                headers={"Retry-After": str(int(retry_after))},
            )
        encrypted = store.encrypted_telegram_token_with_version(tenant["id"])
        if not encrypted:
            raise HTTPException(status_code=409, detail="telegram token is missing")
        envelope, key_version = encrypted
        token = cipher.decrypt(
            tenant["id"], "telegram", envelope, key_version=key_version
        )["bot_token"]
        status_code: int | None = None
        payload: dict[str, Any] = {}
        try:
            identity = requests.get(f"{TELEGRAM_API_ROOT}/bot{token}/getMe", timeout=10)
            status_code, payload = identity.status_code, (identity.json() or {})
            if not identity.ok or not payload.get("ok"):
                raise RuntimeError("getMe rejected")
            bot_username = str((payload.get("result") or {}).get("username") or "")
            sent = requests.post(
                f"{TELEGRAM_API_ROOT}/bot{token}/sendMessage",
                json={
                    "chat_id": connection["chat_id"],
                    "text": "xAuby: Telegram alerts are connected. "
                            "Critical safety alerts are always delivered here.",
                },
                timeout=10,
            )
            status_code, payload = sent.status_code, (sent.json() or {})
            if not sent.ok or not payload.get("ok"):
                raise RuntimeError("sendMessage rejected")
        except Exception as exc:
            telegram_test_throttle.record_failure(throttle_key)
            store.set_telegram_connection(
                tenant["id"], connection["chat_id"], connection["token_last4"],
                status="failed", enabled=bool(connection["enabled"]),
                bot_username=str(connection.get("bot_username") or ""),
            )
            raise HTTPException(
                status_code=502, detail=_telegram_error(exc, status_code, payload)
            ) from None
        telegram_test_throttle.clear(throttle_key)
        updated = store.set_telegram_connection(
            tenant["id"], connection["chat_id"], connection["token_last4"],
            status="tested", enabled=True, bot_username=bot_username,
        )
        store.audit("telegram_tested", tenant_id=tenant["id"], user_id=str(user["id"]))
        return {"ok": True, "connection": updated}

    @app.delete("/api/v1/telegram")
    def telegram_disconnect(user: dict[str, Any] = Depends(csrf_user)):
        tenant = own_tenant(user)
        store.delete_telegram_connection(tenant["id"])
        preferences = supervisor.update_notification_config(tenant["slug"], {
            "alerts_enabled": False, "commands_enabled": False,
        })
        store.audit("telegram_disconnected", tenant_id=tenant["id"], user_id=str(user["id"]))
        return {"ok": True, "preferences": preferences, "restart_required": True}

    @app.get("/api/v1/telegram/preferences")
    def telegram_preferences(user: dict[str, Any] = Depends(current_user)):
        return supervisor.read_notification_config(own_tenant(user)["slug"])

    @app.patch("/api/v1/telegram/preferences")
    def telegram_preferences_patch(body: TelegramPreferencesBody,
                                   user: dict[str, Any] = Depends(csrf_user)):
        tenant = own_tenant(user)
        changes = body.model_dump(exclude_none=True)
        if not changes:
            raise HTTPException(status_code=422, detail="no supported settings supplied")
        try:
            preferences = supervisor.update_notification_config(tenant["slug"], changes)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        store.audit("telegram_preferences_updated", tenant_id=tenant["id"],
                    user_id=str(user["id"]), payload=changes)
        return {"ok": True, "preferences": preferences, "restart_required": True}

    @app.post("/api/v1/live/request")
    def live_request(user: dict[str, Any] = Depends(csrf_user)):
        tenant = own_tenant(user)
        if (not user.get("totp_enabled") or not user.get("mfa_verified")) and not settings.dev_login_enabled:
            raise HTTPException(status_code=403, detail="verified TOTP is required for Live")
        connection = store.exchange_connection(tenant["id"])
        if not connection or connection["status"] != "tested":
            raise HTTPException(status_code=409, detail="exchange connection must pass testing first")
        if not withdrawal_permission_verified(connection):
            raise HTTPException(status_code=409, detail=WITHDRAWAL_LIVE_GATE_DETAIL)
        profile = store.trading_profile(tenant["id"])
        if not profile:
            raise HTTPException(status_code=409, detail="certified trading profile is required")
        if not any(item.get("live_certified") for item in profile.get("presets", [])):
            raise HTTPException(
                status_code=409, detail="no selected preset is live certified"
            )
        return store.request_live(tenant["id"], user["id"], risk=profile.get("risk"))

    @app.post("/api/v1/live/activate")
    def live_activate(body: LiveActivateBody, user: dict[str, Any] = Depends(csrf_user)):
        if not settings.live_activation_enabled:
            raise HTTPException(status_code=503, detail="live activation is currently disabled")
        if not body.risk_acknowledged:
            raise HTTPException(status_code=422, detail="risk acknowledgement is required")
        tenant = own_tenant(user)
        if (not user.get("totp_enabled") or not user.get("mfa_verified")) and not settings.dev_login_enabled:
            raise HTTPException(status_code=403, detail="verified TOTP is required for Live")
        ok, reason = store.check_trade_pin(user["id"], body.trade_pin)
        if not ok:
            raise HTTPException(status_code=403, detail=reason)
        connection = store.exchange_connection(tenant["id"])
        tested_at = _finite_float((connection or {}).get("tested_at"))
        test_age = None if tested_at is None else time.time() - tested_at
        if (
            not connection
            or connection["status"] != "tested"
            or tested_at is None
            or tested_at <= 0
            or test_age is None
            or test_age < -5
            or test_age > 1800
        ):
            raise HTTPException(status_code=409, detail="test the exchange connection again")
        if not withdrawal_permission_verified(connection):
            raise HTTPException(status_code=409, detail=WITHDRAWAL_LIVE_GATE_DETAIL)
        profile = store.trading_profile(tenant["id"])
        if not profile or profile.get("target_id") != connection.get("target_id"):
            raise HTTPException(status_code=409, detail="profile and exchange target must match")
        certified = [
            item for item in profile.get("presets", []) if item.get("live_certified")
        ]
        if not certified:
            raise HTTPException(status_code=409, detail="no selected preset is live certified")
        for preset in certified:
            # Only a catalog-certified CDC Pure preset may go live without a
            # stop loss; every other certified preset must keep one.
            if not preset.get("stop_loss_required", True) and not preset.get("cdc_pure_certified"):
                raise HTTPException(
                    status_code=409,
                    detail="a certified stop-loss or CDC Pure profile is required",
                )
        if not store.reserve_engine_slot(tenant["id"], settings.max_active_engines):
            raise HTTPException(status_code=409, detail="engine capacity is full; tenant queued")
        try:
            if tenant["live_status"] != "requested":
                store.request_live(tenant["id"], user["id"], risk=profile.get("risk"))
            store.approve_live(tenant["id"], user["id"])
            supervisor.set_live_mode(tenant["slug"], profile["target_id"])
            supervisor.restart(tenant["slug"])
            store.update_tenant(tenant["id"], live_status="active", status="running")
        except Exception as exc:
            try:
                supervisor.set_sim_mode(tenant["slug"])
                supervisor.clear_credentials(tenant["slug"])
            finally:
                store.fail_live_activation(tenant["id"], user["id"], str(exc))
                store.update_tenant(tenant["id"], status="error")
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        store.audit("live_activated", tenant_id=tenant["id"], user_id=user["id"])
        return {"ok": True, "live_status": "active"}

    @app.post("/api/v1/live/deactivate")
    def live_deactivate(user: dict[str, Any] = Depends(csrf_user)):
        tenant = own_tenant(user)
        try:
            supervisor.stop(tenant["slug"])
            supervisor.set_sim_mode(tenant["slug"])
        finally:
            store.update_tenant(tenant["id"], live_status="not_requested", status="stopped")
        store.audit("live_deactivated", tenant_id=tenant["id"], user_id=user["id"])
        return {"ok": True, "live_status": "not_requested"}

    @app.post("/api/v1/orders/preview")
    def order_preview(body: OrderPreviewBody, user: dict[str, Any] = Depends(csrf_user)):
        tenant = own_tenant(user)
        try:
            context = manual_order_context(tenant, body.symbol, body.intent)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        position = context["position"]
        mark = _finite_float(context["mark_price"])
        if mark is None or mark <= 0:
            raise HTTPException(status_code=409, detail="a valid market price is required")
        if body.intent == "CLOSE_POSITION":
            quantity = _finite_float(position.get("quantity"))
            if quantity is None or quantity <= 0:
                raise HTTPException(status_code=409, detail="tracked position quantity is unavailable")
            side = str(position.get("position_side") or "LONG").upper()
            estimated_notional = quantity * mark
            sizing_mode = "close_position"
            allocation_pct = None
        else:
            config = supervisor.read_curated_config(tenant["slug"])
            focus = context["focus"]
            breakdown = focus.get("equity_breakdown") if isinstance(focus.get("equity_breakdown"), dict) else {}
            raw_equity = focus.get("total_equity_usdt")
            if raw_equity is None or raw_equity == "":
                raw_equity = breakdown.get("portfolio_total_usdt")
            equity = _finite_float(raw_equity)
            if equity is None or equity <= 0:
                raise HTTPException(status_code=409, detail="portfolio equity is unavailable")
            preset = context["preset"]
            # A certified pair's own cap is authoritative.  The profile-level
            # value is only a fallback for legacy presets; otherwise the
            # smallest cap among selected pairs would incorrectly flatten all
            # manual previews to the same size.
            preset_position_cap = preset.get("max_position_per_trade_pct")
            if preset_position_cap is None:
                preset_position_cap = config.get("max_position_per_trade_pct") or 10.0
            max_position_pct = _finite_float(preset_position_cap)
            allocation_pct_limit = _finite_float(preset.get("allocation_pct", 100.0))
            if (
                max_position_pct is None or allocation_pct_limit is None
                or max_position_pct <= 0 or allocation_pct_limit <= 0
            ):
                raise HTTPException(status_code=409, detail="pair sizing limits are unavailable")
            max_allocation = min(max_position_pct, allocation_pct_limit) / 100.0
            execution_profile = preset.get("execution_profile") or {}
            cdc_pure = bool(preset.get("cdc_pure_certified"))
            if cdc_pure:
                # CDC Pure has no exchange stop loss.  Its certified sizing is
                # a fixed fraction of equity, so the manual preview must use
                # the same position_pct as the engine instead of inventing an
                # SL distance and applying the generic risk formula.
                effective_pct = bounded_position_fraction(
                    execution_profile.get("position_pct", 0.95) or 0.95,
                    preset.get("allocation_pct"),
                )
                estimated_notional = equity * effective_pct
                allocation_pct = effective_pct * 100.0
                sizing_mode = "cdc_pure"
            else:
                risk_fraction = _finite_float(
                    preset.get("risk_pct", config.get("risk_pct") or 0.01)
                )
                if risk_fraction is None or risk_fraction <= 0:
                    raise HTTPException(status_code=409, detail="pair risk settings are unavailable")
                # Size off the pair's live ATR at the preset's own sl_atr_mult —
                # the same distance the strategy itself would stop at — instead
                # of a synthetic fixed percent, so a manual preview isn't
                # larger than the engine would size the identical signal
                # (see docs/audit_system_2026-07-21.md F-3).
                atr = resolve_pair_atr(
                    focus.get("indicators") if isinstance(focus.get("indicators"), dict) else None
                )
                sl_atr_mult = float(execution_profile.get("sl_atr_mult") or 0.0)
                stop_distance, sizing_basis = risk_based_stop_distance(
                    mark_price=mark, atr=atr, sl_atr_mult=sl_atr_mult,
                )
                risk_sized = (equity * risk_fraction / stop_distance) * mark
                estimated_notional = min(risk_sized, equity * max_allocation)
                allocation_pct = (estimated_notional / equity) * 100.0
                sizing_mode = "risk_based"
            quantity = estimated_notional / mark
            if (
                not math.isfinite(estimated_notional) or estimated_notional <= 0
                or not math.isfinite(quantity) or quantity <= 0
            ):
                raise HTTPException(status_code=409, detail="pair sizing data is unavailable")
            side = "SHORT" if body.intent == "OPEN_SHORT" else "LONG"
        payload = {
            "version": 1,
            "symbol": context["symbol"],
            "intent": body.intent,
            "side": side,
            "management_mode": (
                "strategy_handoff"
                if body.intent in {"OPEN_LONG", "OPEN_SHORT"}
                else "strategy"
            ),
            "mode": "live" if context["live"] else "simulation",
            "mark_price": mark,
            "estimated_quantity": quantity,
            "estimated_notional": estimated_notional,
            "sizing_mode": sizing_mode,
            "sizing_basis": sizing_basis if sizing_mode == "risk_based" else None,
            "allocation_pct": allocation_pct,
            "preset_id": context["preset"]["id"],
            "target_id": context["target"]["id"],
        }
        challenge = store.create_challenge(tenant["id"], user["id"], payload, ttl_seconds=60)
        store.audit(
            "order_previewed", tenant_id=tenant["id"], user_id=user["id"],
            payload={"challenge_id": challenge["id"], "intent": body.intent, "symbol": context["symbol"]},
        )
        return {
            "challenge_id": challenge["id"],
            "expires_at": challenge["expires_at"],
            "digest": challenge["digest"],
            "preview": payload,
        }

    @app.post("/api/v1/orders/{challenge_id}/confirm")
    def order_confirm(challenge_id: str, body: OrderConfirmBody,
                      user: dict[str, Any] = Depends(csrf_user)):
        tenant = own_tenant(user)
        if not settings.manual_trading_enabled:
            raise HTTPException(status_code=404, detail="manual trading is disabled")
        ok, reason = store.check_trade_pin(user["id"], body.trade_pin)
        if not ok:
            raise HTTPException(status_code=403, detail=reason)
        command_id = hashlib.sha256(
            f"{tenant['id']}:{challenge_id}:{body.idempotency_key}".encode()
        ).hexdigest()[:32]
        draft = store.challenge(challenge_id, tenant["id"], user["id"])
        if draft is None:
            raise HTTPException(status_code=404, detail="order challenge not found")
        if draft["status"] == "queued":
            if str(draft.get("command_id") or "") != command_id:
                raise HTTPException(status_code=409, detail="challenge used with another idempotency key")
            return {"ok": True, "status": "queued", "command_id": command_id}
        payload = json.loads(draft["payload_json"])
        # Re-run all fail-closed state and certification checks at confirmation time.
        manual_order_context(tenant, payload["symbol"], payload["intent"])
        try:
            challenge = store.confirm_challenge(challenge_id, tenant["id"], user["id"], command_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if challenge["status"] != "queued":
            try:
                supervisor.queue_manual_order(
                    tenant["slug"], symbol=payload["symbol"],
                    intent=payload["intent"], request_id=command_id,
                    management_mode=str(payload.get("management_mode") or "strategy_handoff"),
                )
            except (OSError, RuntimeError, ValueError) as exc:
                raise HTTPException(status_code=503, detail="could not queue manual order") from exc
            challenge = store.mark_challenge_queued(challenge_id, command_id)
            store.audit(
                "order_queued", tenant_id=tenant["id"], user_id=user["id"],
                payload={"challenge_id": challenge_id, "command_id": command_id,
                         "intent": payload["intent"], "symbol": payload["symbol"]},
            )
        return {"ok": True, "status": challenge["status"], "command_id": command_id}

    @app.get("/api/v1/orders")
    def orders(user: dict[str, Any] = Depends(current_user)):
        tenant = own_tenant(user)
        if not settings.manual_trading_enabled:
            raise HTTPException(status_code=404, detail="manual trading is disabled")
        items = []
        for row in store.list_challenges(tenant["id"]):
            payload = json.loads(row["payload_json"])
            items.append({
                "id": row["id"], "status": row["status"], "created_at": row["created_at"],
                "expires_at": row["expires_at"], "command_id": row.get("command_id"),
                "symbol": payload.get("symbol"), "intent": payload.get("intent"),
                "side": payload.get("side"), "mode": payload.get("mode"),
                "management_mode": payload.get("management_mode"),
                "estimated_quantity": payload.get("estimated_quantity"),
                "estimated_notional": payload.get("estimated_notional"),
            })
        return {"items": items}

    @app.get("/api/v1/admin/users")
    def admin_users(user: dict[str, Any] = Depends(admin_read_user)):
        return {"items": store.list_users(), "capacity": settings.max_users}

    @app.post("/api/v1/admin/invites", status_code=201)
    def admin_invite(body: InviteBody, user: dict[str, Any] = Depends(admin_user)):
        if store.user_count() >= settings.max_users:
            raise HTTPException(status_code=409, detail="pilot capacity is full")
        try:
            invite, token = store.create_invite(body.email, user["id"])
            url = f"{settings.public_base_url}/invite/{quote(token)}"
            delivery = "sent"
            delivery_detail = ""
            try:
                mailer.send(
                    invite["email"], "Your xAuby pilot invitation",
                    f"You have been invited to xAuby. This link is valid for 7 days:\n{url}",
                )
            except RuntimeError as exc:
                delivery = "manual"
                delivery_detail = str(exc)
                store.audit(
                    "invite_delivery_failed",
                    user_id=user["id"],
                    payload={"email": invite["email"], "reason": delivery_detail},
                )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "ok": True,
            "email": invite["email"],
            "expires_at": invite["expires_at"],
            "invite_url": url,
            "delivery": delivery,
            "delivery_detail": delivery_detail,
        }

    @app.post("/api/v1/admin/users/{user_id}/status")
    def admin_account_status(user_id: str, body: AccountStatusBody,
                             user: dict[str, Any] = Depends(admin_user)):
        target = store.user_by_id(user_id)
        if not target:
            raise HTTPException(status_code=404, detail="user not found")
        if target.get("role") == "platform_admin" and body.status != "active":
            raise HTTPException(status_code=409, detail="owner account cannot be disabled here")
        updated = store.set_account_status(user_id, body.status, user["id"])
        tenant = store.tenant_for_user(user_id)
        if body.status == "active":
            tenant = ensure_user_workspace(updated)
        elif body.status != "active" and tenant is not None:
            try:
                supervisor.stop(tenant["slug"])
            finally:
                store.update_tenant(tenant["id"], status="stopped", live_status="not_requested")
                supervisor.set_sim_mode(tenant["slug"])
        return {"ok": True, "user": updated, "tenant": store.tenant_for_user(user_id)}

    @app.delete("/api/v1/admin/users/{user_id}")
    def admin_remove_pilot(user_id: str, body: RemovePilotBody,
                           user: dict[str, Any] = Depends(admin_user)):
        target = store.user_by_id(user_id)
        if not target:
            raise HTTPException(status_code=404, detail="user not found")
        if target.get("role") == "platform_admin":
            raise HTTPException(status_code=409, detail="owner account cannot be removed")
        if target.get("account_status") != "suspended":
            raise HTTPException(
                status_code=409, detail="suspend the pilot before removing the account"
            )
        if str(body.confirm_email).strip().lower() != str(target["email"]).strip().lower():
            raise HTTPException(status_code=422, detail="confirmation email does not match")

        tenant = store.tenant_for_user(user_id)
        if tenant is not None:
            try:
                supervisor.stop(tenant["slug"])
            except (OSError, RuntimeError, ValueError) as exc:
                raise HTTPException(
                    status_code=503,
                    detail="could not verify that the tenant engine is stopped",
                ) from exc
            store.update_tenant(
                tenant["id"], status="stopped", live_status="not_requested"
            )

        try:
            removed = store.remove_pilot(user_id, str(user["id"]))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="user not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        # Avatar bytes are not part of the relational tombstone and have no
        # audit value. Remove every supported extension so stale files cannot
        # survive a prior interrupted appearance update.
        for ext in ("png", "jpg", "webp"):
            try:
                (avatar_root / f"{user_id}.{ext}").unlink(missing_ok=True)
            except OSError:
                logger.exception("could not remove avatar for deleted pilot %s", user_id)
        return {
            "ok": True,
            "email": removed["email"],
            "email_available": True,
            "workspace": "archived",
        }

    @app.get("/api/v1/admin/tenants")
    def admin_tenants(user: dict[str, Any] = Depends(admin_read_user)):
        return {"items": store.list_tenants(), "capacity": settings.max_active_engines,
                "active": store.active_count()}

    @app.post("/api/v1/admin/tenants/{tenant_id}/approve-live")
    def admin_approve_live(tenant_id: str, user: dict[str, Any] = Depends(admin_user)):
        if not settings.live_activation_enabled:
            raise HTTPException(
                status_code=503,
                detail="Live activation is locked until legacy position handoff is reconciled",
            )
        tenant = store.tenant_by_id(tenant_id)
        if not tenant:
            raise HTTPException(status_code=404, detail="tenant not found")
        if (tenant["exchange_id"], tenant["market_type"]) not in supervisor.LIVE_CERTIFIED:
            raise HTTPException(status_code=409, detail="exchange/market is not live certified")
        connection = store.exchange_connection(tenant_id)
        if not connection or connection["status"] != "tested":
            raise HTTPException(status_code=409, detail="exchange connection is not tested")
        if not withdrawal_permission_verified(connection):
            raise HTTPException(status_code=409, detail=WITHDRAWAL_LIVE_GATE_DETAIL)
        profile = store.trading_profile(tenant_id)
        if not profile or not profile.get("target_id"):
            raise HTTPException(status_code=409, detail="tenant has no saved trading profile")
        was_active = tenant["status"] in {"starting", "running", "degraded"}
        if not store.reserve_engine_slot(tenant_id, settings.max_active_engines):
            raise HTTPException(status_code=409, detail="engine capacity is full; tenant queued")
        try:
            store.approve_live(tenant_id, user["id"])
            supervisor.set_live_mode(tenant["slug"], profile["target_id"])
            supervisor.restart(tenant["slug"])
            store.update_tenant(tenant_id, live_status="active", status="running")
        except Exception as exc:
            try:
                supervisor.stop(tenant["slug"])
            except Exception:
                pass
            try:
                supervisor.set_sim_mode(tenant["slug"])
            except Exception:
                pass
            store.fail_live_activation(tenant_id, user["id"], str(exc))
            if not was_active:
                store.update_tenant(tenant_id, status="error")
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}

    return app
