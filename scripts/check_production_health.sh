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
  response="$($CURL -fsS --connect-timeout 5 --max-time 15 "$base/healthz")"
  if [[ "$response" != *'"ok":true'* && "$response" != *'"ok": true'* ]]; then
    echo "[ERR] $label health did not return ok=true: $base/healthz" >&2
    return 1
  fi
  echo "[OK] $label $base/healthz"
}

check_health "frontend" "$PUBLIC_URL"
check_health "control-plane" "$API_URL"
