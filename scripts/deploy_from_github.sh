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

# Load GITHUB_TOKEN if available
if [[ -z "${GITHUB_TOKEN:-}" ]] && [[ -f "$ROOT/.env" ]] && grep -q '^GITHUB_TOKEN=' "$ROOT/.env"; then
  set -a
  source <(grep '^GITHUB_TOKEN=' "$ROOT/.env")
  set +a
fi

# Set remote URL with token if available
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  REMOTE_URL="https://${GITHUB_TOKEN}@github.com/${GITHUB_REPO}.git"
  git remote set-url origin "$REMOTE_URL" 2>/dev/null || true
else
  REMOTE_URL="https://github.com/${GITHUB_REPO}.git"
  git remote set-url origin "$REMOTE_URL" 2>/dev/null || true
fi

echo "[1/5] Checking for local changes..."
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "[WARN] You have uncommitted changes. Stashing them..."
  git stash push -m "deploy-backup-$(date +%Y%m%d-%H%M%S)"
  STASHED=true
else
  STASHED=false
fi

echo "[2/5] Fetching latest from origin/$BRANCH..."
MAX_RETRIES=4
for attempt in $(seq 1 $MAX_RETRIES); do
  if git fetch origin "$BRANCH"; then
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

echo "[3/5] Merging origin/$BRANCH..."
git merge "origin/$BRANCH" --ff-only || {
  echo "[ERR] Fast-forward merge failed. Manual intervention needed."
  echo "      Run: git merge origin/$BRANCH"
  exit 1
}

echo "[4/5] Checking Python syntax of changed files..."
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

echo "[5/5] Deploy complete."
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
