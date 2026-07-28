from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from xauby.api import create_exchange_client
from xauby.runtime.exchange_config import resolve_exchange_credentials
from xauby.saas.withdraw_check import check_withdraw_disabled


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe one tenant exchange connection")
    parser.add_argument("--config-dir", required=True)
    args = parser.parse_args(argv)
    config_path = Path(args.config_dir) / "bot_config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    key, secret, base_url = resolve_exchange_credentials(config)
    if not key or not secret:
        raise SystemExit("missing tenant exchange credentials")
    client = create_exchange_client(config, key, secret, base_url)
    try:
        balances = client.get_balances()
        info = client.get_exchange_info()
        capabilities = dict(getattr(client, "capabilities", {}) or {})
        exchange_id = str((config.get("exchange") or {}).get("ccxt_id") or "")
        # Ask the venue rather than trusting the onboarding checkbox. Tri-state:
        # None means we could not determine it, and that is reported as such.
        withdraw = check_withdraw_disabled(client, exchange_id)
        payload = {
            "ok": isinstance(balances, dict) and isinstance(info, dict),
            "exchange_id": exchange_id,
            "capabilities": capabilities,
            "withdraw_disabled_verified": withdraw["withdraw_disabled"],
            "withdraw_permission_checked": withdraw["checked"],
            "withdraw_permission_detail": withdraw["detail"],
        }
        print(json.dumps(payload, sort_keys=True))
        return 0 if payload["ok"] else 1
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    raise SystemExit(main())
