# VPS Latency Checks

Use this when the bot feels slow, the TUI lags, or exchange requests time out.
The goal is to separate network latency from local VPS load or disk I/O.

## Quick Check

```bash
./venv/bin/python scripts/vps_latency_check.py
```

The script's default targets are still `api.binance.th` / `www.binance.th`
(legacy default). The live runtime is OKX (`api.okx.com`), so pass it
explicitly when checking the actual trading path:

```bash
./venv/bin/python scripts/vps_latency_check.py --host api.okx.com
```

JSON output for logging/comparison:

```bash
./venv/bin/python scripts/vps_latency_check.py --json
```

## What To Look For

- `curl total`: rough HTTPS request time to the target host(s) (`api.okx.com` for the live path; `api.binance.th` / `www.binance.th` are the script's legacy defaults).
- `curl ttfb`: time to first byte; high values usually mean routing/API latency.
- `tcp443`: raw TCP connect time; high values point to network distance/routing.
- `ping`: ICMP may be blocked or deprioritized, so treat it as a hint only.
- `clock`: `NTPSynchronized=yes` matters because signed Binance requests use timestamps.
- `loadavg`: sustained load above available CPU cores can make the bot/TUI lag.
- `disk_used` / `free`: low free space or slow disk can hurt SQLite/event/state writes.

## Bot Metrics

The TUI header surfaces:

- `REST`: latest REST request latency.
- `WS`: latest websocket tick age.
- `Tick`: whole engine tick duration.
- `Sync`: candle sync duration.
- `State`: state JSON export duration.

If `REST` is high but `Tick` is low, the VPS routing/exchange path is likely the issue.
If `Tick`, `Sync`, or `State` is high, optimize local work or reduce polling/logging.
