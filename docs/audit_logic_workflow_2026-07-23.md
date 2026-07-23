# Senior logic/workflow audit — xAuby

Date: 2026-07-23. Scope: signal → risk → sizing → execution → reconciliation →
state/API → Pilot Workspace, plus authentication and production deployment.
The review deliberately avoids redesigning working components. Production
inspection was read-only; this is a logic/safety audit, not a claim that a
strategy will be profitable.

## Executive result

No evidence of an uncontrolled duplicate-entry or wrong-side path was found in
the reviewed critical flow. Existing guards cover stale candles, daily loss,
drawdown, position count, close-before-reverse, residual exchange positions,
idempotent manual-order challenges, and strategy ownership during handoff.

Two user-facing reliability issues were fixed in this change:

1. **First email login could bounce back to login.** The login request set a
   valid cookie, but client-side routing could reuse an SWR 401 cached before
   login. Login now proves the cookie by calling `/api/v1/me`, then performs a
   full navigation; `AppShell` no longer redirects when valid user data is
   already present.
2. **Vercel deployment depended on the current directory and remembered local
   state.** `scripts/deploy_vercel.sh` now resolves the repository path itself,
   validates branch/commit/project/CLI, uploads a prebuilt artifact without
   moving the alias, smoke-tests it, and only then promotes it.
3. **Vercel silently blocked commits made by a non-member Git identity.** The
   deploy command now validates the HEAD author email before spending time on
   a build; the repository identity on this VPS is set to the authorized owner.

## Workflow and worst-case matrix

| Scenario | Required safe outcome | Evidence / status |
|---|---|---|
| Exchange candles or ticker become stale | No new entry from stale evidence; surface delayed state | `tests/test_candle_staleness.py`, REST ticker fallback tests — covered |
| CDC/SuperTrend flips while holding opposite side | Close first; open the reverse side only after confirmed close and balance settlement | `tests/test_cdc_long_short.py`, `tests/test_short_dispatch.py`, `tests/test_order_allocation_guard.py`, `tests/test_entry_market_fallback.py` — covered |
| Close succeeds but OKX balance cache still shows the pre-close balance | Wait for a direct balance refresh; on timeout remain flat and retry later, never send a reduced reverse order | reverse settlement tests — covered by commit `7d0b04a` |
| Close fails or an exchange residual position remains | Do not reverse; preserve/restore protection and alert | short dispatch and partial-TP live tests — covered |
| Two pairs signal together | Enforce the shared open-position limit and per-pair allocation | startup consistency guard in `tests/test_open_positions_guard.py`; allocation tests — covered |
| Equity/daily-loss/drawdown inputs are missing or stale | Block risk-increasing action or use explicitly scoped persisted state | drawdown and critical-path hardening tests — covered; production monitoring still required |
| Strategy changes while a position is open | The entry strategy retains exit ownership until flat | strategy handoff contract in `docs/trading-flow.md` and regression tests — covered |
| Manual trade is submitted twice | Expiring digest/challenge plus idempotency key prevents duplicate execution | SaaS/manual-order tests — covered |
| Login cookie is accepted but UI still has a cached pre-login 401 | Verify `/api/v1/me`; clear client cache through a full navigation | fixed and regression-tested in this change |
| Build, upload or smoke test fails during Vercel deploy | Production alias remains on the previous deployment | new deploy-script tests — covered |

## Findings resolved in the follow-up

### P1 — VPS checkout permissions can stop an engine after deployment — RESOLVED

A prior `git pull` under a restrictive umask left tracked Python files at mode
`0600`; the tenant engine then raised `PermissionError` importing
`xauby/observability/events.py`. The live file is currently normalized to
`0644`. `scripts/deploy_from_github.sh` now sets a code-safe `0022` checkout
umask, normalizes only files/directories represented by the Git index, verifies
service-user read/execute bits, and refuses restart if the preflight fails.
This is intentionally scoped to tracked source paths rather than a recursive
chmod over runtime/config/credential data.

### P1 — VPS deploy helper persists a GitHub token in the remote URL — RESOLVED

When `GITHUB_TOKEN` is present, `scripts/deploy_from_github.sh` writes it into
the repository's persistent `origin` URL. That credential can then appear in
configuration backups or diagnostic output. The helper now always normalizes
`origin` to a credential-free URL and supplies a token only to a mode-0700,
short-lived `GIT_ASKPASS` process. It also reads `.env` as literal data instead
of sourcing it. The current `origin` contains no embedded credentials.

### P1 — the documented public sslip HTTPS health endpoint is not listening — RESOLVED

At audit time both systemd services were `active`, and the engine continuously
evaluated BTC and XAU signals, but
`https://188.166.253.203.sslip.io/healthz` refused port 443. The committed
`deploy/Caddyfile` declares only `http://188.166.253.203.sslip.io`; that hostname
is now explicitly a compatibility redirect, not a TLS origin. The authoritative
API origin is `https://xauby-vps.tailfcdd3a.ts.net` in Vercel and documentation.
A hardened systemd timer checks both that origin and `https://xauby.vercel.app`
every five minutes through `scripts/check_production_health.sh`.

### P2 — repository-wide test/config drift — RESOLVED

The audit baseline `unittest discover` run had 1,035 passes and two failures:
tests expected an XAU-only whitelist and CDC `fresh_zone_window=1`, while the
deployed configuration intentionally includes BTC and uses window 3. The full
pytest run had 1,093 passes and five failures: those same two plus incomplete
quick-config fixtures for the deployed strategy/regime and the secret scanner
flagging a tracked test passphrase fixture. These are not current live-order
failures, but they made a red suite ambiguous. The whitelist/CDC expectations,
strict quick-config fixture, current strategy identifier, and test-secret
placeholder classification were corrected. GitHub Actions now runs the full
pytest suite and the production frontend build in addition to tracked/history
secret scanning.

Follow-up verification completed with 1,103 pytest tests passing and no
failures; the admin read-policy regression added immediately afterward also
passed its targeted run. The Next.js production build and TypeScript check
completed successfully.

### P2 — displayed settings still flatten per-pair sizing — RESOLVED

The Settings summary exposes the focus pair's
`max_position_per_trade_pct` as one portfolio number even though enforcement is
per pair. This is display drift, not a sizing bypass, but it can mislead an
operator comparing BTC and XAU. The compiled profile now exposes
`pair_position_caps` beside `pair_allocations`, and Settings renders one sizing
cell per selected symbol with allocation and maximum-per-trade percentages.

### P3 — duplicated admin authorization logic — RESOLVED

Two admin read endpoints repeat role/TOTP checks instead of using the shared
`admin_user` dependency. Both read and write dependencies now call one
`require_admin` policy; reads retain correct GET semantics without requiring a
CSRF header, while writes still pass through CSRF and origin validation.

## Production evidence sampled

- `xauby-control.service`: active.
- `xauby-engine@owner-itsara.service`: active.
- Engine logs showed BTC `supertrend_ema200` waiting for a fresh flip while
  idle, and XAU holding its active position; no repeated order submissions or
  crash loop appeared in the sampled window.
- Control-plane logs showed a successful login POST followed immediately by a
  successful `/api/v1/me`, and a later duplicate login POST. This isolates the
  first-login symptom to frontend navigation/cache handling rather than cookie
  issuance.

## Release gates

Before any live-engine change: targeted strategy/reversal/risk tests, complete
suite with known failures accounted for, startup config guard, read-only OKX
position/order reconciliation, then controlled systemd restart and log watch.

Before a website release: clean committed `origin/main`, pinned CLI, production
env pull, prebuilt build, immutable-deployment smoke test, promotion, production
alias smoke test. `scripts/deploy_vercel.sh` now encodes that path.
