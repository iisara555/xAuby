#!/usr/bin/env bash
# Public end-to-end health probe for the authoritative frontend and API origins.
set -euo pipefail

PUBLIC_URL="${XAUBY_PUBLIC_URL:-https://xauby.vercel.app}"
API_URL="${XAUBY_API_ORIGIN:-https://xauby-vps.tailfcdd3a.ts.net}"
CURL="${CURL_BIN:-curl}"

check_health() {
  local label="$1"
  local base="$2"
  local response
  if ! response="$($CURL -fsS --connect-timeout 5 --max-time 15 "$base/healthz" 2>&1)"; then
    echo "[ERR] $label health request failed: $base/healthz ($response)" >&2
    return 1
  fi
  if [[ "$response" != *'"ok":true'* && "$response" != *'"ok": true'* ]]; then
    echo "[ERR] $label health did not return ok=true: $base/healthz" >&2
    return 1
  fi
  echo "[OK] $label $base/healthz"
}

failures=0
check_health "frontend" "$PUBLIC_URL" || failures=$((failures + 1))
check_health "control-plane" "$API_URL" || failures=$((failures + 1))
exit "$failures"
