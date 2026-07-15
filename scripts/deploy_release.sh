#!/usr/bin/env bash
set -euo pipefail

archive="${1:?usage: deploy_release.sh <release.tar.gz> <release-id>}"
release_id="${2:?usage: deploy_release.sh <release.tar.gz> <release-id>}"
release_root="${XAUBY_RELEASE_ROOT:-/opt/xauby/releases}"
current="${XAUBY_CURRENT_LINK:-/opt/xauby/current}"
target="$release_root/$release_id"
maintenance="${XAUBY_SAAS_DATA_ROOT:-/var/lib/xauby/control}/maintenance"

if [[ "$EUID" -ne 0 ]]; then
  echo "Run as root" >&2
  exit 1
fi
if [[ ! -f "$archive" || -e "$target" ]]; then
  echo "Archive missing or release already exists" >&2
  exit 2
fi

"$current/scripts/predeploy_backup.sh" "$release_id"
install -d -m 0755 "$release_root" "$target"
tar -xzf "$archive" -C "$target" --strip-components=1
python3 -m venv "$target/venv"
"$target/venv/bin/pip" install -r "$target/requirements.txt"
"$target/venv/bin/pip" install -e "$target"
"$target/venv/bin/python" -m unittest tests.test_saas_control_plane -q

previous="$(readlink -f "$current" || true)"
ln -sfn "$target" "${current}.next"
mv -Tf "${current}.next" "$current"
systemctl daemon-reload
sudo -u xauby-control -H "$current/venv/bin/xauby-admin" migrate
systemctl restart xauby-control.service

if ! curl --fail --silent --max-time 10 http://127.0.0.1:8790/healthz >/dev/null; then
  if [[ -n "$previous" ]]; then
    ln -sfn "$previous" "${current}.rollback"
    mv -Tf "${current}.rollback" "$current"
    systemctl restart xauby-control.service
  fi
  echo "Control-plane health check failed; code symlink rolled back. Restore DB only if migration was incompatible." >&2
  exit 3
fi

# Engines stay stopped. A human must reconcile the exchange position before start.
echo "Release installed and control plane healthy. Engines remain stopped for position reconciliation."
echo "After reconciliation: rm -f '$maintenance' && systemctl start xauby.target"
