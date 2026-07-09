# xAuby Security Audit For SaaS Readiness

Date: 2026-07-09

## Scope

This audit covered the local xAuby repository and non-destructive attack-style
tests against code paths that matter if xAuby is exposed beyond a single
operator VPS:

- WebUI HTTP server, static file serving, and JSON API payloads.
- Runtime/config path isolation for multi-instance operation.
- Manual order local IPC.
- Secret leakage through tracked files and git history.

No third-party systems, exchanges, or public targets were attacked.

## Fixed In This Pass

- WebUI remote exposure now fails closed unless auth is configured. Binding to a
  non-loopback host requires `XAUBY_WEBUI_PASSWORD` or `XAUBY_WEBUI_TOKEN`, unless
  explicitly overridden with `XAUBY_WEBUI_ALLOW_UNAUTH_REMOTE=1`.
- WebUI supports browser-friendly Basic Auth and bearer token auth.
- WebUI responses now include baseline security headers:
  `Content-Security-Policy`, `X-Content-Type-Options`, `X-Frame-Options`, and
  `Referrer-Policy`.
- WebUI JSON responses recursively redact secret-like keys before sending them
  to the browser.
- `cli_ui.webui_avatar` now rejects external/unsafe URL schemes and path
  traversal.
- `XAUBY_INSTANCE_ID` is validated as a tenant-safe slug to prevent runtime path
  escape.
- A dependency-free secret scanner was added and wired to tests/CI:
  `scripts/scan_secrets.py`.
- `.gitignore` now excludes env variants, local backups, and additional runtime
  cache/state artifacts.

## Attack Tests Added

- Unauthenticated WebUI request returns `401` when auth is configured.
- Authenticated WebUI request succeeds with valid Basic Auth.
- WebUI refuses `0.0.0.0` bind without auth.
- WebUI allows remote bind when auth is configured.
- WebUI state payload redacts secret-like keys.
- Static server path traversal remains blocked.
- Unsafe configured avatar URL falls back to bundled asset.
- `XAUBY_INSTANCE_ID=../...` is rejected.
- Tracked-file secret scan fails on realistic credential patterns and passes on
  env-var names/placeholders.

## SaaS Residual Risks

- xAuby is still a single-operator bot runtime, not a complete SaaS security
  boundary. Real SaaS needs tenant identity, tenant authorization, RBAC, and
  per-tenant audit logs outside the bot process.
- Do not expose the stdlib WebUI directly to the public internet. Put it behind
  TLS, a reverse proxy, and an identity-aware access layer.
- Manual trading actions must remain local-only or be redesigned with explicit
  authorization, CSRF protection, replay protection, and immutable audit logs
  before any web write endpoints are added.
- Per-tenant exchange credentials should move to a secret manager; local `.env`
  is acceptable for a VPS operator but weak for SaaS.
- Use one OS user/process namespace per tenant, separate `XAUBY_HOME` and
  `XAUBY_CONFIG_DIR`, restrictive file permissions, and resource quotas.
- Add dependency/SBOM scanning and scheduled CVE review. The current scanner only
  covers repository secret leakage.
- Strategy/plugin execution still relies on static checks and convention. SaaS
  plugin execution should use process/container isolation.

## Minimum SaaS Deployment Baseline

- Set `XAUBY_WEBUI_PASSWORD` or `XAUBY_WEBUI_TOKEN` for every non-loopback WebUI.
- Keep `XAUBY_WEBUI_ALLOW_UNAUTH_REMOTE` unset in production.
- Terminate TLS at a reverse proxy and require a real identity provider before
  the WebUI.
- Run the engine as a non-root user.
- Store each tenant under isolated `XAUBY_HOME` and `XAUBY_CONFIG_DIR`.
- Keep tenant config/env files mode `0600` and directories mode `0700`.
- Rotate exchange/API credentials after any suspected exposure.
- Run `python scripts/scan_secrets.py --tracked --history` before release.
