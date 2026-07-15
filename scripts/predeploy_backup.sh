#!/usr/bin/env bash
set -euo pipefail

release_id="${1:?usage: predeploy_backup.sh <release-id>}"
root="${XAUBY_INSTALL_ROOT:-/opt/xauby/current}"
backup_root="${XAUBY_BACKUP_ROOT:-/var/lib/xauby/backups/predeploy}"
maintenance="${XAUBY_SAAS_DATA_ROOT:-/var/lib/xauby/control}/maintenance"

install -d -m 0700 "$(dirname "$maintenance")" "$backup_root"
touch "$maintenance"

# Stop only tenant engines. The control plane remains available in maintenance mode.
systemctl stop xauby.target

"$root/venv/bin/python" "$root/scripts/saas_backup.py" \
  --output "$backup_root" \
  --kind predeploy \
  --release-id "$release_id" \
  --retention-days 30 \
  --include-secrets \
  --host-config /etc/xauby/control.env \
  --host-config /etc/caddy/Caddyfile
