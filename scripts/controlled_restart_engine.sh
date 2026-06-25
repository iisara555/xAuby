#!/usr/bin/env bash
# Controlled xAuby engine restart: preflight first, then restart run_xauby.py.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-./venv/bin/python}"
LOG_PATH="${XAUBY_ENGINE_LOG:-core/logs/xauby_engine_bg.log}"

echo "[1/4] Running restart preflight..."
"$PY" scripts/controlled_restart_preflight.py

echo "[2/4] Stopping current run_xauby.py processes..."
pkill -TERM -f "python.*run_xauby.py" || true
sleep 3
if pgrep -f "python.*run_xauby.py" >/dev/null; then
  echo "[WARN] Engine still running after SIGTERM; sending SIGKILL."
  pkill -KILL -f "python.*run_xauby.py" || true
  sleep 1
fi

echo "[3/4] Starting engine in live mode..."
mkdir -p "$(dirname "$LOG_PATH")"
env BOT_READ_ONLY=false SIMULATE_ONLY=false nohup "$PY" run_xauby.py --live >"$LOG_PATH" 2>&1 &
PID="$!"

echo "[4/4] Waiting for state update..."
for _ in $(seq 1 10); do
  sleep 1
  if pgrep -f "python.*run_xauby.py" >/dev/null; then
    echo "[OK] Engine restarted. pid=$PID log=$LOG_PATH"
    exit 0
  fi
done

echo "[ERR] Engine did not stay running. Check $LOG_PATH" >&2
tail -80 "$LOG_PATH" >&2 || true
exit 1
