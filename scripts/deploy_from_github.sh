#!/usr/bin/env bash
# Deploy latest code from GitHub to this VPS and optionally restart the engine.
#
# Usage (on VPS):
#   ./scripts/deploy_from_github.sh              # pull only
#   ./scripts/deploy_from_github.sh --restart     # pull + restart engine
#
# Prerequisites:
#   - git remote 'origin' pointing to GitHub
#   - GITHUB_TOKEN in .env or environment (for private repos)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

RESTART=false
BRANCH="${DEPLOY_BRANCH:-main}"
GITHUB_REPO="${GITHUB_REPO:-iisara555/xAuby}"
for arg in "$@"; do
  case "$arg" in
    --restart) RESTART=true ;;
    --branch=*) BRANCH="${arg#--branch=}" ;;
  esac
done

echo "╔══════════════════════════════════════╗"
echo "║   xAuby Deploy from GitHub           ║"
echo "╚══════════════════════════════════════╝"
echo ""

# Load GITHUB_TOKEN as literal data if available. Never source .env: a token
# value must not be evaluated as shell code.
if [[ -z "${GITHUB_TOKEN:-}" ]] && [[ -f "$ROOT/.env" ]] && grep -q '^GITHUB_TOKEN=' "$ROOT/.env"; then
  GITHUB_TOKEN="$(sed -n 's/^GITHUB_TOKEN=//p' "$ROOT/.env" | tail -1)"
  if [[ "$GITHUB_TOKEN" == \"*\" || "$GITHUB_TOKEN" == \'*\' ]]; then
    GITHUB_TOKEN="${GITHUB_TOKEN:1:${#GITHUB_TOKEN}-2}"
  fi
fi

# Keep the persistent remote credential-free. Authentication is supplied to
# git fetch through a short-lived askpass process, never a URL or command arg.
REMOTE_URL="${GITHUB_REMOTE_URL:-https://github.com/${GITHUB_REPO}.git}"
git remote set-url origin "$REMOTE_URL"
ASKPASS_DIR=""
cleanup() {
  if [[ -n "$ASKPASS_DIR" && -d "$ASKPASS_DIR" ]]; then
    rm -r -- "$ASKPASS_DIR"
  fi
}
trap cleanup EXIT

fetch_branch() {
  if [[ -z "${GITHUB_TOKEN:-}" || "$REMOTE_URL" != https://github.com/* ]]; then
    git fetch origin "$BRANCH"
    return
  fi
  if [[ -z "$ASKPASS_DIR" ]]; then
    ASKPASS_DIR="$(mktemp -d)"
    chmod 700 "$ASKPASS_DIR"
    printf '%s\n' \
      '#!/bin/sh' \
      'case "$1" in' \
      '  *Username*) printf "%s\\n" "x-access-token" ;;' \
      '  *) printf "%s\\n" "$XAUBY_GIT_TOKEN" ;;' \
      'esac' > "$ASKPASS_DIR/askpass"
    chmod 700 "$ASKPASS_DIR/askpass"
  fi
  GIT_ASKPASS="$ASKPASS_DIR/askpass" XAUBY_GIT_TOKEN="$GITHUB_TOKEN" \
    GIT_TERMINAL_PROMPT=0 git -c credential.helper= fetch origin "$BRANCH"
}

echo "[1/6] Checking for local changes..."
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "[WARN] You have uncommitted changes. Stashing them..."
  git stash push -m "deploy-backup-$(date +%Y%m%d-%H%M%S)"
  STASHED=true
else
  STASHED=false
fi

echo "[2/6] Fetching latest from origin/$BRANCH..."
MAX_RETRIES=4
for attempt in $(seq 1 $MAX_RETRIES); do
  if fetch_branch; then
    break
  fi
  if [ "$attempt" -eq "$MAX_RETRIES" ]; then
    echo "[ERR] Failed to fetch after $MAX_RETRIES attempts."
    exit 1
  fi
  WAIT=$((2 ** attempt))
  echo "[WARN] Fetch failed, retrying in ${WAIT}s... (attempt $attempt/$MAX_RETRIES)"
  sleep "$WAIT"
done

# Show what will change
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")
if [ "$LOCAL" = "$REMOTE" ]; then
  echo "[OK] Already up to date ($LOCAL)."
else
  echo ""
  echo "Changes to deploy:"
  git log --oneline "$LOCAL..$REMOTE"
  echo ""
fi

echo "[3/6] Merging origin/$BRANCH..."
# Git creates new checkout paths through the process umask. systemd deliberately
# uses 0077 for runtime secrets, so deployment must establish a code-safe umask.
umask 0022
git merge "origin/$BRANCH" --ff-only || {
  echo "[ERR] Fast-forward merge failed. Manual intervention needed."
  echo "      Run: git merge origin/$BRANCH"
  exit 1
}

echo "[4/6] Normalizing and verifying tracked checkout permissions..."
while IFS= read -r -d '' entry; do
  index_mode="${entry%% *}"
  path="${entry#*$'\t'}"
  [[ -f "$path" && ! -L "$path" ]] || continue
  parent="$(dirname "$path")"
  while [[ "$parent" != "." ]]; do
    chmod go+rx "$parent"
    parent="$(dirname "$parent")"
  done
  if [[ "$index_mode" == "100755" ]]; then
    chmod u+rw,go+r,ugo+x,go-w "$path"
  elif [[ "$index_mode" == "100644" ]]; then
    chmod a-x,u+rw,go+r,go-w "$path"
  fi
done < <(git ls-files -s -z)

PERMISSIONS_OK=true
while IFS= read -r -d '' entry; do
  index_mode="${entry%% *}"
  path="${entry#*$'\t'}"
  [[ -f "$path" && ! -L "$path" ]] || continue
  other_digit="$(stat -c '%a' "$path")"
  other_digit="${other_digit: -1}"
  required=4
  [[ "$index_mode" == "100755" ]] && required=5
  if (( (8#$other_digit & required) != required )); then
    echo "  [ERR] Service users cannot read/execute tracked file: $path" >&2
    PERMISSIONS_OK=false
  fi
done < <(git ls-files -s -z)
[[ "$PERMISSIONS_OK" == true ]] || {
  echo "[ERR] Checkout permissions are unsafe; refusing to restart services." >&2
  exit 1
}
echo "  Tracked files are readable by isolated service users."

echo "[5/6] Checking Python syntax of changed files..."
CHANGED=$(git diff --name-only "$LOCAL..HEAD" -- '*.py' 2>/dev/null || true)
if [[ -n "$CHANGED" ]]; then
  PY="${PYTHON:-./venv/bin/python}"
  if [[ ! -x "$PY" ]]; then
    PY="python3"
  fi
  SYNTAX_OK=true
  while IFS= read -r f; do
    if [[ -f "$f" ]]; then
      if ! "$PY" -m py_compile "$f" 2>/dev/null; then
        echo "  [ERR] Syntax error: $f"
        SYNTAX_OK=false
      fi
    fi
  done <<< "$CHANGED"
  if [ "$SYNTAX_OK" = false ]; then
    echo "[ERR] Syntax errors found in deployed code!"
    exit 1
  fi
  echo "  All changed .py files pass syntax check."
else
  echo "  No Python files changed."
fi

echo "[6/6] Deploy complete."
echo "  Before: ${LOCAL:0:7}"
echo "  After:  $(git rev-parse --short HEAD)"
echo ""

if [ "$STASHED" = true ]; then
  echo "[INFO] You had stashed changes. Run 'git stash pop' to restore them."
fi

if [ "$RESTART" = true ]; then
  echo "[INFO] Restarting engine..."
  if [[ -x "$ROOT/scripts/controlled_restart_engine.sh" ]]; then
    exec "$ROOT/scripts/controlled_restart_engine.sh"
  else
    echo "[WARN] controlled_restart_engine.sh not found. Restart manually."
  fi
else
  echo "[INFO] Code deployed. Restart engine manually when ready:"
  echo "       ./scripts/controlled_restart_engine.sh"
fi
