# xAuby Security Audit For SaaS Readiness

> **SUPERSEDED 2026-07-28 — see
> [`security-saas-audit-2026-07-28.md`](security-saas-audit-2026-07-28.md).**
>
> This pass audited `xauby/webui/server.py`, **which has been deleted**. Its
> findings, its attack tests and every line of its "Minimum SaaS Deployment
> Baseline" describe a stdlib WebUI, `XAUBY_WEBUI_PASSWORD` and
> `XAUBY_WEBUI_ALLOW_UNAUTH_REMOTE` — none of which exist any more, so none of
> it is actionable as written. It is kept for history, not for guidance.
>
> The **residual risk** list below is the exception: it was the right checklist
> and is carried forward, item by item with current status, into the new audit.

Date: 2026-07-09 (updated 2026-07-10: branded sign-in / session cookies)

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

## Added 2026-07-10: Branded Sign-In / Session Cookies

- Browsers now get a branded `/login` page instead of the native Basic Auth
  dialog. Sessions are HMAC-SHA256-signed cookies (`xauby_session`,
  `HttpOnly; SameSite=Lax`, 7-day expiry) verified with
  `hmac.compare_digest`; password comparison on `POST /login` is also
  constant-time, with a 0.3s delay on failure as a brute-force damper.
- The signing secret is random per process (sessions die on restart) unless
  `XAUBY_WEBUI_SESSION_SECRET` is set. It is deliberately NOT derived from the
  password, so a leaked cookie cannot be brute-forced offline into the
  password.
- The pre-auth surface is an exact allowlist: `/login`, `/logout`,
  `/login.js`, `/style.css`, `/xau-logo.svg`. Everything else — including
  `/app.js`, the operator avatar photo, and all `/api/*` — stays behind auth,
  so no personal or behavioral data leaks before sign-in. Allowlisted files
  are still served through the traversal-protected static handler.
- `/api/*` keeps the previous `401` + `WWW-Authenticate: Basic` contract;
  Basic Auth and Bearer tokens keep working unchanged for programmatic and
  tunnel clients. Only browser-facing paths redirect (302) to `/login`.
- Cookies omit `Secure` by default (the supported deployments are plain-HTTP
  loopback/Tailscale); `XAUBY_WEBUI_COOKIE_SECURE=1` opts in behind TLS.
- CSP was extended with `font-src 'self' https://fonts.gstatic.com` and
  `style-src ... https://fonts.googleapis.com` so the UI webfont loads; no
  other origins were opened, `script-src` remains `'self'`.

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
- Login: correct password sets a signed HttpOnly/Lax cookie that authorizes
  both pages and `/api/*`; wrong password redirects with no cookie.
- Tampered/expired/garbage session cookies are rejected (unit tests on
  `_sign_session`/`_verify_session` plus HTTP-level test).
- Unauthenticated HTML requests 302 to `/login` while `/api/*` still returns
  `401 WWW-Authenticate` — asserted in the same test.
- The pre-auth allowlist is exact: `/style.css`, `/xau-logo.svg`, `/login.js`
  serve unauthenticated; `/app.js`, `/avatar-default.svg`, `/index.html`
  redirect.
- `POST` to any path other than `/login` returns 404.
- No-password loopback mode keeps zero friction (`/` serves, `/login` bounces
  to `/`).
- `XAUBY_WEBUI_COOKIE_SECURE=1` adds the `Secure` cookie attribute.

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
