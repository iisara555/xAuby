from __future__ import annotations

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
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from xauby.runtime.manual_orders import VALID_MANUAL_INTENTS, queue_manual_order_command
from xauby.saas.catalog import public_catalog, validate_profile
from xauby.saas.mailer import Mailer
from xauby.saas.security import (
    new_totp_secret,
    sign_state,
    verify_password,
    verify_state,
    verify_totp,
)
from xauby.saas.settings import SaaSSettings
from xauby.saas.store import ControlPlaneStore
from xauby.saas.supervisor import TenantSupervisor
from xauby.saas.runtime import RuntimeGateway

SESSION_COOKIE = "xauby_saas_session"
OAUTH_STATE_COOKIE = "xauby_saas_oauth_state"


class ExchangeConnectBody(BaseModel):
    exchange_id: str = Field(min_length=2, max_length=32)
    api_key: str = Field(min_length=4, max_length=512)
    api_secret: str = Field(min_length=4, max_length=512)
    passphrase: str = Field(default="", max_length=512)
    market_type: str = Field(default="swap", pattern="^(spot|swap)$")
    withdraw_disabled_attested: bool


class TradePinBody(BaseModel):
    pin: str = Field(pattern=r"^[0-9]{8,12}$")
    current_pin: str | None = Field(default=None, pattern=r"^[0-9]{8,12}$")


class OrderPreviewBody(BaseModel):
    symbol: str = Field(min_length=3, max_length=32)
    intent: str
    fraction: float | None = None
    management_mode: str = Field(default="strategy", pattern="^(strategy|manual)$")


class OrderConfirmBody(BaseModel):
    trade_pin: str = Field(pattern=r"^[0-9]{8,12}$")
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


class TradingProfileBody(BaseModel):
    preset_ids: list[str] = Field(min_length=1, max_length=3)
    active_preset_id: str
    risk: dict[str, Any]


class AccountStatusBody(BaseModel):
    status: str = Field(pattern="^(active|rejected|suspended)$")


def _safe_slug_from_email(email: str, fallback: str) -> str:
    local = str(email).split("@", 1)[0].lower()
    slug = re.sub(r"[^a-z0-9]+", "-", local).strip("-")[:48]
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
    mailer = mailer or Mailer(settings)
    runtime = runtime or RuntimeGateway(settings, supervisor)
    app = FastAPI(title="xAuby SaaS Control Plane", version="0.1.0")
    app.state.settings = settings
    app.state.store = store
    app.state.supervisor = supervisor
    app.state.mailer = mailer
    app.state.runtime = runtime

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

    @app.get("/")
    def index(request: Request):
        if store.session(request.cookies.get(SESSION_COOKIE, "")) is None:
            return RedirectResponse("/login")
        return FileResponse(static_root / "index.html")

    @app.get("/login")
    def login_page():
        return FileResponse(static_root / "login.html")

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
        if store.user_count() >= settings.max_users:
            raise HTTPException(status_code=409, detail="pilot capacity is full")
        try:
            user, token = store.create_password_user(body.email, body.password)
            mailer.send(
                user["email"], "ยืนยันอีเมล xAuby",
                f"ยืนยันอีเมลภายใน 24 ชั่วโมง:\n{settings.public_base_url}/verify-email?token={quote(token)}",
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"ok": True, "status": "pending_email"}

    @app.post("/auth/verify-email")
    def verify_email(body: TokenBody):
        try:
            user = store.verify_email_token(body.token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "status": user["account_status"]}

    @app.post("/auth/login")
    def password_login(body: LoginBody, response: Response):
        user = store.authenticate_password(body.email, body.password)
        if not user:
            raise HTTPException(status_code=401, detail="email or password is incorrect")
        if not user.get("email_verified"):
            raise HTTPException(status_code=403, detail="email verification is required")
        if user.get("account_status") in {"rejected", "suspended"}:
            raise HTTPException(status_code=403, detail=f"account is {user['account_status']}")
        mfa_ok = not bool(user.get("totp_enabled"))
        if user.get("totp_enabled"):
            mfa_ok = verify_totp(str(user.get("totp_secret") or ""), body.totp_code)
            if not mfa_ok:
                raise HTTPException(status_code=403, detail="valid TOTP code is required")
        token, csrf = store.create_session(user["id"], mfa_verified=mfa_ok)
        set_session_cookie(response, token)
        store.audit("login", user_id=user["id"])
        return {"ok": True, "csrf_token": csrf, "status": user["account_status"]}

    @app.post("/auth/forgot-password")
    def forgot_password(body: EmailBody):
        user = store.user_by_email(body.email)
        if user and user.get("password_hash"):
            token = store.create_auth_token(user["id"], "password_reset", ttl_seconds=1800)
            try:
                mailer.send(
                    user["email"], "ตั้งรหัสผ่าน xAuby ใหม่",
                    f"ลิงก์นี้ใช้ได้ 30 นาที:\n{settings.public_base_url}/reset-password?token={quote(token)}",
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
                body.email, "ยืนยันอีเมลใหม่ของ xAuby",
                f"ยืนยันอีเมลใหม่ภายใน 1 ชั่วโมง:\n{settings.public_base_url}/confirm-email?token={quote(token)}",
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
    def totp_setup(user: dict[str, Any] = Depends(csrf_user)):
        secret = new_totp_secret()
        store.set_totp_secret(user["id"], secret)
        label = quote(f"xAuby:{user['email']}")
        return {"secret": secret, "otpauth_uri": f"otpauth://totp/{label}?secret={secret}&issuer=xAuby"}

    @app.post("/auth/totp/enable")
    def totp_enable(body: TotpBody, request: Request,
                    user: dict[str, Any] = Depends(csrf_user)):
        if not user.get("totp_secret") or not verify_totp(user["totp_secret"], body.code):
            raise HTTPException(status_code=400, detail="TOTP code is invalid")
        codes = [secrets.token_hex(5).upper() for _ in range(8)]
        store.enable_totp(user["id"], codes)
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
    def google_start():
        if not settings.google_client_id or not settings.google_client_secret:
            raise HTTPException(status_code=503, detail="Google sign-in is not configured")
        nonce = secrets.token_urlsafe(24)
        signed = sign_state(settings.session_secret, {"nonce": nonce}, ttl_seconds=600)
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
        existing = store.user_by_email(str(claims.get("email") or ""))
        if existing is None and store.user_count() >= settings.max_users:
            raise HTTPException(status_code=409, detail="pilot capacity is full")
        user, _ = store.upsert_google_user(str(claims.get("email") or ""), str(claims.get("sub") or ""))
        tenant = store.tenant_for_user(str(user["id"]))
        token, _ = store.create_session(str(user["id"]), mfa_verified=not bool(user.get("totp_enabled")))
        store.audit("login", tenant_id=tenant["id"] if tenant else None, user_id=user["id"])
        response = RedirectResponse("/")
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
        return {
            "id": user["id"], "email": user["email"], "role": user["role"],
            "csrf_token": user["csrf_token"], "tenant": tenant,
            "account_status": user.get("account_status"),
            "email_verified": bool(user.get("email_verified")),
            "totp_enabled": bool(user.get("totp_enabled")),
            "mfa_verified": bool(user.get("mfa_verified")),
            "trade_pin_configured": bool(user.get("trade_pin_hash")),
        }

    @app.get("/api/v1/catalog")
    def catalog(user: dict[str, Any] = Depends(current_user)):
        return public_catalog()

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
            if tenant["live_status"] in {"requested", "approved", "active"}:
                supervisor.stop(tenant["slug"])
                store.update_tenant(tenant["id"], status="stopped")
                store.reset_live_approval(tenant["id"], user["id"], "trading profile changed")
            compiled = supervisor.apply_profile(
                tenant["slug"], body.preset_ids, body.active_preset_id, body.risk
            )
            store.save_trading_profile(tenant["id"], user["id"], profile)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True, "profile": compiled, "mode": "sim", "live_reapproval_required": True}

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

    @app.post("/api/v1/exchange/connect")
    def exchange_connect(body: ExchangeConnectBody, user: dict[str, Any] = Depends(csrf_user)):
        if not body.withdraw_disabled_attested:
            raise HTTPException(status_code=422, detail="withdraw permission must be disabled")
        tenant = own_tenant(user)
        if tenant["live_status"] in {"active", "approved"}:
            try:
                supervisor.stop(tenant["slug"])
            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail="could not stop live engine before credential rotation",
                ) from exc
        supervisor.store_credentials(
            tenant["slug"], body.exchange_id, body.api_key, body.api_secret, body.passphrase
        )
        supervisor.set_sim_mode(tenant["slug"])
        store.reset_live_approval(tenant["id"], user["id"], "exchange credentials changed")
        store.update_tenant(
            tenant["id"], exchange_id=body.exchange_id.lower(), market_type=body.market_type,
            status="stopped" if tenant["live_status"] in {"active", "approved"} else tenant["status"],
        )
        connection = store.set_exchange_connection(
            tenant["id"], body.exchange_id.lower(), body.api_key[-4:],
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
            result = supervisor.probe_exchange(tenant["slug"])
        except Exception as exc:
            store.set_exchange_connection(
                tenant["id"], tenant["exchange_id"], connection["key_last4"], status="failed"
            )
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        capabilities = dict(result.get("capabilities") or {})
        capabilities["withdraw_disabled_attested"] = True
        updated = store.set_exchange_connection(
            tenant["id"], tenant["exchange_id"], connection["key_last4"],
            status="tested", capabilities=capabilities,
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
        return store.request_live(tenant["id"], user["id"])

    @app.post("/api/v1/orders/preview")
    def order_preview(body: OrderPreviewBody, user: dict[str, Any] = Depends(csrf_user)):
        tenant = own_tenant(user)
        intent = body.intent.upper()
        if intent not in VALID_MANUAL_INTENTS:
            raise HTTPException(status_code=422, detail="unsupported manual order intent")
        if intent.startswith("PARTIAL_CLOSE") and not body.fraction:
            raise HTTPException(status_code=422, detail="fraction is required for partial close")
        if tenant["live_status"] not in {"approved", "active"} and not supervisor.read_curated_config(tenant["slug"])["simulate_only"]:
            raise HTTPException(status_code=403, detail="live trading is not approved")
        profile = store.trading_profile(tenant["id"])
        if profile:
            active = next(
                (item for item in profile.get("presets", [])
                 if item.get("id") == profile.get("active_preset_id")), None
            )
            if active and body.symbol.upper().replace("_", "") != active["symbol"]:
                raise HTTPException(status_code=422, detail="manual order must use the active pair")
        state = supervisor.read_state(tenant["slug"])
        focus = (state.get("by_symbol") or {}).get(body.symbol.upper()) or state
        payload = {
            "symbol": body.symbol.upper().replace("_", ""), "intent": intent,
            "fraction": body.fraction, "management_mode": body.management_mode,
            "expected_position_side": str((focus.get("position") or focus).get("position_side") or ""),
            "expected_quantity": float((focus.get("position") or focus).get("quantity") or 0.0),
        }
        warnings = ["Manual order bypasses the strategy entry signal"] if intent.startswith("OPEN_") else []
        challenge = store.create_challenge(tenant["id"], user["id"], payload)
        store.audit("manual_order_previewed", tenant_id=tenant["id"], user_id=user["id"],
                    payload={"challenge_id": challenge["id"], "intent": intent})
        return {
            "challenge_id": challenge["id"], "digest": challenge["digest"],
            "expires_at": challenge["expires_at"], "order": payload, "warnings": warnings,
        }

    @app.post("/api/v1/orders/{challenge_id}/confirm")
    def order_confirm(challenge_id: str, body: OrderConfirmBody,
                      user: dict[str, Any] = Depends(csrf_user)):
        tenant = own_tenant(user)
        ok, reason = store.check_trade_pin(user["id"], body.trade_pin)
        if not ok:
            store.audit("trade_pin_failed", tenant_id=tenant["id"], user_id=user["id"])
            raise HTTPException(status_code=403, detail=reason)
        with store.connection() as conn:
            challenge = conn.execute(
                "SELECT * FROM order_challenges WHERE id=? AND tenant_id=? AND user_id=?",
                (challenge_id, tenant["id"], user["id"]),
            ).fetchone()
        if challenge is None:
            raise HTTPException(status_code=404, detail="order challenge not found")
        payload = json.loads(challenge["payload_json"])
        command_id = hashlib.sha256(
            f"{tenant['id']}:{body.idempotency_key}".encode()
        ).hexdigest()[:32]
        try:
            confirmed = store.confirm_challenge(
                challenge_id, tenant["id"], user["id"], command_id
            )
            command = queue_manual_order_command(
                payload["symbol"], payload["intent"],
                idempotency_key=body.idempotency_key, fraction=payload.get("fraction"),
                management_mode=payload.get("management_mode", "strategy"),
                actor_user_id=user["id"], queue_path=str(supervisor.queue_path(tenant["slug"])),
                expected_position_side=payload.get("expected_position_side", ""),
                expected_quantity=payload.get("expected_quantity"),
                request_id=command_id,
            )
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        store.audit("manual_order_confirmed", tenant_id=tenant["id"], user_id=user["id"],
                    payload={"command_id": command["request_id"], "intent": payload["intent"]})
        return {"ok": True, "command": command, "challenge": confirmed}

    @app.get("/api/v1/orders")
    def orders(user: dict[str, Any] = Depends(current_user)):
        tenant = own_tenant(user)
        path = supervisor.queue_path(tenant["slug"])
        if not path.exists():
            return {"items": []}
        import sqlite3

        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT request_id,symbol,intent,fraction,status,created_at,completed_at,result_reason "
                "FROM manual_order_commands ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        finally:
            conn.close()
        return {"items": [dict(row) for row in rows]}

    @app.get("/api/v1/admin/users")
    def admin_users(user: dict[str, Any] = Depends(current_user)):
        if user.get("role") != "platform_admin":
            raise HTTPException(status_code=403, detail="platform admin required")
        if (not user.get("totp_enabled") or not user.get("mfa_verified")) and not settings.dev_login_enabled:
            raise HTTPException(status_code=403, detail="verified TOTP is required for admin actions")
        return {"items": store.list_users(), "capacity": settings.max_users}

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
