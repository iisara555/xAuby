#!/usr/bin/env bash
# Start (or restart) the Textual dashboard in a detached tmux session.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SESSION="${XAUBY_TMUX_SESSION:-dashboard}"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

# Kill only our own dashboard session, never the whole tmux server —
# other unrelated sessions on the host must survive.
tmux kill-session -t "$SESSION" 2>/dev/null || true
sleep 0.5

ENGINE_SESSION="${XAUBY_ENGINE_TMUX_SESSION:-xauby-engine}"
if tmux has-session -t "$ENGINE_SESSION" 2>/dev/null; then
  echo "[INFO] Engine tmux session '$ENGINE_SESSION' is already running. Continuing with TUI."
elif ! pgrep -f 'run_xauby\.py' >/dev/null 2>&1; then
  mkdir -p core/logs
  # Try to start the engine; exit code 2 means another instance grabbed the
  # lock just before pgrep ran — treat that as "already running", not an error.
  nohup ./venv/bin/python run_xauby.py --live >> core/logs/xauby_engine_bg.log 2>&1 &
  ENGINE_PID=$!
  sleep 3
  if ! kill -0 "$ENGINE_PID" 2>/dev/null; then
    EC=$(wait "$ENGINE_PID" 2>/dev/null || echo $?)
    if [ "${EC:-0}" -eq 2 ]; then
      echo "[INFO] Engine already running (duplicate instance blocked). Continuing with TUI."
    else
      echo "[WARN] Engine process exited early (code=${EC:-?}). Check core/logs/xauby_engine_bg.log" >&2
    fi
  fi
fi

tmux new-session -d -s "$SESSION" -c "$ROOT" \
  "env -u NO_COLOR -u FORCE_COLOR FROM_LAUNCHER=true COLORTERM=truecolor TERM=xterm-256color XAUBY_START_SCREEN=dashboard ./venv/bin/python -m xauby.ui.textual_tui.app; exec bash"

sleep 1
if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "Failed to create tmux session. Check core/logs/tui_tmux.log" >&2
  exit 1
fi

chmod +x scripts/attach_dashboard_tmux.sh 2>/dev/null || true
echo "Session ready: ./scripts/attach_dashboard_tmux.sh"
echo "  (or: env -u TMUX tmux attach -t $SESSION)"
