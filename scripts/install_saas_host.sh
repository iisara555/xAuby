#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 1
fi

repo="$(cd "$(dirname "$0")/.." && pwd)"
install_root="${XAUBY_INSTALL_ROOT:-/opt/xauby/current}"
if [[ "$repo" != "$install_root" ]]; then
  echo "Repository is at $repo; systemd units will be adjusted from /opt/xauby/current." >&2
fi

command -v setfacl >/dev/null || { echo "setfacl is required (install the acl package)." >&2; exit 1; }
command -v visudo >/dev/null || { echo "visudo is required." >&2; exit 1; }

if ! id xauby-control >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/xauby/control --shell /usr/sbin/nologin xauby-control
fi
if ! getent group xauby-engines >/dev/null; then
  groupadd --system xauby-engines
fi
install -d -o xauby-control -g xauby-control -m 0700 /var/lib/xauby/control
install -d -o xauby-control -g xauby-control -m 0700 /var/lib/xauby/runtime
install -d -o xauby-control -g xauby-control -m 0700 /var/lib/xauby/backups
install -d -o root -g xauby-engines -m 0770 /var/lib/xauby/account_locks
install -d -o xauby-control -g xauby-control -m 0700 /etc/xauby/tenants
install -d -m 0755 /usr/local/libexec
setfacl -m g:xauby-engines:--x /etc/xauby/tenants /var/lib/xauby/runtime

install -m 0755 "$repo/deploy/xauby-provision-tenant" /usr/local/libexec/xauby-provision-tenant
install -m 0755 "$repo/deploy/xauby-service-control" /usr/local/libexec/xauby-service-control
install -m 0755 "$repo/deploy/xauby-materialize-credentials" /usr/local/libexec/xauby-materialize-credentials
install -m 0440 "$repo/deploy/xauby-control.sudoers" /etc/sudoers.d/xauby-control
visudo -cf /etc/sudoers.d/xauby-control >/dev/null

# xauby-healthcheck.* and xauby-deadman@.* were previously authored but never
# installed by this loop, so a fresh host had no external monitoring at all.
for unit in xauby-control.service xauby-engine@.service xauby.target \
            xauby-backup.service xauby-backup.timer \
            xauby-healthcheck.service xauby-healthcheck.timer \
            xauby-deadman@.service xauby-deadman@.timer; do
  sed "s#/opt/xauby/current#$install_root#g" "$repo/deploy/systemd/$unit" > "/etc/systemd/system/$unit"
done

if [[ ! -f /etc/xauby/control.env ]]; then
  sed \
    -e "s#/opt/xauby/current#$install_root#g" \
    -e "s#replace-with-at-least-32-random-bytes#$(openssl rand -hex 32)#" \
    -e "s#replace-with-base64-encoded-32-random-bytes#$(openssl rand -base64 32)#" \
    "$repo/.env.saas.example" > /etc/xauby/control.env
  chmod 0640 /etc/xauby/control.env
  chown root:xauby-control /etc/xauby/control.env
fi

if [[ ! -x "$install_root/venv/bin/python" ]]; then
  python3 -m venv "$install_root/venv"
fi
"$install_root/venv/bin/pip" install -r "$install_root/requirements.txt"
"$install_root/venv/bin/pip" install -e "$install_root"

systemctl daemon-reload
install -d -o xauby-control -g xauby-control -m 0700 /var/lib/xauby/deadman

# The dead-man's switch needs Telegram credentials that survive a reboot and do
# not depend on the engine having started. Seeded empty; fill it in to arm the
# alert channel. Without it the switch still detects silence, but can only log.
if [[ ! -f /etc/xauby/deadman.env ]]; then
  printf '%s\n' \
    '# Alert channel for xauby-deadman@.service. Must NOT be the engine-materialized' \
    '# /run/xauby/credentials/<tenant>.env — that only exists after the engine starts.' \
    'TELEGRAM_BOT_TOKEN=' \
    'TELEGRAM_CHAT_ID=' > /etc/xauby/deadman.env
fi
chown root:xauby-control /etc/xauby/deadman.env
chmod 0640 /etc/xauby/deadman.env

systemctl enable xauby.target xauby-backup.timer xauby-healthcheck.timer

echo "Host installation complete."
echo "1. Edit /etc/xauby/control.env (domain, SMTP and Google OAuth credentials)."
echo "2. Install deploy/Caddyfile with your real domain."
echo "3. Run: sudo -u xauby-control -H $install_root/venv/bin/xauby-admin migrate"
echo "4. Bootstrap Owner, then start caddy and xauby-control."
