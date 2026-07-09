# WebUI

xAuby includes a small read-only browser dashboard for checking the bot from a
desktop or phone. It does not place, cancel, pause, or resume trades.

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

The mobile Home view shows live OKX runtime status, 24 recent OHLC candles,
EMA12/EMA26 overlays, CDC Action Zone details, and open-position partial TP
status. Activity uses its own scrolling view for events and closed trades.

## Partial TP Status

On the Home view, the Position card appends partial take-profit status to the
PnL line when the active strategy has it configured:

- `PTP 50% @ 4,604` means the one-shot partial close is still pending at that
  trigger price.
- `PTP banked` means the partial close has already executed for the current
  position and the remainder is riding to the normal strategy exit.

The WebUI is read-only; it only reflects the engine state file and does not
manually trigger or cancel partial TP orders.

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
3. Keep the WebUI read-only.
4. Set a WebUI password before binding to any non-loopback address:

```bash
export XAUBY_WEBUI_PASSWORD='<long-random-password>'
```

5. Either use Tailscale Serve, or bind the WebUI to the VPS Tailscale IP:

```bash
WEBUI_HOST=<vps-tailscale-ip> WEBUI_PORT=8787 ./scripts/start_webui.sh
```

Then open `http://<vps-tailscale-ip>:8787` from the phone while Tailscale is on.
The browser will prompt for Basic Auth. The default username is `xauby`; override
it with `XAUBY_WEBUI_USERNAME`.

The WebUI refuses non-loopback binds without `XAUBY_WEBUI_PASSWORD` or
`XAUBY_WEBUI_TOKEN`, unless `XAUBY_WEBUI_ALLOW_UNAUTH_REMOTE=1` is explicitly
set. Do not use that override on a public VPS.

## Endpoints

- `GET /api/state`
- `GET /api/health`
- `GET /api/recent-events`
- `GET /api/trades?limit=10`
- `GET /api/candles?symbol=XAUUSDT&timeframe=4h&limit=24`

All endpoints are read-only.
