# xAuby Documentation

English reference for operators and contributors. The root [README.md](../README.md) is the main entry point.

## Current baseline

The committed runtime uses OKX USDT-settled perpetual swaps through CCXT. Both
active pairs are live-enabled for long and short at 1x leverage; their
RegimeRouter gates remain off.

| Symbol | Mode | Strategy | Sides | Router |
|--------|------|----------|-------|--------|
| `XAUUSDT` | `live` | `xauby_actionzone` | Long + short | Off |
| `BTCUSDT` | `live` | `supertrend_ema200` | Long + short | Off |

## Documents

| Document | Description |
|----------|-------------|
| [architecture.md](architecture.md) | System layers, feature flags, pair isolation, router gates |
| [trading-flow.md](trading-flow.md) | Per-tick engine loop, entries, exits, NO_TRADE, handoff |
| [configuration.md](configuration.md) | `bot_config.yaml`, whitelist, source of truth, RegimeRouter mapping |
| [multi-exchange-ccxt.md](multi-exchange-ccxt.md) | CCXT REST adapter setup and current multi-exchange limitations |
| [tui.md](tui.md) | Textual dashboard, responsive layouts, strategy-aware legends |
| [telegram.md](telegram.md) | Alerts, commands, multi-pair and per-mode messages |
| [roadmap_2026H2.md](roadmap_2026H2.md) | CTO roadmap for H2 2026: phased plan, findings, exit criteria (Thai) |
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
