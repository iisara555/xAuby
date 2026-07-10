#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

HOST="${WEBUI_HOST:-127.0.0.1}"
PORT="${WEBUI_PORT:-8787}"

PY="${PYTHON:-./venv/bin/python}"
if [[ ! -x "$PY" ]]; then
  PY="${PYTHON:-python3}"
fi

echo "Starting xAuby WebUI at http://${HOST}:${PORT}"
echo "Windows SSH tunnel: ssh -L ${PORT}:127.0.0.1:${PORT} user@your-vps-ip"
exec "$PY" -m xauby.webui --host "$HOST" --port "$PORT"
