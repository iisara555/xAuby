# Security policy

xAuby places real orders on a live exchange account. A defect in the control
plane or the engine can lose money, so security reports are treated as
operational incidents rather than as ordinary bugs.

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Use GitHub's private vulnerability reporting: **Security → Report a
vulnerability** on this repository. That channel is private to the maintainer
and lets a fix ship before the details are public.

Useful in a report, roughly in order of value:

- what an attacker gains — unauthorised orders, credential disclosure, denial
  of service, or something else
- the smallest reproduction you have, ideally against a local `--simulate`
  instance rather than a live account
- affected commit or release
- whether the finding requires an authenticated session, a Trade PIN, or
  neither

Expect an acknowledgement within a few days. This is a single-maintainer
project, not a funded programme; there is no bounty, and the honest answer on
timelines is that a fix ships when it is ready and correct.

## Scope

In scope, and most likely to matter:

- `xauby/saas` — the multi-tenant control plane. It is the only component that
  faces the internet: authentication, session and CSRF handling, TOTP, the
  Trade PIN, exchange-credential storage, and the manual-order path.
- `Website/lib/server/api-proxy.ts` — the proxy in front of that control plane.
  It is security-relevant because the upstream trusts the forwarding headers it
  stamps.
- `xauby/engine`, `xauby/api` — order placement, risk guards, and exchange
  communication.
- `scripts/` deployment and credential helpers, and the `deploy/` systemd
  units.

Out of scope:

- The self-hosted runner used for backtests, and any host-level configuration
  of it.
- Anything requiring physical or shell access to an operator's machine.
- Findings that depend on running the engine with `LIVE_TRADING=true` against
  credentials that are not yours.
- Reports produced solely by an automated scanner with no demonstrated impact.
- Trading losses. A strategy performing badly is not a vulnerability; every
  certificate in this repository states its own uncertainty.

## Please do not

- Test against the production deployment. Run your own instance —
  `python run_xauby.py --simulate` never places a real order.
- Use credentials, API keys, or accounts that are not yours.
- Run denial-of-service or load tests against any hosted endpoint.

## What is already known

Two dependency findings are documented rather than hidden, with the reasoning
in `.github/workflows/security.yml`: a `setuptools` advisory pinned in place by
`pandas-ta`'s own version cap, and `sharp` CVEs inherited through Next.js where
the only offered remediation is a semver-major downgrade. Reports restating
either without new impact will be closed with a pointer to that file.

Secrets never live in this repository. `bot_config.yaml` and
`coin_whitelist.json` are committed and key-free; credentials come from `.env`
and, in production, from `/etc/xauby/tenants/<tenant>/`, which is outside the
checkout entirely.
