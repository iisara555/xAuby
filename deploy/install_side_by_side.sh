#!/usr/bin/env bash
set -euo pipefail

archive="${1:?usage: install_side_by_side.sh <release-archive> <sha256>}"
expected="${2:?usage: install_side_by_side.sh <release-archive> <sha256>}"
release_id="xauby-saas-v0.2.0-20260715"
release_root="/opt/xauby/releases"
target="$release_root/$release_id"

[[ "$EUID" -eq 0 ]] || { echo "run as root" >&2; exit 1; }
actual="$(sha256sum "$archive" | awk '{print $1}')"
[[ "$actual" == "$expected" ]] || { echo "release checksum mismatch" >&2; exit 2; }
compgen -G '/root/xAuby/backups/predeploy/xauby-legacy-predeploy-*.tar.gz' >/dev/null || {
  echo "verified pre-deploy backup is missing" >&2; exit 3;
}

old_pid="$(pgrep -f '/root/xAuby/venv/bin/python run_xauby.py --live|./venv/bin/python run_xauby.py --live' | head -1 || true)"
[[ -n "$old_pid" ]] || { echo "legacy live engine is not running; refusing unexpected state" >&2; exit 4; }

export DEBIAN_FRONTEND=noninteractive
apt-get install -y --no-install-recommends caddy acl

install -d -m 0755 "$release_root"
[[ ! -e "$target" ]] || { echo "release target already exists" >&2; exit 5; }
install -d -m 0755 "$target"
tar -xzf "$archive" -C "$target" --strip-components=1
cp -a /root/xAuby/venv "$target/venv"
ln -sfn "$target" /opt/xauby/current.next
mv -Tf /opt/xauby/current.next /opt/xauby/current

id xauby-control >/dev/null 2>&1 || useradd \
  --system --home-dir /var/lib/xauby/control --shell /usr/sbin/nologin xauby-control
install -d -o xauby-control -g xauby-control -m 0700 /var/lib/xauby/control
install -d -o xauby-control -g xauby-control -m 0700 /var/lib/xauby/runtime
install -d -o xauby-control -g xauby-control -m 0700 /var/lib/xauby/backups
install -d -o root -g xauby-control -m 0770 /var/lib/xauby/account_locks
install -d -o xauby-control -g xauby-control -m 0700 /etc/xauby/tenants
install -d -m 0755 /usr/local/libexec
install -m 0755 "$target/deploy/xauby-provision-tenant" /usr/local/libexec/xauby-provision-tenant
install -m 0755 "$target/deploy/xauby-service-control" /usr/local/libexec/xauby-service-control
install -m 0440 "$target/deploy/xauby-control.sudoers" /etc/sudoers.d/xauby-control
visudo -cf /etc/sudoers.d/xauby-control >/dev/null

for unit in xauby-control.service xauby-engine@.service xauby.target xauby-backup.service xauby-backup.timer; do
  install -m 0644 "$target/deploy/systemd/$unit" "/etc/systemd/system/$unit"
done

if [[ ! -f /etc/xauby/control.env ]]; then
  secret="$(openssl rand -hex 32)"
  sed \
    -e "s#replace-with-at-least-32-random-bytes#$secret#" \
    -e 's#^XAUBY_GOOGLE_CLIENT_ID=.*#XAUBY_GOOGLE_CLIENT_ID=#' \
    -e 's#^XAUBY_GOOGLE_CLIENT_SECRET=.*#XAUBY_GOOGLE_CLIENT_SECRET=#' \
    -e 's#^XAUBY_SMTP_HOST=.*#XAUBY_SMTP_HOST=#' \
    -e 's#^XAUBY_SMTP_PASSWORD=.*#XAUBY_SMTP_PASSWORD=#' \
    "$target/.env.saas.example" > /etc/xauby/control.env
fi
chmod 0640 /etc/xauby/control.env
chown root:xauby-control /etc/xauby/control.env

install -m 0644 "$target/deploy/Caddyfile" /etc/caddy/Caddyfile
systemctl daemon-reload
sudo -u xauby-control -H "$target/venv/bin/python" -m xauby.saas.admin migrate
sudo -u xauby-control -H "$target/venv/bin/python" -m xauby.saas.admin \
  bootstrap-owner --email iisara555@gmail.com --tenant owner-itsara
systemctl enable xauby-control.service xauby-backup.timer xauby.target
systemctl restart xauby-control.service
systemctl enable --now caddy
systemctl start xauby-backup.timer

curl --fail --silent --max-time 10 http://127.0.0.1:8790/healthz >/dev/null
systemctl reload caddy
sleep 2
curl --fail --silent --max-time 15 https://188.166.253.203.sslip.io/healthz >/dev/null

new_pid="$(pgrep -f '/root/xAuby/venv/bin/python run_xauby.py --live|./venv/bin/python run_xauby.py --live' | head -1 || true)"
[[ "$new_pid" == "$old_pid" ]] || { echo "legacy engine PID changed unexpectedly" >&2; exit 6; }
echo "side-by-side control plane ready; legacy engine unchanged at pid $old_pid; Live activation locked"
