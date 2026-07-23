#!/usr/bin/env bash
# Deterministic production deployment for the Next.js workspace.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_ROOT="$ROOT/Website"
PROJECT_NAME="xauby"
PROJECT_ID="prj_m653cHTPdEVORx8WNAYsRRPvKmKZ"
SCOPE="itsara-kaewruangs-projects"
ORG_ID="team_bVwJhoMi3IPSnRxtu3fageUS"
PRODUCTION_URL="https://xauby.vercel.app"
PINNED_VERCEL_VERSION="56.2.1"
AUTHORIZED_GIT_EMAIL="${VERCEL_GIT_AUTHOR_EMAIL:-iisara555@gmail.com}"
VERCEL_BIN="${VERCEL_CLI_BIN:-vercel}"
CURL="${CURL_BIN:-curl}"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  echo "Usage: ./scripts/deploy_vercel.sh"
  echo "Deploys the committed origin/main Website build to xauby.vercel.app."
  exit 0
fi
if [[ $# -ne 0 ]]; then
  echo "[ERR] This command takes no arguments. Use --help for usage." >&2
  exit 2
fi

fail() {
  echo "[ERR] $*" >&2
  exit 1
}

vc() {
  local args=("$@" --scope "$SCOPE")
  if [[ -n "${VERCEL_TOKEN:-}" ]]; then
    args+=(--token "$VERCEL_TOKEN")
  fi
  "$VERCEL_BIN" "${args[@]}"
}

# Vercel CLI 56 forwards global flags such as --scope to the underlying curl
# binary. The project link was verified above, and VERCEL_TOKEN is inherited
# from the environment when present, so protected smoke requests intentionally
# avoid vc() and pass curl flags only after the separator.
vc_protected_curl() {
  (
    cd "$WEB_ROOT"
    "$VERCEL_BIN" curl "$@" -- --silent --show-error --fail
  )
}

[[ -d "$WEB_ROOT" && -f "$WEB_ROOT/package.json" && -f "$WEB_ROOT/package-lock.json" ]] \
  || fail "Website project files are missing under $WEB_ROOT"
command -v git >/dev/null || fail "git is required"
command -v python3 >/dev/null || fail "python3 is required"
command -v "$VERCEL_BIN" >/dev/null || fail "Vercel CLI is missing; install vercel@$PINNED_VERCEL_VERSION"
command -v "$CURL" >/dev/null || fail "curl is required"

actual_version="$($VERCEL_BIN --version 2>&1 | sed -nE 's/.* ([0-9]+\.[0-9]+\.[0-9]+).*/\1/p' | tail -1)"
[[ "$actual_version" == "$PINNED_VERCEL_VERSION" ]] \
  || fail "Vercel CLI must be $PINNED_VERCEL_VERSION (found ${actual_version:-unknown})"

cd "$ROOT"
[[ "$(git branch --show-current)" == "main" ]] || fail "production deploy requires branch main"
[[ -z "$(git status --porcelain -- Website)" ]] \
  || fail "Website has uncommitted changes; commit and push them before deploying"

echo "[1/7] Fetching origin/main and verifying the deploy commit..."
git fetch --quiet origin main
local_sha="$(git rev-parse HEAD)"
remote_sha="$(git rev-parse origin/main)"
[[ "$local_sha" == "$remote_sha" ]] \
  || fail "HEAD does not match origin/main; push or pull before deploying"
commit_email="$(git show -s --format='%ae' HEAD)"
[[ "$commit_email" == "$AUTHORIZED_GIT_EMAIL" ]] \
  || fail "HEAD author $commit_email is not the Vercel-authorized identity $AUTHORIZED_GIT_EMAIL; create the commit with the authorized Git author"

echo "[2/7] Verifying Vercel authentication and project link..."
vc whoami >/dev/null || fail "Vercel authentication failed; run 'vercel login' or export VERCEL_TOKEN"
link_ok=false
if [[ -f "$WEB_ROOT/.vercel/project.json" ]]; then
  read -r linked_project linked_org < <(
    python3 - "$WEB_ROOT/.vercel/project.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    value = json.load(handle)
print(value.get("projectId", ""), value.get("orgId", ""))
PY
  )
  if [[ "$linked_project" == "$PROJECT_ID" && "$linked_org" == "$ORG_ID" ]]; then
    link_ok=true
  fi
fi
if [[ "$link_ok" != true ]]; then
  vc link --yes --cwd "$WEB_ROOT" --project "$PROJECT_NAME" >/dev/null
fi

echo "[3/7] Pulling production environment..."
vc pull --yes --environment=production --cwd "$WEB_ROOT" >/dev/null

echo "[4/7] Building the production artifact..."
vc build --prod --cwd "$WEB_ROOT"

rollback_id="$(vc inspect xauby.vercel.app --format=json 2>/dev/null \
  | sed -n '/^{/,$p' | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id", "unknown"))' \
  || true)"

echo "[5/7] Uploading without changing the production alias..."
deployment_url="$(vc deploy --prebuilt --prod --skip-domain --yes --cwd "$WEB_ROOT" \
  --meta "gitCommitSha=$local_sha" \
  | tr '\r' '\n' \
  | grep -Eo 'https://[^[:space:]]+\.vercel\.app' \
  | head -1)"
[[ "$deployment_url" == https://* ]] || fail "Vercel did not return a deployment URL"
vc inspect "$deployment_url" --wait --timeout 3m >/dev/null

smoke() {
  local base="$1"
  "$CURL" -fsS "$base/" >/dev/null
  "$CURL" -fsS "$base/login" >/dev/null
  "$CURL" -fsS "$base/app" >/dev/null
  local health
  health="$($CURL -fsS "$base/healthz")"
  [[ "$health" == *'"ok":true'* || "$health" == *'"ok": true'* ]] \
    || fail "health check did not return ok=true for $base"
}

smoke_protected_deployment() {
  local deployment="$1"
  vc_protected_curl / --deployment "$deployment" --yes >/dev/null
  vc_protected_curl /login --deployment "$deployment" --yes >/dev/null
  vc_protected_curl /app --deployment "$deployment" --yes >/dev/null
  local health
  health="$(vc_protected_curl /healthz --deployment "$deployment" --yes)"
  [[ "$health" == *'"ok":true'* || "$health" == *'"ok": true'* ]] \
    || fail "health check did not return ok=true for $deployment"
}

echo "[6/7] Smoke-testing the immutable deployment..."
smoke_protected_deployment "$deployment_url"

echo "[7/7] Promoting the verified artifact and checking the production alias..."
vc promote "$deployment_url" --yes >/dev/null
smoke "$PRODUCTION_URL"

short_sha="${local_sha:0:7}"
echo ""
echo "Deploy Result"
echo "  URL:       $deployment_url"
echo "  Alias:     $PRODUCTION_URL"
echo "  Status:    READY"
echo "  Commit:    $short_sha"
echo "  Project:   $PROJECT_NAME ($PROJECT_ID)"
echo "  Rollback:  ${rollback_id:-unknown}"
