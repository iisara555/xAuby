# xAuby Documentation

English reference for operators and contributors. The root [README.md](../README.md) is the main entry point.

## Current baseline

The committed runtime uses OKX USDT-settled perpetual swaps through CCXT. XAU is
long-only with D1-gated entries; BTC remains long + short. Both run at 1x and
their RegimeRouter gates remain off.

| Symbol | Mode | Strategy | Sides | Router |
|--------|------|----------|-------|--------|
| `XAUUSDT` | `live` | `xauby_actionzone` | Long only, D1-gated | Off |
| `BTCUSDT` | `live` | `supertrend_ema200` | Long + short | Off |

## Documents

| Document | Description |
|----------|-------------|
| [architecture.md](architecture.md) | System layers, feature flags, pair isolation, router gates |
| [trading-flow.md](trading-flow.md) | Per-tick engine loop, entries, exits, NO_TRADE, handoff |
| [configuration.md](configuration.md) | `bot_config.yaml`, whitelist, source of truth, RegimeRouter mapping |
| [multi-exchange-ccxt.md](multi-exchange-ccxt.md) | CCXT REST adapter setup and current multi-exchange limitations |
| [tui.md](tui.md) | Textual dashboard, responsive layouts, strategy-aware legends |
| [shadow-evaluator.md](shadow-evaluator.md) | Credential-free Champion/Challenger forward evaluator, artifacts, safety gates, activation |
| [telegram.md](telegram.md) | Alerts, commands, multi-pair and per-mode messages |
| [roadmap_2026H2.md](roadmap_2026H2.md) | CTO roadmap for H2 2026: phased plan, findings, exit criteria (Thai) |
| [offsite_backup_runbook.md](offsite_backup_runbook.md) | P2.2 encrypted off-site backup, recovery-key custody, restore drill and key rotation |
| [research/xau_long_only_d1_certificate_2026-07-29.md](research/xau_long_only_d1_certificate_2026-07-29.md) | Certificate for the exact live XAU long-only + D1 preset |
| [research/xau_okx_pf_grid_2026-07-29.md](research/xau_okx_pf_grid_2026-07-29.md) | 432-cell OKX XAU grid and balanced-candidate selection |
| [research/binance_th_spot_certification_protocol.md](research/binance_th_spot_certification_protocol.md) | Binance TH BTCUSDT/XAUTUSDT venue-specific grids and honest certificate gates |
| [research/btc_supertrend_ema200_certificate_2026-07.md](research/btc_supertrend_ema200_certificate_2026-07.md) | Certificate for the live BTC config |
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
