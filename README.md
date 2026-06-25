<div align="center">

# xAuby

**Alternative Store of Value Trading System**

Multi-pair trading automation for **Binance Thailand spot** with a pluggable
exchange gateway — native Binance.th or **CCXT** — featuring per-pair strategies,
isolated strategy runners, per-symbol execution mode, strategy-aware charts,
RegimeRouter support, exchange stop-losses, Textual TUI, and Telegram operations.

[![Python](https://img.shields.io/badge/Python-3.12%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Exchange](https://img.shields.io/badge/Exchange-Binance.th%20%7C%20CCXT-F0B90B)](https://www.binance.th/)
[![UI](https://img.shields.io/badge/TUI-Textual-5c2dee)](https://textual.textualize.io/)
[![Docs](https://img.shields.io/badge/Docs-docs%2F-blue)](docs/README.md)

[Quick Start](#quick-start) | [Screens](#screens) | [Configuration](#configuration-essentials) | [Current Runtime State](#current-runtime-state) | [Backtest](#backtest-and-optimization) | [TUI](docs/tui.md) | [Telegram](docs/telegram.md)

</div>

---

![xAuby launcher menu](docs/screenshots/menu.svg)

> The launcher opens a native Textual menu (a focusable `OptionList`): navigate
> with ↑/↓ + Enter or the mouse, or press the `1`-`9` shortcut. From here you
> start the engine (sim/live), open the dashboard, edit the quick config, run a
> backtest, restart the service, or open the diagnostics tools.

## Screens

<details>
<summary>Dashboard · Trade log · Incident explorer (click to expand)</summary>

### Dashboard
![Dashboard](docs/screenshots/dashboard-wide.svg)

### Trade log
![Trade log](docs/screenshots/tradelog.svg)

### Incident explorer
![Incident explorer](docs/screenshots/incidents.svg)

### Quick Config (native Textual)
![Quick Config](docs/screenshots/quick-config.svg)

</details>

## Overview

**xAuby** is an event-driven crypto trading system for store-of-value pairs such
as **XAUT** and **BTC** on configured USDT markets. The current committed
baseline uses **Binance.th spot** in long-only mode.

1. Ingest candles and tickers through REST plus WebSocket.
2. Resolve the configured strategy, timeframe, execution mode, and portfolio budget for every active pair.
3. Run each pair through its own strategy instance and `StrategyRunner`.
4. Optionally route a pair through `RegimeRouter` when both global and per-asset gates are enabled.
5. Place exchange orders, or simulated trades, with ATR stop-loss, trailing, and breakeven logic.
6. Persist trades and telemetry to SQLite plus JSONL for replay, incidents, and dashboard state.
7. Operate through Textual TUI, launcher, scripts, and Telegram.

> Start in simulation first. Enable live only after risk limits, alerts, replay validation, and per-pair router gates are checked.

---

## Features

| Area | Highlights |
|------|------------|
| Multi-pair | `coin_whitelist.json` pair universe, per-symbol contexts, hot reload |
| Exchange | Pluggable `IExchangeGateway` — Binance native client or CCXT adapter via the `exchange:` config block; credentials, fee, and quote asset all config-driven |
| Multi-instance | Relocatable runtime root (`XAUBY_HOME` / `XAUBY_INSTANCE_ID`) — run isolated engines side by side on one host |
| Strategy | Each whitelist asset selects its own strategy plugin |
| Isolation | Each active symbol gets its own strategy instance, runner, mode, and handoff state |
| Plugin safety | Static (AST) sandbox scan rejects strategies that touch the engine/DB/network or use `eval`/`exec` (opt-in strict mode) |
| Auto regime | Optional RegimeRouter maps market regimes to strategies per pair; macro weights are configurable per asset class |
| Portfolio | Global sizing plus `portfolio.symbols.<SYMBOL>.position_sizing` overrides |
| Risk | Max positions, daily loss cap, cooldowns, slippage gate, exchange SL restore |
| Drawdown guard | Portfolio kill-switch: halts new BUYs (and optionally force-closes) when equity drops `max_drawdown_pct` below its high-water mark |
| Sizing | Risk-based auto-compounding: `qty = equity × risk_pct / sl_distance`, capped by per-trade and per-symbol allocation |
| Stops | Initial ATR SL, trailing, breakeven, multi-tick local SL confirmation |
| Backtest | Live-config replay, low-CPU optimizer, best config apply to YAML |
| Strategy R&D | Multi-year Binance global data, per-regime PF evaluation harness, train/test gates, short-side simulator |
| Observability | `run_id`, `tick_id`, JSONL events, replay validation, health JSON |
| UI/Ops | Textual dashboard, pair switching, strategy-aware chart legend, Telegram commands |

---

## Quick Start

Requirements: Python 3.12+, a configured exchange API key, optional Telegram bot.

```bash
git clone https://github.com/iisara555/xAuby.git
cd xAuby

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -e .

cp .env.example .env
python run_xauby.py --simulate
python health_check.py
```

Other entrypoints:

```bash
python launcher.py   # opens the Textual launcher menu (engine, dashboard, config, backtest, tools)
xauby --sim
python -m xauby.ui.textual_tui.app
```

Common operations:

```bash
xauby restart        # Restart the engine; live mode uses controlled preflight
xauby restart --live # Force controlled live restart
xauby update         # Pull origin/main from GitHub, syntax-check changes, restart
```

`xauby update` is the operator shortcut for:

```bash
scripts/deploy_from_github.sh --restart --branch=main
```

The controlled restart path allows positions that are already tracked by the
bot, including their matching stop-loss order, and still blocks unknown balances
or untracked open orders.

---

## Configuration Essentials

| File | Role |
|------|------|
| `.env` | Secrets, `LIVE_TRADING`, `TELEGRAM_*`; do not commit |
| `bot_config.yaml` | Engine, Strategy, Portfolio, notifications, backtest optimizer |
| `coin_whitelist.json` | Active pairs, timeframes, per-pair strategy, mode, RegimeRouter gates |

The config is split into three responsibilities:

| Level | Owns |
|-------|------|
| Engine Config | Exchange connection, logging, monitoring, notifications, global guards |
| Strategy Config | Signal and exit parameters that affect Live, Backtest, Replay |
| Portfolio Config | Position sizing, allocation, capital management |

Core principle: every parameter that can change backtest or live trade outcomes belongs under Strategy Config or per-symbol strategy overrides. The engine remains strategy-agnostic.

### Exchange, economics, and credentials

`bot_config.yaml -> exchange:` is the single source of truth for exchange
selection and exchange-wide economics. The engine, backtest, replay, health
probe, and ops scripts all resolve through the same helpers
(`xauby/runtime/exchange_config.py`), so simulation stays in parity with live.

```yaml
exchange:
  provider: binance        # binance (native client) | ccxt (generic adapter)
  name: binance.th
  quote_asset: USDT        # default quote currency; also drives live cash reads
  fee_pct: 0.001           # taker fee fraction (0.1%); used by sim/backtest/replay
  # --- ccxt provider only ---
  # ccxt_id: kraken
  # api_key_env: KRAKEN_API_KEY   # else defaults to <PREFIX>_API_KEY
```

- **Credentials** read from exchange-driven env vars: `<PREFIX>_API_KEY` /
  `_API_SECRET` / `_BASE_URL` where `<PREFIX>` derives from the provider
  (`binance` → `BINANCE_*`, `kraken` → `KRAKEN_*`); override with `*_env` keys.
- **Fee** precedence: `exchange.fee_pct` → `backtest.fee_pct` → `trading.fee_pct`
  → `0.001`; per-symbol `sim_fee_pct` (whitelist) wins for that pair.
- CCXT is **REST-only** (no websocket stream, no exchange-side `STOP_LOSS_LIMIT`
  by default) — see [docs/multi-exchange-ccxt.md](docs/multi-exchange-ccxt.md).

See [docs/configuration.md](docs/configuration.md) for the full env-var table.

---

## Current Runtime State

This is the current committed baseline.

Exchange: **Binance.th spot** (`provider: binance`, `name: binance.th`, `market_type: spot`) via the exchange plugin registry. The default quote asset is `USDT`; backtests can still proxy XAUT to `PAXGUSDT`.

Both live pairs are configured as **long-only** in `coin_whitelist.json`. Shorts and live short execution are disabled for the committed baseline.

| Symbol | Mode | Strategy | Primary TF | Confirm TF | Sides | Notes |
|--------|------|----------|------------|------------|-------|-------|
| `XAUTUSDT` | `live` | `cdc_action_zone` | `4h` | `1d` | `long` | Backtest proxy `PAXGUSDT`; RegimeRouter off |
| `BTCUSDT` | `live` | `donchian_trend` | `4h` | `1d` | `long` | RegimeRouter off |

Safety gate: a pair with `mode: live` and `regime_router_enabled: true` is forced to sim unless that pair also has `regime_router_live_confirmed: true`.

---

## Strategy Routing

Current RegimeRouter mapping:

| Regime | Strategy | Action |
|--------|----------|--------|
| `BULL_BREAKOUT` | `cdc_action_zone` | BUY allowed |
| `BULL_TREND_STRONG` | `cdc_action_zone` | BUY allowed |
| `BULL_TREND_WEAK` | `cdc_action_zone` | BUY allowed |
| `LOW_VOL_ACCUMULATION` | `bbrsi_mean_reversion` | BUY allowed |
| `LOW_VOL_RANGE` | `bbrsi_mean_reversion` | BUY allowed |
| `VOLATILITY_EXPANSION` | `supertrend_ema200` | BUY allowed |
| `SIDEWAYS_CHOP` | `bbrsi_mean_reversion` | BUY allowed |
| `BEAR_TREND_WEAK` | `cdc_action_zone` | BUY allowed |
| `PANIC_SELL` | `None` | NO_TRADE |
| `BEAR_BREAKDOWN` | `None` | NO_TRADE |
| `BEAR_TREND_STRONG` | `None` | NO_TRADE |

When the backtest regime filter is enabled for a symbol, it mirrors this routing layer: BUY signals are suppressed whenever the classifier returns a regime outside the active strategy's target regime set, so replay results stay close to the live entry gate.

NO_TRADE behavior blocks new BUY entries, keeps existing stop protection, tightens trailing stop to 1x ATR, waits for 3 tradeable recovery candles, and can force close after 6 NO_TRADE candles.

Strategy handoff is safe by design: if a regime switch happens while a position is open, the existing position remains owned by the old strategy until closed. New entries use the new strategy only after handoff completes.

---

## Chart And Legend Source Of Truth

The terminal chart and Textual legend no longer hard-code CDC lines. They use:

1. `bot_config.yaml -> architecture.strategy_chart_indicators`
2. Indicator plugin `display_config`
3. The active strategy resolved for the selected symbol

Current strategy display mapping:

| Strategy | Indicator plugin(s) | Zones | Lines |
|----------|---------------------|-------|-------|
| `cdc_action_zone` | `cdc_action_zone` | Blue / Green / Red / Neutral | EMA12, EMA26 |
| `supertrend_ema200` | `supertrend`, `ema200` | ST Bull / ST Bear / Neutral | SuperTrend, EMA200 |
| `bbkc_squeeze` | `bbkc_squeeze` | Compressed / Breakout / Neutral | Bollinger Bands, Keltner Channels |
| `bbrsi_mean_reversion` | `bbrsi_mean_reversion` | Oversold / Neutral / Overbought | Bollinger Bands |
| `btc_ema_pullback` | `btc_ema_pullback` | Trend / Pullback / Reclaim / Neutral | EMA Fast, EMA Slow, EMA Trend |
| `ict_lite_strategy` | `ict_lite` | Sweep Low / Reclaim / MSS / Neutral | EMA Fast, EMA Slow, Recent High, Recent Low |
| `rsi2_meanrev` | `rsi2_meanrev` | Buy Setup / Oversold / Exit / Neutral | EMA200, SMA5 |
| `vol_breakout` | `vol_breakout` | Breakout / ATR Expansion / Neutral | Range High, Exit EMA |

When adding a new strategy, add the strategy plugin, matching indicator plugin, tests, and `strategy_chart_indicators` entry together.

---

## Backtest And Optimization

Backtest and Live resolve strategy and portfolio config through the same resolver. This keeps backtest, replay, and live trading aligned with the same strategy source of truth.

**Data source:** backtests pull from `https://api.binance.com` (Global Binance) by default, giving BTCUSDT longer historical coverage and 12-month rolling windows. Live trading still uses the configured live exchange endpoint. The source is set via `backtest.data_base_url` in `bot_config.yaml` and can be overridden with `BINANCE_BACKTEST_URL`.

**Regime-aware replay:** setting `use_regime_filter: true` on a symbol's strategy config activates the regime classifier inside the replay engine. BUY signals are suppressed outside the strategy's `TARGET_REGIMES`, re-evaluated every 24 bars by default via `regime_update_bars`.

```bash
python scripts/replay_backtest.py --symbol XAUTUSDT --config bot_config.yaml
python scripts/replay_backtest.py --symbol BTCUSDT --config bot_config.yaml
```

Low-CPU optimizer:

```bash
python scripts/optimize_pair_configs.py
```

Strategy selection is dry-run by default:

```bash
python scripts/select_pair_strategy.py --symbol BTCUSDT --candidates donchian_trend,cdc_action_zone,supertrend_ema200
python scripts/select_pair_strategy.py --symbol BTCUSDT --candidates donchian_trend,cdc_action_zone,supertrend_ema200 --apply
```

`--apply` only writes the best passing candidate back to `bot_config.yaml`.

---

## Regime Strategy Evaluation (R&D)

Data-driven gatekeeper for strategy changes. A candidate strategy may replace an
incumbent in `regime_router.mapping` **only** if it beats the incumbent's Profit
Factor on both the train (oldest 70%) and test (newest 30%) splits of multi-year
real market data, with enough trades and bounded drawdown. Full protocol and the
recorded verdicts live in [docs/regime_strategy_selection.md](docs/regime_strategy_selection.md).

```bash
# Fetch multi-year BTCUSDT klines from Binance global (binance.th caps at ~6 months)
python scripts/fetch_global_klines.py --symbol BTCUSDT --timeframe 1h --years 3

# Tune candidates on the train split only, then produce the verdict table
python scripts/regime_strategy_eval.py --timeframe 1h --grid
python scripts/regime_strategy_eval.py --timeframe 1h --config-overrides core/regime_overrides.json

# Short-side research (futures fee model; research only — spot cannot short)
python scripts/regime_strategy_eval.py --timeframe 1h --skip-long --short-strategies donchian_short,supertrend_short,rsi2_short
```

The harness labels every bar with the live regime classifier (rolling 250 bars,
no lookahead), gates entries to each strategy's regime family exactly like the
RegimeRouter, and reports PF / win rate / trades / max DD per family and split.

Research plugins (no chart indicator plugins; never map the `*_short` ones in
production — their BUY opens a simulated short):

| Plugin | Side | Mechanic |
|--------|------|----------|
| `donchian_trend` | long | Turtle breakout above EMA200 with ADX filter |
| `rsi2_meanrev` | long | Connors RSI(2) dip-buy above EMA200 |
| `vol_breakout` | long | ATR-expansion range breakout |
| `donchian_short` / `supertrend_short` / `rsi2_short` | short (research only) | Mirrored bear-regime entries, Binance futures fee + funding model |

Verdict as of 2026-06: three rounds (long 1h, long 4h, short 1h) led to
`donchian_trend` deployment on BTCUSDT 1h with RegimeRouter live-confirmed after
SL/trailing and config contamination fixes. Backtests now use Global Binance data
and 12-month windows to provide more meaningful trade counts.

---

## Replay Validation

Use replay validation after restarts, incidents, and config changes:

```bash
python scripts/replay_validate.py <run_id> --symbol XAUTUSDT
python scripts/replay_validate.py <run_id> --symbol BTCUSDT
```

Replay validation checks whether recorded live `signal_evaluated` events match the strategy replay for the same run. It validates strategy output before macro guard, cooldown, and execution overrides.

---

## Trading Modes

| Mode | How to enable | Behavior |
|------|---------------|----------|
| Simulation | `--simulate`, `simulate_only: true`, or per-asset `mode: sim` | No real orders; SimBroker ledger when enabled |
| Live | `--live` plus `LIVE_TRADING=true` plus `simulate_only: false` plus per-asset `mode: live` | Real orders on the configured exchange |
| Read-only | `--read-only` or `read_only: true` | Signals only, no orders |
| Semi-auto | `trading.mode: semi_auto` | Telegram confirmation before BUY |

---

## Deploy On A VPS

```bash
mkdir -p core/logs
nohup ./venv/bin/python run_xauby.py --live >> core/logs/xauby_engine_bg.log 2>&1 &

./scripts/start_dashboard_tmux.sh
./scripts/attach_dashboard_tmux.sh
```

Use exactly one engine instance **per runtime root**. `<root>/.engine.lock`
prevents duplicates within a root.

To run isolated engines side by side on one host, give each its own runtime root
— every artifact (DB, lock, logs, sim balances, state JSON) is namespaced under it:

```bash
XAUBY_HOME=/data/acctA python run_xauby.py --live   # -> /data/acctA/...
XAUBY_INSTANCE_ID=acctB  python run_xauby.py --live  # -> core/acctB/...
```

With both unset, everything resolves under `core/` exactly as before.

For normal VPS operations after the CLI is installed, prefer:

```bash
xauby update   # deploy origin/main + controlled restart
xauby restart  # controlled restart without pulling code
```

Manual fallback:

```bash
scripts/deploy_from_github.sh --restart --branch=main
scripts/controlled_restart_engine.sh
```

---

## Project Layout

```text
xAuby/
|-- run_xauby.py
|-- launcher.py            # thin shim re-exporting the xauby.launcher package
|-- bot_config.yaml
|-- coin_whitelist.json
|-- docs/
|-- scripts/
|-- tests/
`-- xauby/
    |-- launcher/          # interactive launcher: config_io, process, maintenance, quick_config
    |-- engine/
    |-- strategies/
    |-- runtime/
    |-- backtest/
    |-- observability/
    `-- ui/textual_tui/
```

---

## Scripts And Tests

```bash
python -m unittest discover -s tests -q
python health_check.py
python scripts/incident_explorer.py list
python scripts/replay_validate.py <run_id> --symbol XAUTUSDT
python scripts/select_pair_strategy.py --symbol BTCUSDT --candidates cdc_action_zone,supertrend_ema200,bbkc_squeeze,bbrsi_mean_reversion
python scripts/optimize_pair_configs.py
python scripts/capture_tui_screenshots.py
python scripts/fetch_global_klines.py --symbol BTCUSDT --timeframe 1h --years 3
python scripts/regime_strategy_eval.py --timeframe 1h --grid
```

---

## Safety Checklist

- [ ] Simulation run reviewed before live.
- [ ] API key has Spot permission only and no withdraw permission.
- [ ] `risk_pct`, `max_position_per_trade_pct`, and global guards reviewed for account size.
- [ ] `risk.drawdown_guard` threshold set for your risk tolerance (kill-switch on portfolio drawdown).
- [ ] BTC backtest reviewed after downloading fresh 12-month 1h candles from Global Binance.
- [ ] `regime_router_live_confirmed: true` is set only after explicit per-pair sign-off.
- [ ] `architecture.strategy_sandbox_strict: true` when running third-party/untrusted strategy plugins.
- [ ] `scripts/select_pair_strategy.py` report reviewed before using `--apply`.
- [ ] `scripts/replay_validate.py <run_id> --symbol <PAIR>` passes after restart.
- [ ] Telegram tested; `/status` shows all active pairs and per-pair mode.
- [ ] Engine supervised by tmux/systemd, not an unmanaged SSH shell.

---

## Disclaimer

This software is for education and research. Cryptocurrency trading involves substantial risk. Past performance does not guarantee future results. Comply with the configured exchange's API terms and applicable regulations.

---

<div align="center">

**xAuby** - disciplined multi-pair spot automation

</div>
