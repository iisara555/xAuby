# CLAUDE.md

Guidance for AI assistants (and humans) working in this repository. Read this
before making changes. The deepest reference material lives in `README.md`,
`README_DEV.md`, and `docs/`; this file is the orientation layer and the place
where conventions that are easy to violate are spelled out.

## What this project is

**xAuby** is a single-process, multi-symbol **perpetual-swap** trading bot on
**OKX** (`api.okx.com`), routed through the exchange-neutral **CCXT** adapter. It
ingests candles/tickers over REST + WebSocket, runs each whitelisted pair through
its own strategy plugin, optionally routes the pair through a market-regime
classifier, places swap orders (or simulated ones)
with ATR stop-loss / trailing / breakeven logic, and persists everything to
SQLite + JSONL for replay, incidents, and a Textual dashboard. Operators drive it
through a CLI, a Textual TUI, and Telegram.

Package name: `xauby`. Python `>=3.10` (pyproject), README targets 3.12+; the dev
container currently runs 3.11.

## Entry points

| Command | What it does |
|---------|--------------|
| `python run_xauby.py --simulate` / `--live` | Start the trading engine directly |
| `python launcher.py` | Interactive launcher (engine + TUI menus) |
| `xauby` / `xauby --sim` / `xauby --live` | Installed console script (`xauby.cli:main`, also `python -m xauby`) |
| `xauby restart [--live]` | Controlled restart (live path runs preflight via `scripts/controlled_restart_engine.sh`) |
| `xauby update` | `scripts/deploy_from_github.sh --restart --branch=main` then restart |
| `python -m xauby.ui.textual_tui.app` | Launch the TUI standalone |
| `python health_check.py` | One-shot health probe |
| `bin/xauby` | Bash wrapper that cd's to project root and prefers `venv/bin/python` |

Setup: `python3 -m venv venv && source venv/bin/activate && pip install -r
requirements.txt && pip install -e .`, then `cp .env.example .env`.

## Repository map

```
run_xauby.py        Thin engine launcher (sim/live/read-only flags)
launcher.py         Thin back-compat shim re-exporting the xauby.launcher package (TUI execs it on restart)
bot_config.yaml     Engine + Strategy + Portfolio + notifications + optimizer config (committed, no secrets)
coin_whitelist.json Active pairs: strategy, timeframes, mode, router gates (committed)
.env / .env.example Secrets + LIVE_TRADING / TELEGRAM_* (.env is gitignored)
health_check.py     Standalone health check
bin/xauby           CLI wrapper script
scripts/            Ops + R&D scripts (backtest, optimize, deploy, replay, restart, screenshots)
tests/              65 unittest modules (test_*.py)
docs/               Operator/contributor docs (architecture, trading-flow, configuration, tui, telegram)
weekly_reviews/     Generated weekly review markdown
xauby/
  cli.py            CLI argument handling + restart/update logic
  launcher/         Interactive launcher package: config_io, process, maintenance, quick_config (menus)
  engine/           LiteTradingEngine (composed of mixins), brokers, risk, orders, regime routing
  strategies/       Strategy plugins + indicators/ indicator plugins + registries
  runtime/          Pair registry, whitelist validation, config resolvers, symbol utils
  regime/           Regime classifier + models
  backtest/         Replay engine, optimizer, metrics, data fetch (uses Global Binance by default)
  observability/    EventEmitter, JSONL store, replay validation, incidents, health
  ui/               Terminal chart + Textual TUI (textual_tui/, incl. quick_config/ native config screens), dashboard, tradelog
  api/              CCXT adapter (OKX) REST + WebSocket + exchanges/ plugin registry
  notifications/    Telegram bot, command poller, report formatting, schedulers
  analytics/, track_record/, macro/, domain/, database/, storage/, utils/
```

`LiteTradingEngine` (`xauby/engine/trading.py`) is a thin class composed of
mixins: `BaseEngine`, `AlertMixin`, `RiskMixin`, `OrderMixin`, `ReconcileMixin`,
`LoopMixin`. The per-tick logic lives in `xauby/engine/loop.py` (`tick()`).

## Configuration: source-of-truth model (important)

Config is split into three responsibilities. Putting a value in the wrong layer
is a recurring bug source because backtest, live, replay, and the optimizer all
resolve through the **same** strategy/portfolio resolver — divergence breaks
parity.

| Layer | Owns | Must NOT own |
|-------|------|--------------|
| **Engine config** (`bot_config.yaml` top-level) | Exchange, logging, monitoring, notifications, global risk guards, feature flags | Strategy signal/exit params |
| **Strategy config** (`strategy.config.<id>`, `strategy.symbols.<SYMBOL>`, whitelist `strategy_params`) | RSI/ATR/volume, SL, trailing, breakeven, strategy timeframe, signal/exit logic | Credentials or sizing |
| **Portfolio config** (`portfolio.position_sizing`, `portfolio.symbols.<SYMBOL>.position_sizing`) | Position sizing, allocation, capital | Indicator/signal rules |

**Rule:** any value that can change a backtest result must live under Strategy or
Portfolio config, never in engine-only blocks. The engine is strategy-agnostic.

- Secrets live ONLY in `.env`. `bot_config.yaml` is committed and must stay
  key-free.
- `coin_whitelist.json` is the source of truth for pairs when
  `architecture.whitelist_strict: true` (it is). `data.pairs` is synced from the
  whitelist when `sync_yaml_pairs_from_whitelist: true`.

### Architecture feature flags (`bot_config.yaml -> architecture:`)

`whitelist_strict`, `indicator_registry_enabled`, `sync_yaml_pairs_from_whitelist`,
`tui_indicator_registry`, `per_symbol_execution_mode`, `sim_broker_enabled`,
`regime_router_enabled`, `regime_confidence_filter`, `event_bus_enabled`.
Rollback is intentionally a flag flip to `false` — no code revert required.

## Current runtime baseline (from coin_whitelist.json / bot_config.yaml)

Exchange is **OKX perpetual swap** (`exchange.provider: ccxt`, `ccxt_id: okx`,
`name: okx`, `market_type: swap`, `margin_mode: isolated`, `position_mode:
one_way`) via the CCXT adapter, at **1x leverage** (`derivatives.max_leverage: 1`)
with the funding guard (`max_abs_funding_rate`) and `min_liquidation_distance_pct`
active. The gold slot trades **XAUUSDT** (perpetual); backtests proxy XAU to
`PAXGUSDT` (deep history on Global Binance) via
`strategy_params.backtest_data_proxy`.

The single live pair (XAU) runs CDC ActionZone **long + short** as a
stop-and-reverse (`allowed_sides: [long, short]`, `short_live_enabled: true`,
`enable_short: true`); live shorts execute because the swap adapter advertises
`swap` / `positions` / `reduce_only` capabilities (`xauby/api/ccxt_client.py`).
Order flow: entries place a **LIMIT** at the ticker (`execution.order_type: limit`)
and, when `execution.entry_market_fallback: true`, top up any unfilled remainder
with a **MARKET** order after the timeout instead of cancelling (keeps live in
parity with the market-fill backtest). Exits use MARKET on urgent triggers (CDC
red / NO_TRADE / force close) and LIMIT otherwise. The current XAU pair is
**CDC-pure** (`disable_stop_loss: true`), so there is no exchange-side stop —
exits are zone-flip driven, and sizing uses fixed-fraction `position_pct` of
equity rather than an SL-distance.

| Symbol | Mode | Strategy | Primary TF | Confirm TF | Sides |
|--------|------|----------|-----------|-----------|-------|
| `XAU` (XAUUSDT) | `live` | `cdc_action_zone` | `4h` | `1d` | `long` + `short` (backtest proxy `PAXGUSDT`) |

> Doc drift warning: this file and `README.md` reflect the current OKX swap
> baseline, but `README_DEV.md` and several files under `docs/` still describe the
> older Binance.th spot / multi-pair state. Treat `coin_whitelist.json` +
> `bot_config.yaml` as ground truth, and prefer updating stale docs when you
> touch the baseline.

**Router safety gate:** a pair with `mode: live` and `regime_router_enabled:
true` is forced to **sim** unless it also has `regime_router_live_confirmed:
true`. Never flip `regime_router_live_confirmed` without explicit per-pair
operator sign-off. NO_TRADE regimes (`PANIC_SELL`, `BEAR_BREAKDOWN`,
`BEAR_TREND_STRONG`) block new BUYs, keep stop protection, tighten trailing to
1x ATR, and can force-close after `force_close_candles`.

## Strategy + indicator plugins (the core extension pattern)

Strategies and their chart/legend indicators **travel together**. A PR that adds
a strategy without a matching indicator plugin and `strategy_chart_indicators`
mapping is incomplete — the dashboard would drift from real behavior.

**Strategy plugin** — `xauby/strategies/<name>/strategy.py`:
- Subclass `xauby.strategies.base.Strategy`, decorate with `@register("<name>")`.
- Implement `analyze(ctx: MarketContext) -> Signal`. Optionally
  `default_config()` and `validate_config()`.
- A Strategy is a pure analyst: it MUST NOT place orders, touch the DB, call
  Telegram, or call engine methods. The engine discovers plugins automatically
  via `load_strategy(name, config)` (config = `default_config() | yaml_config`).

**Indicator plugin** — `xauby/strategies/indicators/indicator_<name>.py`:
- Subclass `xauby.strategies.indicators.base.Indicator`, decorate with
  `@register("<name>")`.
- `compute(df, config)` appends named columns (no ANSI). `display_config` defines
  zones/lines/metrics/labels/colors. `snapshot()` / `panel_items()` optional.
- Feeds the terminal chart (`xauby/ui/chart.py`) and TUI legend/checklist.

**Wire-up checklist for a new strategy:**
1. Strategy plugin + `@register`.
2. Indicator plugin + `@register` (or reuse a compatible one).
3. `bot_config.yaml -> architecture.strategy_chart_indicators.<strategy>: [<indicators>]`
   (defaults also live in `indicators/registry.py:DEFAULT_STRATEGY_CHART_MAP`).
4. Tests: strategy test, indicator test, and chart legend coverage.

Production note: the `*_short` strategies (`donchian_short`, `supertrend_short`,
`rsi2_short`) and other R&D plugins (`rsi2_meanrev`, `vol_breakout`) are research
only (tagged `research`). The engine hard-blocks any `research`-tagged strategy
from a `live` pair (`_load_strategy_for_symbol`), so they are sim/backtest only —
never map them in `regime_router.mapping` for production. On the current OKX swap
baseline the XAU pair runs CDC ActionZone **long + short** (stop-and-reverse), so
the short path is **active** in live; the `research`-tagged `*_short` plugins still
stay sim/backtest only.

Shorts in general: a strategy emits `open_short`/`close_short`
(`xauby/strategies/signal.py`); the engine routes them to
`execute_open_short`/`execute_close_short` and is gated per-pair by
`allowed_sides` + `short_live_enabled` (live) and the swap adapter's
`capabilities`. `MarketContext.position_side` tells a strategy whether an open
position is LONG or SHORT so it can pick the right exit.

## Testing

Plain `unittest`, no extra runner. Always set `PYTHONPATH=.`.

```bash
# Full suite (65 modules)
PYTHONPATH=. python3 -m unittest discover -s tests -q

# Targeted architecture suite (fast confidence check)
PYTHONPATH=. python3 -m unittest \
  tests.test_chart_registry_path tests.test_indicator_display_adapter \
  tests.test_indicator_registry tests.test_sim_broker \
  tests.test_per_symbol_execution_mode tests.test_regime_router_mapping \
  tests.test_no_trade_handoff tests.test_confidence_filter -v
```

New plugins require strategy tests, indicator tests, and chart legend coverage.

## Backtest, optimization, and R&D

- Backtests pull from **Global Binance** (`api.binance.com`) by default for
  longer history (`backtest.data_base_url`, env override `BINANCE_BACKTEST_URL`);
  live trading uses the configured OKX endpoint.
- `python scripts/replay_backtest.py --symbol BTCUSDT --config bot_config.yaml`
- `python scripts/optimize_pair_configs.py` (low-CPU optimizer)
- `python scripts/select_pair_strategy.py --symbol BTCUSDT --candidates a,b,c`
  is dry-run; add `--apply` to write the best passing candidate to YAML.
- `python scripts/fetch_global_klines.py --symbol BTCUSDT --timeframe 1h --years 3`
- `python scripts/regime_strategy_eval.py --timeframe 1h --grid` — gatekeeper for
  strategy changes; see `docs/regime_strategy_selection.md`.
- Replay validation after restarts/incidents/config changes:
  `python scripts/replay_validate.py <run_id> --symbol XAUUSDT`.

## Architectural invariants (do not break)

- `observability` must not import `engine` (replay/health stay engine-agnostic).
- The TUI is read-only — it opens the DB with `LiteDB(readonly=True)` and never
  mutates trades.
- Exactly one engine instance; `core/.engine.lock` guards against doubles.
- Engine stays strategy-agnostic; signal/exit logic belongs to plugins/config.
- Strategy + indicator plugins ship together.

## Conventions

- Trading mode resolution: `--simulate`/`--live`/`--read-only` flags, YAML
  `simulate_only`/`read_only`, env `LIVE_TRADING`/`SIMULATE_ONLY`/`BOT_READ_ONLY`,
  and per-asset `mode`. Live trading requires `--live` AND `LIVE_TRADING=true` AND
  `simulate_only: false` AND per-asset `mode: live`. **Default to simulation.**
- `risk_pct` is kept aligned (currently `0.03`) across `trading`,
  `portfolio.position_sizing`, and per-symbol sizing. Keep these consistent — live
  reads `portfolio.position_sizing.risk_pct` while the backtest reads
  `trading.risk_pct`, so a mismatch breaks live/backtest parity. The startup
  guard `MAX_SANE_RISK_PCT = 0.10` rejects any `risk_pct > 10%`.
- Sizing is risk-based and auto-compounding: `qty = (equity × risk_pct) / sl_distance`,
  capped by `max_position_per_trade_pct` and per-symbol `allocation_pct`. Equity
  includes open positions at mark price, so profits grow the next position.
- `risk.drawdown_guard` is a portfolio kill-switch: blocks new BUYs (and
  force-closes if `close_positions: true`) when equity falls `max_drawdown_pct`
  below its persisted high-water mark (`core/equity_peak.json`).
- Runtime state (DB, logs, locks, sim balances, backtest cache) lives under
  `core/` and is gitignored; don't commit it. TUI screenshots use stable names in
  `docs/screenshots/*.svg`.
- Match surrounding code style; comments only for non-obvious constraints.

## Git workflow for this environment

- Develop on the designated feature branch; create it locally if missing.
- Commit with clear messages; push with `git push -u origin <branch>` (retry with
  exponential backoff on network errors).
- Do NOT open a pull request unless explicitly asked.
- Never push to a different branch without explicit permission.
