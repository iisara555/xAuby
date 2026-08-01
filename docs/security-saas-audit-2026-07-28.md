# SaaS control plane — security audit, 2026-07-28

Roadmap P2.4. Supersedes `security-saas-audit.md` (2026-07-09), which audited
`xauby/webui/server.py`. **That component has been deleted.** Every finding and
every line of its "Minimum SaaS Deployment Baseline" describes a stdlib WebUI,
`XAUBY_WEBUI_PASSWORD`, and `XAUBY_WEBUI_ALLOW_UNAUTH_REMOTE` — none of which
exist. Its *residual risk* list is still the right checklist and is carried
forward below, marked with what has since changed.

**Scope of this pass:** `xauby/saas/` (54 HTTP routes), `deploy/` (systemd units,
sudoers, installer) and the credential path from browser to engine process. Not
in scope: the trading engine's own risk logic, which Phase 0/1 covered.

**Method:** reading the current code, not the previous report. Where a claim is
about production behaviour I say whether I verified it or only read it.

---

## What is actually in place

Recorded because a residual-risk list read alone gives an unfairly bleak
picture, and because knowing what is solid tells you where to spend.

| Area | State |
|---|---|
| Credential storage | AES-256-GCM envelope, AAD bound to `(tenant_id, target_id)` — an envelope cannot be replayed into another tenant or another exchange |
| Credential exposure | Plaintext only under `/run/xauby/credentials/<tenant>.env`, mode 0600, on tmpfs; cleared on `stop`, and now also when a tenant disconnects (P2.1) |
| Session | httpOnly, SameSite=Lax, `Secure` from `cookie_secure`, 7-day max-age, server-side revocation |
| CSRF | Per-session token required on every state-changing route, plus an `Origin` check against `public_base_url` |
| Admin authz | `admin_read_user` / `admin_user` both funnel through `require_admin`, which requires `platform_admin` **and** verified TOTP |
| Live activation | Fresh exchange test (30 min) + TOTP + Trade PIN, and a preset that is `live_certified` |
| Rate limiting | Login, email, Trade-PIN reset and Telegram test all throttled with lockout |
| Privilege boundary | Control plane runs as `xauby-control`; service control is a fixed sudoers entry to one wrapper script, not general `systemctl` |
| Secret leakage | `scan_secrets.py` on tracked files **and history**, in CI |
| Dependency CVEs | `pip-audit` + `npm audit` on PRs and weekly, Dependabot on pip/npm/actions (P2.3) |

The earlier report's finding that admin read endpoints used inline authorization
while writes used a shared dependency (F-5) **is fixed**: both go through
`require_admin`.

---

## Findings from this pass

### 1. Withdrawal permission was asserted, never checked — FIXED in this pass

`/api/v1/exchange/test` set `capabilities["withdraw_disabled_attested"] = True`
unconditionally after any successful probe, and stored it inside the
`capabilities` dict beside properties the probe had actually measured. The value
originated from a checkbox the user ticked at onboarding. The probe was honest —
it emitted `withdraw_permission_checked: False` — and the API layer overwrote
that.

Now `xauby/saas/withdraw_check.py` asks the venue: OKX
`GET /api/v5/account/config` (`perm`), Binance
`GET /sapi/v1/account/apiRestrictions` (`enableWithdrawals`). The result is
tri-state and **every failure path yields unknown, never a pass** — unsupported
venue, network error, a key not permitted to read its own restrictions, an
unrecognised response shape. The workspace shows the venue's answer, and says
"not verified" when there is none.

**Not yet verified against a live exchange account.** The logic and its failure
modes are covered by 17 tests with fake venue responses; the first real
`Test connection` after deploy is what confirms the endpoint shapes.

### 2. `/auth/dev-login` set its session cookie inline — FIXED in this pass

Every other login path used `set_session_cookie`; this one duplicated it and
carried neither `secure` nor `path`, while also creating the session with
`mfa_verified=True`. It is gated behind `dev_login_enabled` (default `False`,
404 otherwise), so this was not exploitable in production — but a second, weaker
copy of a security-relevant helper sitting behind one flag is the shape of a
future incident. It now calls the shared helper.

### 3. Credential-key rotation is now implemented — CONFIGURATION REQUIRED (P2.2)

`key_version` now controls a real keyring: new writes use the active version,
retained prior versions decrypt old rows and backups, and
`scripts/rotate_credential_master_key.py` stages a new keyring before it
re-encrypts all exchange and Telegram blobs in one transaction. The prior key
must remain until the off-site retention window has expired and a restore drill
has passed. Rotation remains an operator-controlled action requiring stopped
services; it is not an API endpoint.

### 4. Off-site encrypted recovery is implemented — CONFIGURATION REQUIRED (P2.2)

The backup unit now supports an AES-256-GCM encrypted rclone archive plus a
separate recovery bundle encrypted to an offline GPG public key. The VPS never
receives the matching GPG private key. It remains unconfigured until the
operator supplies a dedicated rclone remote, backup key, and public-key
recipient in `/etc/xauby/backup.env`; without every component the service fails
closed rather than uploading a partial or plaintext backup. See
`docs/offsite_backup_runbook.md`.

### 5. One direct dependency cannot be audited — ACCEPTED RISK (P2.3)

`pandas-ta` is installed from a git fork (`MerlinR/Pandas-ta-fork`), so
`pip-audit` skips it entirely: a direct dependency that no scanner covers. It
also declares `setuptools (<=80)`, which pins a version with a known advisory
(PYSEC-2026-3447, fixed in 83.0.0) and prevents raising the floor.
The exception is named in the blocking audit rather than suppressing the job.
The decision and triggers for removing it are recorded in
`docs/threat-model.md`; weekly audits and dependency PRs keep it visible.

### 6. Two npm advisories have no forward fix — ACCEPTED RISK (P2.3)

`sharp < 0.35.0` inherits four libvips CVEs. The vulnerable range covers every
Next 16.x release and npm's only remediation is `next@14.2.35`, a semver-major
downgrade. Partial mitigation: the single `next/image` usage passes
`unoptimized`, so it does not route through the optimizer that reaches libvips —
the complete tracked Website source has now been scanned, and
`scripts/check_image_optimizer_policy.py` runs in frontend CI to reject any new
`next/image` import, direct `/_next/image` call, or removal of `unoptimized`
without an explicit security review. The audit remains gated at `critical` so
new advisories block, while the known highs stay visible in the full report.

### 7. CodeQL and SBOM — IMPLEMENTED (P2.3)

GitHub CodeQL Default Setup analyzes Actions, JavaScript/TypeScript, and Python
on PRs. `.github/workflows/security.yml` also runs a commit-SHA-pinned
Anchore/Syft generator on dependency changes and weekly, retaining an SPDX JSON
SBOM artifact for 30 days. CI tests lock the generator, format, retention, and
immutable action reference in place.

---

## Residual risks carried forward

From the 2026-07-09 list, updated against the current architecture.

| Risk | Then | Now |
|---|---|---|
| Tenant identity, authz, per-tenant audit logs | absent | **present** — sessions, roles, `store.audit`, tenant-scoped queries |
| Do not expose the WebUI directly | applied to the stdlib WebUI | **obsolete** — that server is deleted; Tailscale Funnel is the trusted production TLS origin and Caddy only redirects the HTTP compatibility hostname |
| Manual trading needs authz/CSRF/replay protection/audit | open | **present** — Trade PIN, TOTP, per-request challenge with a digest, idempotency key |
| Credentials should move to a secret manager | open | **partially** — envelope encryption with a host-held master key is better than `.env`, and is still not a managed secret store (finding 4) |
| One OS user/namespace per tenant | open | **partially** — separate `XAUBY_HOME` / `XAUBY_CONFIG_DIR` per tenant, `xauby-engines` group, systemd hardening; not separate OS users, no resource quotas |
| Dependency/SBOM scanning and scheduled CVE review | open | **present** — weekly audits, Dependabot, CodeQL Default Setup and retained SPDX SBOM (P2.3) |
| Plugin execution isolation | open | **open** — still static checks and convention. The maturity gate added in P1.4 stops an uncertified plugin reaching a live pair, which narrows the blast radius but is not isolation |

---

## Deployment baseline for the current architecture

Replaces the WebUI-era baseline. Every item refers to something that exists.

- Terminate production TLS at the trusted Tailscale Funnel origin; keep Caddy's
  IP-derived hostname as an HTTP-only redirect; set
  `XAUBY_SAAS_COOKIE_SECURE=true` in production.
- `XAUBY_SAAS_DEV_LOGIN` must be unset or false. It bypasses MFA by design.
- `XAUBY_SAAS_SESSION_SECRET` set and at least 32 bytes; the settings loader
  refuses to start without it outside dev-login mode.
- `/etc/xauby/control.env` mode 0640, owner `root:xauby-control`.
- `/etc/xauby/deadman.env` mode 0640, owner `root:xauby-control` — read by
  systemd as PID 1 before it drops privileges, so it does not need to be
  readable by the service user.
- Tenant config directories mode 0700 under `/etc/xauby/tenants/<tenant>/`,
  owned per tenant.
- Control plane runs as `xauby-control`; engines under the `xauby-engines`
  group. Neither runs as root.
- `sudoers.d/xauby-control` grants exactly the wrapper scripts, nothing broader.
- Run `python scripts/scan_secrets.py --tracked --history` before release — in
  CI on every push and PR.
- Configure the encrypted off-site remote and prove a restore drill (finding 4);
  the code fails closed until the remote, backup key and GPG recipient exist.
- Rotate the credential master key with
  `scripts/rotate_credential_master_key.py`, retain prior keys through the
  backup-retention window, and validate both current rows and retained backups.

---

## What this audit does not cover

Stated so the gaps are not mistaken for clean results:

- **No penetration testing.** This is a code read, not an attack.
- **No live-host verification.** Findings about file modes and unit behaviour
  come from reading `deploy/`, not from inspecting the running VPS.
- **The withdrawal check has never run against a real exchange key.**
- **Threat model maintained separately.** `docs/threat-model.md` enumerates the
  trust boundaries and risks, but its production assumptions have not been
  validated by a live-host inspection or penetration test.
