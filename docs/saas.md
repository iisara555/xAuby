# xAuby SaaS Control Plane

xAuby can run as a small hosted service with one isolated engine per tenant. The
first deployment target is one owner plus two customers on a 1 vCPU / 2 GB VPS.
The control plane is the only public application; tenant engines have no public
ports.

## Architecture

| Layer | Responsibility |
|---|---|
| Caddy | Public TLS and reverse proxy |
| `xauby-control` | Google sign-in, tenant authorization, configuration, lifecycle, live approval and manual-order challenges |
| `xauby-engine@<tenant>` | One strategy/execution process per exchange account |
| Control SQLite | Users, tenants, sessions, approvals, config revisions and hash-chained audit events |
| Tenant SQLite | Trading state, candles, events and durable manual commands |

The platform owner has `platform_admin` authority and owns a normal personal
tenant. Admin authority never bypasses engine risk checks or the Trade PIN.

## Install on the pilot VPS

The installer expects Linux with Python 3, `sudo`, systemd, Caddy, `acl` and
`openssl`. Deploy the repository (normally `/opt/xauby/current`) and run:

```bash
sudo ./scripts/install_saas_host.sh
sudo editor /etc/xauby/control.env
sudo -u xauby-control /opt/xauby/current/venv/bin/xauby-admin migrate
sudo -u xauby-control /opt/xauby/current/venv/bin/xauby-admin \
  bootstrap-owner --email owner@example.com --tenant owner-itsara
sudo systemctl enable --now xauby-control xauby-backup.timer
```

Replace `app.example.com` in `deploy/Caddyfile`, install it as the Caddy
configuration, and set the exact Google callback URI:

```text
https://app.example.com/auth/google/callback
```

Only TCP 80/443 should be public. The control plane binds to
`127.0.0.1:8790`.

## Owner workflow

The owner signs in with Google and uses two browser tabs:

- **My Bot** controls the owner's isolated engine and trading account.
- **Admin** displays tenant capacity and approves requested live activation.

The owner consumes one of the three active-engine slots. New public accounts
are created in `queued` state after all slots are occupied.

Normal start/stop happens in the browser. Emergency host commands are:

```bash
sudo systemctl status xauby-control
sudo systemctl status xauby-engine@owner-itsara.service
sudo systemctl stop 'xauby-engine@*.service'
```

Stopping an engine does not close its position. Closing exposure is a separate,
Trade-PIN-confirmed, reduce-only command.

## Live activation

All tenants start in simulation. A user stores exchange credentials, attests
that withdrawal permission is disabled, runs the connection probe, and requests
live access. A platform admin then approves the request. The pilot certification
matrix permits live trading only for OKX swap.

The generic CCXT catalog is not a live-safety guarantee. Every additional
exchange/market must validate precision, minimum notional, fills, cancellation,
contract size, positions, reduce-only, short semantics and restart recovery
before it is added to `TenantSupervisor.LIVE_CERTIFIED`.

Admin approval atomically reserves one of the three engine slots and enables
all three hosted live gates: `simulate_only: false`, `LIVE_TRADING=true`, and
the tenant whitelist's `mode: live`. If capacity is full, approval leaves the
tenant queued rather than starting a fourth engine.

Secrets are kept in per-tenant `secrets.env` files with mode `0600`; systemd
reads them before dropping the engine into its `DynamicUser` sandbox. They are
excluded from the unencrypted local backup. Use an encrypted off-host secret
backup or reissue exchange credentials during disaster recovery.
Rotating credentials on an active live tenant stops its engine, restores all
simulation gates and requires a fresh connection test and admin approval.

## Manual trading

The hosted browser supports market intents:

- `OPEN_LONG` / `CLOSE_LONG`
- `OPEN_SHORT` / `CLOSE_SHORT`
- `PARTIAL_CLOSE_LONG` / `PARTIAL_CLOSE_SHORT`

The browser first requests a 60-second preview. Confirmation requires an
8-12 digit Trade PIN, CSRF token and idempotency key. The PIN is Argon2id hashed
(with a memory-hard scrypt fallback if an embedded Python cannot load Argon2)
and locks for 15 minutes after five failures. Replacing an existing PIN also
requires the current PIN. Confirmed orders enter the
tenant-local `manual_orders.db`; the engine atomically claims and completes each
command.

Manual entries may bypass strategy/regime recommendations, but remain subject
to pause, feed, balance, position, allocation, daily-loss and drawdown guards.
Manual closes use the existing broker reduce-only path.

## Capacity and operations

The included units budget approximately 280 MB for the control plane and 420 MB
maximum per engine. Do not run the TUI, optimizers, research jobs or backtests on
the pilot host. Validate three-engine operation with a 72-hour soak and alert at
80% RAM, 70% CPU p95, missing heartbeat, ambiguous orders and failed backups.

`xauby-backup.timer` creates a daily consistent archive of the control database,
tenant trading databases and non-secret configuration, retaining seven days.
Copy those archives to encrypted off-host storage.

Move the control database to PostgreSQL and split engine workers onto separate
hosts before raising the active limit beyond the measured capacity of this VPS.
