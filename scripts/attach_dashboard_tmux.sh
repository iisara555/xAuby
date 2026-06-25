#!/usr/bin/env bash
# Attach to the dashboard tmux session (safe from inside another tmux).
set -euo pipefail
SESSION="${XAUBY_TMUX_SESSION:-dashboard}"
if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "No session '$SESSION'. Start with: ./scripts/start_dashboard_tmux.sh" >&2
  exit 1
fi
exec env -u TMUX tmux attach -t "$SESSION"
