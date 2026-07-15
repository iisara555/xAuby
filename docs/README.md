# xAuby Documentation

English reference for operators and contributors. The root [README.md](../README.md) is the main entry point.

## Current baseline

| Symbol | Mode | Strategy | Router |
|--------|------|----------|--------|
| `XAUTUSDT` | `live` | `cdc_action_zone` | Off |
| `BTCUSDT` | `sim` | `supertrend_ema200` | Auto-regime sim soak |

## Documents

| Document | Description |
|----------|-------------|
| [architecture.md](architecture.md) | System layers, feature flags, pair isolation, router gates |
| [trading-flow.md](trading-flow.md) | Per-tick engine loop, entries, exits, NO_TRADE, handoff |
| [configuration.md](configuration.md) | `bot_config.yaml`, whitelist, source of truth, RegimeRouter mapping |
| [multi-exchange-ccxt.md](multi-exchange-ccxt.md) | CCXT REST adapter setup and current multi-exchange limitations |
| [tui.md](tui.md) | Textual dashboard, responsive layouts, strategy-aware legends |
| [telegram.md](telegram.md) | Alerts, commands, multi-pair and per-mode messages |
| [webui.md](webui.md) | Read-only browser dashboard via SSH tunnel or Tailscale |
| [saas.md](saas.md) | Hosted multi-tenant control plane, Owner workflow, live approval and manual trading |
| [screenshots/](screenshots/) | TUI captures in SVG format |

## Regenerate TUI screenshots

Requires a running or sample `core/logs/xauby_bot_state.json` for rich dashboard content:

```bash
./venv/bin/python scripts/capture_tui_screenshots.py
```

Outputs stable filenames:

- `docs/screenshots/dashboard-wide.svg`
- `docs/screenshots/tradelog.svg`
- `docs/screenshots/incidents.svg`
- `docs/screenshots/menu.svg`

## Observability deep dive

See [xauby/observability/README.md](../xauby/observability/README.md) for events, replay, and incident tooling.
