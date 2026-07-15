#!/usr/bin/env bash
set -euo pipefail

env_file="/etc/xauby/control.env"

if [[ "${EUID}" -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi
if [[ ! -f "${env_file}" ]]; then
  echo "missing ${env_file}" >&2
  exit 1
fi

read -r -p "Google OAuth Client ID: " client_id
read -r -s -p "Google OAuth Client Secret: " client_secret
printf '\n'

if [[ -z "${client_id}" || -z "${client_secret}" ]]; then
  echo "Client ID and Client Secret are required" >&2
  exit 1
fi

tmp_file="$(mktemp)"
trap 'rm -f "${tmp_file}"' EXIT
chmod 600 "${tmp_file}"

export XAUBY_CONFIGURE_GOOGLE_CLIENT_ID="${client_id}"
export XAUBY_CONFIGURE_GOOGLE_CLIENT_SECRET="${client_secret}"
python3 - "${env_file}" "${tmp_file}" <<'PY'
import os
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
target = pathlib.Path(sys.argv[2])
client_id = os.environ["XAUBY_CONFIGURE_GOOGLE_CLIENT_ID"]
client_secret = os.environ["XAUBY_CONFIGURE_GOOGLE_CLIENT_SECRET"]
lines = source.read_text(encoding="utf-8").splitlines()
replacements = {
    "XAUBY_GOOGLE_CLIENT_ID": client_id,
    "XAUBY_GOOGLE_CLIENT_SECRET": client_secret,
}
seen = set()
output = []
for line in lines:
    key = line.split("=", 1)[0]
    if key in replacements:
        output.append(f"{key}={replacements[key]}")
        seen.add(key)
    else:
        output.append(line)
for key, value in replacements.items():
    if key not in seen:
        output.append(f"{key}={value}")
target.write_text("\n".join(output) + "\n", encoding="utf-8")
PY
unset XAUBY_CONFIGURE_GOOGLE_CLIENT_ID XAUBY_CONFIGURE_GOOGLE_CLIENT_SECRET

install -m 0640 -o root -g xauby-control "${tmp_file}" "${env_file}"
systemctl restart xauby-control.service
sleep 2
curl --fail --silent --max-time 10 http://127.0.0.1:8790/healthz >/dev/null
echo "Google OAuth configuration saved; control plane is healthy."
