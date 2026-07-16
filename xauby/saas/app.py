from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from xauby.saas.catalog import public_catalog, target_by_id, validate_profile
from xauby.saas.credentials import CredentialCipher
from xauby.saas.mailer import Mailer
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
from xauby.saas.supervisor import TenantSupervisor
from xauby.utils.atomic_io import atomic_bytes_write

SESSION_COOKIE = "xauby_saas_session"
OAUTH_STATE_COOKIE = "xauby_saas_oauth_state"
TRADE_PIN_PATTERN = r"^[0-9]{8,12}$"
# Legacy consoles accepted a broader secret for the old/current value. Accept
# it only during rotation; every newly saved PIN and sensitive action keeps the
# stronger 8-12 digit requirement.


class ExchangeConnectBody(BaseModel):
    target_id: str = Field(min_length=3, max_length=64)
    api_key: str = Field(min_length=4, max_length=512)
    api_secret: str = Field(min_length=4, max_length=512)
    passphrase: str = Field(default="", max_length=512)
    withdraw_disabled_attested: bool


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


class AccountStatusBody(BaseModel):
    status: str = Field(pattern="^(active|rejected|suspended)$")


def _safe_slug_from_email(email: str, fallback: str) -> str:
    local = str(email).split("@", 1)[0].lower()
    # Keep room for the ``xauby-`` prefix used by the tenant-specific Linux
    # service account (Linux usernames are limited to 32 characters).
    slug = re.sub(r"[^a-z0-9]+", "-", local).strip("-")[:25]
    return slug if slug else f"user-{fallback[:8]}"


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
    cipher = CredentialCipher(
        settings.credential_master_key,
        fallback_secret=settings.session_secret if settings.dev_login_enabled else "",
    )

    def load_credentials(slug: str) -> tuple[str, dict[str, str]] | None:
        tenant = store.tenant_by_slug(slug)
        if not tenant:
            return None
        encrypted = store.encrypted_credentials(tenant["id"])
        if not encrypted:
            return None
        target_id, envelope = encrypted
        target = target_by_id(target_id)
        return target["exchange_id"], cipher.decrypt(tenant["id"], target_id, envelope)

    supervisor.set_credential_loader(load_credentials)
    mailer = mailer or Mailer(settings)
    runtime = runtime or RuntimeGateway(settings, supervisor)
    login_throttle = AttemptThrottle(max_attempts=8, window_seconds=300, lockout_seconds=900)
    email_throttle = AttemptThrottle(max_attempts=5, window_seconds=900, lockout_seconds=900)
    trade_pin_reset_throttle = AttemptThrottle(max_attempts=5, window_seconds=300, lockout_seconds=900)
    app = FastAPI(title="xAuby SaaS Control Plane", version="0.1.0")
    app.state.settings = settings
    app.state.store = store
    app.state.supervisor = supervisor
    app.state.mailer = mailer
    app.state.runtime = runtime
    avatar_root = settings.data_root / "avatars"
    avatar_root.mkdir(parents=True, exist_ok=True)

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

    def admin_user(user: dict[str, Any] = Depends(csrf_user)) -> dict[str, Any]:
        if user.get("role") != "platform_admin":
            raise HTTPException(status_code=403, detail="platform admin required")
        if (not user.get("totp_enabled") or not user.get("mfa_verified")) and not settings.dev_login_enabled:
            raise HTTPException(status_code=403, detail="verified TOTP is required for admin actions")
        return user

    def own_tenant(user: dict[str, Any]) -> dict[str, Any]:
        if user.get("account_status") != "active":
            raise HTTPException(status_code=403, detail=f"account is {user.get('account_status')}")
        tenant = store.tenant_for_user(str(user["id"]))
        if tenant is None:
            raise HTTPException(status_code=409, detail="tenant is not provisioned")
        return tenant

    def manual_order_context(tenant: dict[str, Any], symbol: str, intent: str) -> dict[str, Any]:
        if not settings.manual_trading_enabled:
            raise HTTPException(status_code=404, detail="manual trading is disabled")
        if tenant.get("status") not in {"running", "degraded"} or supervisor.status(tenant["slug"]) != "active":
            raise HTTPException(status_code=409, detail="tenant engine must be running")
        snapshot = runtime.snapshot(tenant["slug"])
        if not snapshot.get("ok") or snapshot.get("read_only") or snapshot.get("stale"):
            raise HTTPException(status_code=409, detail="fresh tenant-engine data is required")
        snapshot_age = snapshot.get("age_sec")
        if snapshot_age is None or float(snapshot_age) > 30:
            raise HTTPException(status_code=409, detail="market snapshot is too old")
        state = snapshot.get("state") if isinstance(snapshot.get("state"), dict) else {}
        clean_symbol = RuntimeGateway._symbol(symbol)
        focus_symbol = str(state.get("focus_symbol") or state.get("symbol") or "").upper()
        if clean_symbol != focus_symbol:
            raise HTTPException(status_code=409, detail="symbol is not the active trading pair")
        focus = RuntimeGateway._focus_state(state, clean_symbol)
        position = focus.get("position") if isinstance(focus.get("position"), dict) else {}
        is_open = str(position.get("state") or "idle") == "bought"
        if intent == "CLOSE_POSITION" and not is_open:
            raise HTTPException(status_code=409, detail="there is no tracked position to close")
        if intent != "CLOSE_POSITION" and is_open:
            raise HTTPException(status_code=409, detail="close the current position before opening another")
        profile = store.trading_profile(tenant["id"])
        if not profile:
            raise HTTPException(status_code=409, detail="a trading profile is required")
        preset = next(
            (item for item in profile.get("presets", []) if item.get("id") == profile.get("active_preset_id")),
            None,
        )
        if not preset or str(preset.get("symbol") or "").upper() != clean_symbol:
            raise HTTPException(status_code=409, detail="active profile does not match the symbol")
        target = target_by_id(str(profile.get("target_id") or preset.get("target_id") or ""))
        requested_side = "short" if intent == "OPEN_SHORT" else "long"
        if intent != "CLOSE_POSITION" and requested_side not in target.get("manual_allowed_sides", []):
            raise HTTPException(status_code=409, detail=f"{requested_side.upper()} is unavailable for this market")
        live = tenant.get("live_status") == "active"
        certification = f"manual_{requested_side}_live_certified"
        if live and intent != "CLOSE_POSITION" and not target.get(certification):
            raise HTTPException(status_code=409, detail=f"Live {requested_side.upper()} is not certified")
        mark = float(focus.get("current_price") or position.get("mark_price") or 0.0)
        if mark <= 0:
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
            tenant, created = store.ensure_tenant(
                user["id"], _safe_slug_from_email(user["email"], user["id"])
            )
            if created:
                supervisor.provision(tenant["slug"])
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
            tenant, created = store.ensure_tenant(
                user["id"], _safe_slug_from_email(user["email"], user["id"])
            )
            if created:
                supervisor.provision(tenant["slug"])
        else:
            user, _ = store.upsert_google_user(email, str(claims.get("sub") or ""))
        if user.get("account_status") in {"rejected", "suspended"}:
            # Password login enforces this; the OAuth path must not become a
            # side door for disabled accounts.
            raise HTTPException(status_code=403, detail=f"account is {user['account_status']}")
        tenant = store.tenant_for_user(str(user["id"]))
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
        tenant, created = store.ensure_tenant(
            user["id"], _safe_slug_from_email(user["email"], user["id"])
        )
        if created:
            supervisor.provision(tenant["slug"])
        store.set_account_status(user["id"], "active", user["id"])
        token, csrf = store.create_session(user["id"], mfa_verified=True)
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax")
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

            if live_was_enabled:
                supervisor.stop(tenant["slug"])
                store.update_tenant(tenant["id"], status="stopped")
                store.reset_live_approval(tenant["id"], user["id"], "trading profile changed")
            compiled = supervisor.apply_profile(
                tenant["slug"], body.preset_ids, body.active_preset_id, body.risk
            )
            store.save_trading_profile(tenant["id"], user["id"], profile)
            active = profile["presets"][0]
            store.update_tenant(
                tenant["id"], exchange_id=active["exchange_id"],
                market_type=active["market_type"],
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "ok": True,
            "profile": compiled,
            "mode": "simulation",
            "live_reapproval_required": live_was_enabled,
            "profile_changed": True,
        }

    @app.get("/api/v1/bot")
    def bot(user: dict[str, Any] = Depends(current_user)):
        tenant = own_tenant(user)
        return {
            "tenant": tenant,
            "service_status": supervisor.status(tenant["slug"]),
            "state": supervisor.read_state(tenant["slug"]),
            "exchange_connection": store.exchange_connection(tenant["id"]),
        }

    @app.get("/api/v1/runtime/snapshot")
    def runtime_snapshot(user: dict[str, Any] = Depends(current_user)):
        tenant = own_tenant(user)
        return runtime.snapshot(tenant["slug"])

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
        envelope = cipher.encrypt(tenant["id"], target["id"], credentials)
        supervisor.configure_exchange(tenant["slug"], target["exchange_id"])
        supervisor.set_sim_mode(tenant["slug"])
        store.reset_live_approval(tenant["id"], user["id"], "exchange credentials changed")
        store.update_tenant(
            tenant["id"], exchange_id=target["exchange_id"], market_type=target["market_type"],
            status="stopped" if tenant["live_status"] in {"active", "approved"} else tenant["status"],
        )
        connection = store.set_exchange_connection(
            tenant["id"], target["exchange_id"], body.api_key[-4:],
            target_id=target["id"], credential_blob=envelope,
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
            encrypted = store.encrypted_credentials(tenant["id"])
            if not encrypted:
                raise ValueError("encrypted exchange credentials are missing")
            target_id, envelope = encrypted
            credentials = cipher.decrypt(tenant["id"], target_id, envelope)
            result = supervisor.probe_exchange(tenant["slug"], credentials)
        except Exception as exc:
            store.set_exchange_connection(
                tenant["id"], tenant["exchange_id"], connection["key_last4"],
                target_id=connection["target_id"], status="failed"
            )
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        capabilities = dict(result.get("capabilities") or {})
        capabilities["withdraw_disabled_attested"] = True
        updated = store.set_exchange_connection(
            tenant["id"], tenant["exchange_id"], connection["key_last4"],
            target_id=connection["target_id"], status="tested", capabilities=capabilities,
        )
        return {"ok": True, "connection": updated, "probe": result}

    @app.post("/api/v1/live/request")
    def live_request(user: dict[str, Any] = Depends(csrf_user)):
        tenant = own_tenant(user)
        if (not user.get("totp_enabled") or not user.get("mfa_verified")) and not settings.dev_login_enabled:
            raise HTTPException(status_code=403, detail="verified TOTP is required for Live")
        connection = store.exchange_connection(tenant["id"])
        if not connection or connection["status"] != "tested":
            raise HTTPException(status_code=409, detail="exchange connection must pass testing first")
        profile = store.trading_profile(tenant["id"])
        if not profile:
            raise HTTPException(status_code=409, detail="certified trading profile is required")
        active = next(
            (item for item in profile.get("presets", [])
             if item.get("id") == profile.get("active_preset_id")), None
        )
        if not active or not active.get("live_certified"):
            raise HTTPException(status_code=409, detail="active preset is not live certified")
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
        if (
            not connection
            or connection["status"] != "tested"
            or not connection.get("tested_at")
            or time.time() - float(connection["tested_at"]) > 1800
        ):
            raise HTTPException(status_code=409, detail="test the exchange connection again")
        profile = store.trading_profile(tenant["id"])
        if not profile or profile.get("target_id") != connection.get("target_id"):
            raise HTTPException(status_code=409, detail="profile and exchange target must match")
        active = profile["presets"][0]
        cdc_pure = bool(
            active.get("cdc_pure_certified")
            and active.get("symbol") == "XAUUSDT"
            and active.get("strategy") == "xauby_actionzone"
        )
        if not active.get("live_certified") or (
            not profile["risk"].get("stop_loss_required") and not cdc_pure
        ):
            raise HTTPException(status_code=409, detail="a certified stop-loss or CDC Pure profile is required")
        if not store.reserve_engine_slot(tenant["id"], settings.max_active_engines):
            raise HTTPException(status_code=409, detail="engine capacity is full; tenant queued")
        try:
            if tenant["live_status"] != "requested":
                store.request_live(tenant["id"], user["id"], risk=profile.get("risk"))
            store.approve_live(tenant["id"], user["id"])
            supervisor.set_live_mode(
                tenant["slug"], tenant["exchange_id"], tenant["market_type"]
            )
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
        mark = float(context["mark_price"])
        if body.intent == "CLOSE_POSITION":
            quantity = float(position.get("quantity") or 0.0)
            side = str(position.get("position_side") or "LONG").upper()
            estimated_notional = quantity * mark
            sizing_mode = "close_position"
            allocation_pct = None
        else:
            config = supervisor.read_curated_config(tenant["slug"])
            focus = context["focus"]
            breakdown = focus.get("equity_breakdown") if isinstance(focus.get("equity_breakdown"), dict) else {}
            equity = float(focus.get("total_equity_usdt") or breakdown.get("portfolio_total_usdt") or 0.0)
            if equity <= 0:
                raise HTTPException(status_code=409, detail="portfolio equity is unavailable")
            max_allocation = float(config.get("max_position_per_trade_pct") or 10.0) / 100.0
            preset = context["preset"]
            execution_profile = preset.get("execution_profile") or {}
            cdc_pure = bool(
                preset.get("cdc_pure_certified")
                and str(preset.get("symbol") or "").upper() == "XAUUSDT"
                and preset.get("strategy") == "xauby_actionzone"
            )
            if cdc_pure:
                # CDC Pure has no exchange stop loss.  Its certified sizing is
                # a fixed fraction of equity, so the manual preview must use
                # the same position_pct as the engine instead of inventing an
                # SL distance and applying the generic risk formula.
                position_pct = max(
                    0.0,
                    min(float(execution_profile.get("position_pct", 0.95) or 0.95), 1.0),
                )
                effective_pct = min(position_pct, max(0.0, max_allocation))
                estimated_notional = equity * effective_pct
                allocation_pct = effective_pct * 100.0
                sizing_mode = "cdc_pure"
            else:
                risk_fraction = float(config.get("risk_pct") or 0.01)
                stop_distance = mark * 0.02
                risk_sized = (equity * risk_fraction / stop_distance) * mark
                estimated_notional = min(risk_sized, equity * max_allocation)
                allocation_pct = (estimated_notional / equity) * 100.0
                sizing_mode = "risk_based"
            quantity = estimated_notional / mark
            side = "SHORT" if body.intent == "OPEN_SHORT" else "LONG"
        payload = {
            "version": 1,
            "symbol": context["symbol"],
            "intent": body.intent,
            "side": side,
            "mode": "live" if context["live"] else "simulation",
            "mark_price": mark,
            "estimated_quantity": quantity,
            "estimated_notional": estimated_notional,
            "sizing_mode": sizing_mode,
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
                "estimated_quantity": payload.get("estimated_quantity"),
                "estimated_notional": payload.get("estimated_notional"),
            })
        return {"items": items}

    @app.get("/api/v1/admin/users")
    def admin_users(user: dict[str, Any] = Depends(current_user)):
        if user.get("role") != "platform_admin":
            raise HTTPException(status_code=403, detail="platform admin required")
        if (not user.get("totp_enabled") or not user.get("mfa_verified")) and not settings.dev_login_enabled:
            raise HTTPException(status_code=403, detail="verified TOTP is required for admin actions")
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
        if body.status == "active" and tenant is None:
            tenant, _ = store.ensure_tenant(
                user_id, _safe_slug_from_email(updated["email"], user_id)
            )
            supervisor.provision(tenant["slug"])
        elif body.status != "active" and tenant is not None:
            try:
                supervisor.stop(tenant["slug"])
            finally:
                store.update_tenant(tenant["id"], status="stopped", live_status="not_requested")
                supervisor.set_sim_mode(tenant["slug"])
        return {"ok": True, "user": updated, "tenant": store.tenant_for_user(user_id)}

    @app.get("/api/v1/admin/tenants")
    def admin_tenants(user: dict[str, Any] = Depends(current_user)):
        if user.get("role") != "platform_admin":
            raise HTTPException(status_code=403, detail="platform admin required")
        if (not user.get("totp_enabled") or not user.get("mfa_verified")) and not settings.dev_login_enabled:
            raise HTTPException(status_code=403, detail="verified TOTP is required for admin actions")
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
        was_active = tenant["status"] in {"starting", "running", "degraded"}
        if not store.reserve_engine_slot(tenant_id, settings.max_active_engines):
            raise HTTPException(status_code=409, detail="engine capacity is full; tenant queued")
        try:
            store.approve_live(tenant_id, user["id"])
            supervisor.set_live_mode(tenant["slug"], tenant["exchange_id"], tenant["market_type"])
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
