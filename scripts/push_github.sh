#!/usr/bin/env bash
# Push local main branch to GitHub using GITHUB_TOKEN (PAT with repo scope) or fallback to origin.
# Usage:
#   export GITHUB_TOKEN=ghp_xxxxxxxx
#   ./scripts/push_github.sh [commit_message]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
GITHUB_REPO="${GITHUB_REPO:-iisara555/xAuby}"

# Check for uncommitted changes (modified, staged, or untracked)
if ! git diff --quiet || ! git diff --cached --quiet || [[ -n "$(git status --porcelain)" ]]; then
  echo "[INFO] Uncommitted changes detected."
  COMMIT_MSG="${1:-}"
  if [[ -z "$COMMIT_MSG" ]]; then
    if [ -t 0 ]; then
      read -rp "Enter commit message: " COMMIT_MSG
    fi
    if [[ -z "$COMMIT_MSG" ]]; then
      echo "[ERR] Uncommitted changes present but no commit message provided."
      echo "      Usage: $0 \"Your commit message\""
      exit 1
    fi
  fi
  echo "[INFO] Staging all changes..."
  git add -A
  echo "[INFO] Committing changes with message: '$COMMIT_MSG'..."
  git commit -m "$COMMIT_MSG"
else
  echo "[INFO] No uncommitted changes to commit."
fi

if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  if [[ -f "$ROOT/.env" ]] && grep -q '^GITHUB_TOKEN=' "$ROOT/.env"; then
    # shellcheck disable=SC1091
    set -a
    source <(grep '^GITHUB_TOKEN=' "$ROOT/.env")
    set +a
  fi
fi

if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  REMOTE="https://${GITHUB_TOKEN}@github.com/${GITHUB_REPO}.git"
  echo "[INFO] Pushing main to GitHub using GITHUB_TOKEN..."
else
  REMOTE="origin"
  echo "[INFO] GITHUB_TOKEN not set. Pushing main to GitHub using remote '${REMOTE}'..."
fi

git push "$REMOTE" main
echo "[OK] Pushed main to GitHub."
