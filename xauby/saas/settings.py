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
    credential_master_key: str = ""
    # Rotation (P2.2). The active key encrypts and stamps its id into every
    # envelope; retired keys ("<id>:<base64>,...") only decrypt, and exist so
    # blobs written before a rotation stay readable until they are rewrapped by
    # scripts/rotate_credential_key.py. Defaults reproduce the single-key
    # deployment exactly.
    credential_master_key_id: int = 1
    credential_retired_keys: str = ""
    credential_runtime_root: Path | None = None
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
    manual_trading_enabled: bool = False
    # Comma-separated static egress IP(s) of the engine host. Surfaced in the
    # API-connection screen so operators can restrict their exchange API key to
    # these IPs (OKX "Link IP address"). Empty = no IP shown (guidance only).
    api_whitelist_ips: str = ""
    systemctl_bin: str = "systemctl"
    engine_unit_template: str = "xauby-engine@{tenant}.service"
    provision_helper: str = ""
    service_helper: str = ""
    cookie_secure: bool = True
    dev_login_enabled: bool = False

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
        credential_key = os.environ.get("XAUBY_CREDENTIAL_MASTER_KEY", "").strip()
        if not credential_key and not dev:
            raise ValueError("XAUBY_CREDENTIAL_MASTER_KEY is required outside dev-login mode")
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
            credential_master_key=credential_key,
            # Not clamped on purpose. A typo'd key id must stop the control
            # plane rather than be quietly rounded to 1, which would stamp
            # envelopes with a key the operator does not think is in use.
            credential_master_key_id=int(
                os.environ.get("XAUBY_CREDENTIAL_MASTER_KEY_ID", "1") or "1"
            ),
            credential_retired_keys=os.environ.get(
                "XAUBY_CREDENTIAL_RETIRED_KEYS", ""
            ).strip(),
            credential_runtime_root=Path(
                os.environ.get("XAUBY_CREDENTIAL_RUNTIME_ROOT", "/run/xauby/credentials")
            ).resolve(),
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
            manual_trading_enabled=os.environ.get(
                "XAUBY_MANUAL_TRADING_ENABLED", "0"
            ).lower() in {"1", "true", "yes"},
            api_whitelist_ips=os.environ.get("XAUBY_API_WHITELIST_IPS", "").strip(),
            systemctl_bin=os.environ.get("XAUBY_SYSTEMCTL", "systemctl"),
            provision_helper=os.environ.get("XAUBY_PROVISION_HELPER", ""),
            service_helper=os.environ.get("XAUBY_SERVICE_HELPER", ""),
            cookie_secure=os.environ.get("XAUBY_SAAS_COOKIE_SECURE", "1").lower()
            not in {"0", "false", "no"},
            dev_login_enabled=dev,
        )

    def ensure_directories(self) -> None:
        for path in (self.data_root, self.tenant_config_root, self.tenant_runtime_root):
            path.mkdir(parents=True, exist_ok=True)

    def whitelist_ip_list(self) -> list[str]:
        """Parsed egress IPs (comma-separated env), empty list when unset."""
        return [ip.strip() for ip in self.api_whitelist_ips.split(",") if ip.strip()]
