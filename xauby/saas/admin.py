from __future__ import annotations

import argparse
import json

from xauby.saas.settings import SaaSSettings
from xauby.saas.store import ControlPlaneStore
from xauby.saas.supervisor import TenantSupervisor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xauby-admin", description="xAuby SaaS administration")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="Create or upgrade the control-plane database")
    owner = sub.add_parser("bootstrap-owner", help="Create the platform owner and personal tenant")
    owner.add_argument("--email", required=True)
    owner.add_argument("--tenant", required=True)
    reset = sub.add_parser(
        "password-reset-link", help="Create a one-time Owner recovery link without sending email"
    )
    reset.add_argument("--email", required=True)
    args = parser.parse_args(argv)

    settings = SaaSSettings.from_env()
    settings.ensure_directories()
    store = ControlPlaneStore(settings.database_path)
    store.migrate()
    if args.command == "migrate":
        print(json.dumps({"ok": True, "database": str(settings.database_path)}))
        return 0
    if args.command == "password-reset-link":
        user = store.user_by_email(args.email)
        if user is None:
            raise SystemExit("user not found")
        token = store.create_auth_token(user["id"], "password_reset", ttl_seconds=1800)
        print(json.dumps({
            "ok": True,
            "expires_in_seconds": 1800,
            "url": f"{settings.public_base_url}/reset-password?token={token}",
        }, sort_keys=True))
        return 0
    user, tenant = store.bootstrap_owner(args.email, args.tenant)
    TenantSupervisor(settings).provision(tenant["slug"])
    print(json.dumps({
        "ok": True,
        "owner": {"id": user["id"], "email": user["email"], "role": user["role"]},
        "tenant": tenant,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
