from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SaaSSettings:
    project_root: Path
    data_root: Path
    tenant_config_root: Path
    tenant_runtime_root: Path
    database_path: Path
    public_base_url: str
    session_secret: str
    google_client_id: str = ""
    google_client_secret: str = ""
    owner_email: str = "iisara555@gmail.com"
    max_users: int = 3
    max_active_engines: int = 2
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    live_activation_enabled: bool = False
    systemctl_bin: str = "systemctl"
    engine_unit_template: str = "xauby-engine@{tenant}.service"
    provision_helper: str = ""
    service_helper: str = ""
    cookie_secure: bool = True
    dev_login_enabled: bool = False
    legacy_owner_slug: str = ""
    legacy_webui_url: str = ""
    legacy_webui_token: str = ""
    legacy_webui_timeout_seconds: float = 3.0

    @classmethod
    def from_env(cls, project_root: str | None = None) -> SaaSSettings:
        control_env = os.environ.get("XAUBY_CONTROL_ENV", "/etc/xauby/control.env")
        if os.path.isfile(control_env):
            try:
                from dotenv import load_dotenv

                load_dotenv(control_env, override=False)
            except OSError:
                pass
        root = Path(project_root or os.environ.get("XAUBY_PROJECT_ROOT") or os.getcwd()).resolve()
        data = Path(os.environ.get("XAUBY_SAAS_DATA_ROOT") or root / "saas-data").resolve()
        secret = os.environ.get("XAUBY_SAAS_SESSION_SECRET", "")
        dev = os.environ.get("XAUBY_SAAS_DEV_LOGIN", "").lower() in {"1", "true", "yes"}
        if not secret and not dev:
            raise ValueError("XAUBY_SAAS_SESSION_SECRET is required outside dev-login mode")
        return cls(
            project_root=root,
            data_root=data,
            tenant_config_root=Path(
                os.environ.get("XAUBY_TENANT_CONFIG_ROOT") or data / "tenants"
            ).resolve(),
            tenant_runtime_root=Path(
                os.environ.get("XAUBY_TENANT_RUNTIME_ROOT") or data / "runtime"
            ).resolve(),
            database_path=Path(
                os.environ.get("XAUBY_SAAS_DB") or data / "control-plane.db"
            ).resolve(),
            public_base_url=os.environ.get("XAUBY_PUBLIC_BASE_URL", "http://127.0.0.1:8790").rstrip("/"),
            session_secret=secret or "dev-only-session-secret",
            google_client_id=os.environ.get("XAUBY_GOOGLE_CLIENT_ID", ""),
            google_client_secret=os.environ.get("XAUBY_GOOGLE_CLIENT_SECRET", ""),
            owner_email=os.environ.get("XAUBY_OWNER_EMAIL", "iisara555@gmail.com").lower(),
            max_users=max(1, int(os.environ.get("XAUBY_MAX_USERS", "3"))),
            max_active_engines=max(1, int(os.environ.get("XAUBY_MAX_ACTIVE_ENGINES", "2"))),
            smtp_host=os.environ.get("XAUBY_SMTP_HOST", ""),
            smtp_port=int(os.environ.get("XAUBY_SMTP_PORT", "587")),
            smtp_username=os.environ.get("XAUBY_SMTP_USERNAME", ""),
            smtp_password=os.environ.get("XAUBY_SMTP_PASSWORD", ""),
            smtp_from=os.environ.get("XAUBY_SMTP_FROM", ""),
            live_activation_enabled=os.environ.get(
                "XAUBY_LIVE_ACTIVATION_ENABLED", "0"
            ).lower() in {"1", "true", "yes"},
            systemctl_bin=os.environ.get("XAUBY_SYSTEMCTL", "systemctl"),
            provision_helper=os.environ.get("XAUBY_PROVISION_HELPER", ""),
            service_helper=os.environ.get("XAUBY_SERVICE_HELPER", ""),
            cookie_secure=os.environ.get("XAUBY_SAAS_COOKIE_SECURE", "1").lower()
            not in {"0", "false", "no"},
            dev_login_enabled=dev,
            legacy_owner_slug=os.environ.get("XAUBY_LEGACY_OWNER_SLUG", "").strip(),
            legacy_webui_url=os.environ.get("XAUBY_LEGACY_WEBUI_URL", "").strip().rstrip("/"),
            legacy_webui_token=os.environ.get("XAUBY_LEGACY_WEBUI_TOKEN", "").strip(),
            legacy_webui_timeout_seconds=max(
                0.5, min(10.0, float(os.environ.get("XAUBY_LEGACY_WEBUI_TIMEOUT", "3")))
            ),
        )

    def ensure_directories(self) -> None:
        for path in (self.data_root, self.tenant_config_root, self.tenant_runtime_root):
            path.mkdir(parents=True, exist_ok=True)
