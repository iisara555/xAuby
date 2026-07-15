# WebUI

> This document describes the legacy single-operator WebUI. Hosted multi-user
> deployments use the tenant-aware [SaaS control plane](saas.md), which adds
> lifecycle controls, admin-approved Live activation and Trade-PIN-confirmed
> manual long/short commands.

xAuby includes a small browser dashboard for checking the bot from a desktop or
phone. By default it is monitor-only. Manual buy/sell controls stay disabled
unless a separate trade confirmation code is configured.

## Start On The VPS

```bash
./scripts/start_webui.sh
```

Default bind:

```text
127.0.0.1:8787
```

Keep the engine running separately as usual. The WebUI reads:

- `core/logs/xauby_bot_state.json`
- `core/xauby.db`
- recent health, log, and event files

The mobile Home view shows live OKX runtime status, 32 recent OHLC candles,
EMA12/EMA26 overlays, CDC Action Zone details, and open-position partial TP
status. Activity uses its own scrolling view for events and closed trades.

## Manual Buy/Sell

Manual trading is intentionally fail-closed:

- The WebUI never places exchange orders directly. It writes the same short-lived
  `core/manual_order_request.json` IPC request used by the TUI, and the engine
  consumes it on the next tick.
- The engine still applies its normal execution, sizing, cooldown, pair, feed,
  read-only, and risk/allocation guards.
- The request expires after 120 seconds if the engine does not claim it.
- The WebUI refuses manual orders when the runtime state file is stale, when the
  focused symbol does not match the request, when BUY is requested while a
  tracked position is open, or when SELL is requested while no tracked position
  exists.

Enable the controls by setting a confirmation code with at least 6 characters:

```bash
export XAUBY_WEBUI_TRADE_CONFIRMATION_CODE='<separate-random-code>'
```

`XAUBY_WEBUI_TRADE_CODE` is accepted as a shorter alias. This code is separate
from `XAUBY_WEBUI_PASSWORD`; keep both configured if the WebUI is reachable from
another device. With `BOT_READ_ONLY=true`, the WebUI manual controls remain
locked.

## Partial TP Status

On the Home view, the Position card appends partial take-profit status to the
PnL line when the active strategy has it configured:

- `PTP 50% @ 4,604` means the one-shot partial close is still pending at that
  trigger price.
- `PTP banked` means the partial close has already executed for the current
  position and the remainder is riding to the normal strategy exit.

Partial TP remains engine-managed; the WebUI manual controls only queue full
manual BUY/SELL requests for the focused symbol.

## Windows Access

Use a free SSH tunnel from Windows PowerShell:

```powershell
ssh -L 8787:127.0.0.1:8787 user@your-vps-ip
```

Then open:

```text
http://localhost:8787
```

Close the SSH session to close access.

## Mobile Access

For phones, SSH tunneling is awkward. The simple free option is Tailscale:

1. Install Tailscale on the VPS and on your phone.
2. Log in to the same tailnet.
3. Keep manual trading disabled unless you need it.
4. Set a WebUI password before binding to any non-loopback address:

```bash
export XAUBY_WEBUI_PASSWORD='<long-random-password>'
```

5. Either use Tailscale Serve, or bind the WebUI to the VPS Tailscale IP:

```bash
WEBUI_HOST=<vps-tailscale-ip> WEBUI_PORT=8787 ./scripts/start_webui.sh
```

Then open `http://<vps-tailscale-ip>:8787` from the phone while Tailscale is on.
The browser shows the branded xAuby sign-in page; enter the password from
`XAUBY_WEBUI_PASSWORD`.

The WebUI refuses non-loopback binds without `XAUBY_WEBUI_PASSWORD` or
`XAUBY_WEBUI_TOKEN`, or configured Google sign-in, unless
`XAUBY_WEBUI_ALLOW_UNAUTH_REMOTE=1` is explicitly set. Do not use that override
on a public VPS.

## Sign-In And Sessions

When `XAUBY_WEBUI_PASSWORD` is set, browsers are redirected to a branded
`/login` page (no password set = loopback default, no sign-in at all):

- A correct password sets a signed `HttpOnly; SameSite=Lax` session cookie
  valid for 7 days. `GET /logout` ends the session.
- Sessions are signed with a random per-process secret, so a WebUI restart
  signs everyone out. Set `XAUBY_WEBUI_SESSION_SECRET` to a long random value
  to keep sessions across restarts.
- The cookie omits `Secure` because the supported deployments are plain HTTP
  over loopback or Tailscale. If you terminate TLS in front of the WebUI
  (e.g. Tailscale Serve), set `XAUBY_WEBUI_COOKIE_SECURE=1`.
- Failed logins are delayed 0.3s as a brute-force damper; there is no full
  rate limiter because the WebUI is designed for private networks only.
- Before sign-in, only the login page and its assets are served (`/login`,
  `/login.js`, `/style.css`, `/xau-logo.svg`). The dashboard, the operator
  avatar, and every `/api/*` endpoint stay behind auth.

API and tunnel clients are unaffected: `/api/*` still answers `401` with
`WWW-Authenticate: Basic`, and both HTTP Basic Auth (default username `xauby`,
override with `XAUBY_WEBUI_USERNAME`) and `Authorization: Bearer` tokens
(`XAUBY_WEBUI_TOKEN`) keep working on every endpoint.

## Google Sign-In

Google sign-in is optional and only turns on when all required OAuth settings
and at least one allowlist are present:

```bash
export XAUBY_GOOGLE_CLIENT_ID='<google-oauth-client-id>'
export XAUBY_GOOGLE_CLIENT_SECRET='<google-oauth-client-secret>'
export XAUBY_GOOGLE_REDIRECT_URI='https://your-host.example/auth/google/callback'
export XAUBY_GOOGLE_ALLOWED_EMAILS='you@gmail.com'
# or allow a whole Google Workspace domain:
export XAUBY_GOOGLE_ALLOWED_DOMAINS='example.com'
```

Create a Google OAuth client of type **Web application** and add the exact
`XAUBY_GOOGLE_REDIRECT_URI` value to its Authorized redirect URIs. The callback
path is:

```text
/auth/google/callback
```

For private Tailscale HTTP access, Google OAuth may be easier behind an HTTPS
front door such as Tailscale Serve or another reverse proxy, because Google
requires redirect URIs to match the OAuth client configuration exactly. Keep
`XAUBY_WEBUI_PASSWORD` configured as a fallback unless Google sign-in has been
tested from every device.

## Endpoints

- `GET /login` / `POST /login` (sign-in page + form)
- `GET /logout`
- `GET /auth/config`
- `GET /auth/google/start`
- `GET /auth/google/callback`
- `GET /api/state`
- `GET /api/health`
- `GET /api/recent-events`
- `GET /api/trades?limit=10`
- `GET /api/candles?symbol=XAUUSDT&timeframe=4h&limit=32`
- `POST /api/manual-order` (disabled unless
  `XAUBY_WEBUI_TRADE_CONFIRMATION_CODE` or `XAUBY_WEBUI_TRADE_CODE` is set)

All GET API endpoints are read-only.
