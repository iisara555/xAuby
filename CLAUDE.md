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

Product identity ("xAuby : Alternative Store of Value Trading System") lives in
`xauby/meta.py`; the operator display name is configurable via
`bot_config.yaml -> cli_ui`, not a hardcoded string to templatize. The legacy
read-only WebUI (`xauby/webui`), the `saas-web/` SPA, and their design docs
(PRODUCT.md / DESIGN.md) have been removed — the web surface is the Next.js
Pilot Workspace in `Website/` backed by `xauby/saas`.

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
tests/              139 unittest modules (test_*.py), ~1067 tests
docs/               Operator/contributor docs (architecture, trading-flow, configuration, tui, telegram)
weekly_reviews/     Generated weekly review markdown
xauby/
  cli.py            CLI argument handling + restart/update logic
  meta.py           Product identity (PRODUCT_NAME/TAGLINE, bot_config.yaml cli_ui.bot_name resolution) — read by CLI, TUI, Telegram, and engine alerts
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
`regime_router_enabled`, `regime_confidence_filter`,
`regime_statistical_crosscheck`, `event_bus_enabled`.
Rollback is intentionally a flag flip to `false` — no code revert required.

`regime_statistical_crosscheck` (default off) fits an independent
Gaussian-mixture regime model each tick (`xauby/regime/statistical.py`) and
attaches its label / posterior / agreement to the rule-based regime as `stat_*`
features. It is advisory only — it never overrides the rule-based regime or
routing — so it is safe to arm for observation. Tune via the optional top-level
`regime_statistical:` block (`components`, `min_bars`).

## Current runtime baseline (from coin_whitelist.json / bot_config.yaml)

Exchange is **OKX perpetual swap** (`exchange.provider: ccxt`, `ccxt_id: okx`,
`name: okx`, `market_type: swap`, `margin_mode: isolated`, `position_mode:
one_way`) via the CCXT adapter, at **1x leverage** (`derivatives.max_leverage: 1`)
with the funding guard (`max_abs_funding_rate`) and `min_liquidation_distance_pct`
active. The gold slot trades **XAUUSDT** and the crypto slot **BTCUSDT** (both
perpetual); backtests proxy XAU to `PAXGUSDT` (deep history on Global Binance)
via `strategy_params.backtest_data_proxy`.

**Two pairs run live** (`max_open_positions: 2`, `data.pairs: [XAUUSDT,
BTCUSDT]`) at 1x with the regime router off (`regime_router_enabled: false`) on
both — but they no longer share a side policy. **XAU is long-only** since the
2026-07-29 rollout (`allowed_sides: [long]`, `short_live_enabled: false`,
`enable_short: false`); **BTC is long + short** (`allowed_sides: [long, short]`,
`short_live_enabled: true`, `enable_short: true`). BTC's live shorts execute
because the swap adapter advertises `swap` / `positions` / `reduce_only`
capabilities (`xauby/api/ccxt_client.py`).

| Symbol | Mode | Strategy | Primary TF | Confirm TF | Sides | Stop |
|--------|------|----------|-----------|-----------|-------|------|
| `XAU` (XAUUSDT) | `live` | `xauby_actionzone` | `4h` | `1d` (gates entries) | `long` only (backtest proxy `PAXGUSDT`) | none — CDC-pure |
| `BTC` (BTCUSDT) | `live` | `supertrend_ema200` | `4h` | — | `long` + `short` | ATR (`sl_atr_mult: 3.0`) |

The two pairs are deliberately **not** configured alike, and the difference
matters when you touch sizing or exits:

- **XAU is CDC-pure** (`disable_stop_loss: true`) — there is no exchange-side
  stop, and sizing uses fixed-fraction `position_pct: 0.95` of equity rather than
  an SL-distance. Exits are the zone flip plus the **`minimal_roi` ladder**
  (`{"0": 8.0, "1440": 5.0, "4320": 3.0}` — take +8% from entry, settle for +5%
  after a day, +3% after three). The ladder is part of what every XAU
  certificate has measured, so **changing it re-opens certification**. XAU has
  **no partial TP**: the keys were removed once it was proven they could never
  fire beneath an 8% ladder — `validate_exit_config` (`xauby/runtime/exits.py`)
  now refuses that combination at startup.
- **XAU's D1 gate is on for both sides** (2026-07-29): `use_d1_regime_filter`,
  `_long` and `_short` are all `true`, so the daily zone gates every entry.
  Because the pair is also long-only, `_short` is inert — it is set anyway so
  the shape is unambiguous. `xauby_actionzone` supports per-side gating via
  `use_d1_regime_filter_long` / `_short` (both default to `None` = follow the
  shared flag); the asymmetric `L:D1off S:D1on` shape that shipped on
  2026-07-26 was replaced by this one and is no longer deployed.
  Note the D1 gate also controls whether the engine loads 1d candles at all
  (`SymbolContext.timeframe_regime`), so turning it off stops that fetch.
- **XAU is certified, under a narrow gate.** Certificate fingerprint
  `6b01b6f2598f3881` (`xauby/saas/certificates/okx-xau-actionzone-v1.json`,
  `docs/research/xau_long_only_d1_certificate_2026-07-29.md`) records PF 2.18 /
  net +80.72% / MDD 8.48% over 102 trades on 4.0y of OKX XAUT-USDT. Read the
  scope before citing it: `backtest.acceptance`'s edge test is marked
  `applies: false` here because that test measures the short side and this
  preset does not trade one, so the config cleared only "net positive". The
  profile was picked from a 432-cell search on the same four years, with no
  pristine holdout and no forward record, and the native XAU swap check covers
  1.3 years. It is evidence with real selection uncertainty, not an all-weather
  guarantee. Do not "fix" a future acceptance failure by loosening
  `min_profit_edge_pp` — that threshold is the pre-registered bar the whole XAU
  investigation rests on.
- **BTC keeps a real stop** (`sl_atr_mult: 3.0`, `trailing_atr_mult: 2.0`,
  `breakeven_sl_enabled: true`), so it takes the normal risk-based sizing path
  (`qty = equity × risk_pct / sl_distance`) and exits on SuperTrend flip or EMA200
  loss. No ROI ladder, no partial TP.

Order flow (both pairs): entries place a **LIMIT** at the ticker
(`execution.order_type: limit`) and, when `execution.entry_market_fallback:
true`, top up any unfilled remainder with a **MARKET** order after the timeout
instead of cancelling (keeps live in parity with the market-fill backtest). Exits
use MARKET on urgent triggers (zone flip / NO_TRADE / force close) and LIMIT
otherwise.

> `coin_whitelist.json` + `bot_config.yaml` are the ground truth for the pair
> table above — update this file whenever they change. Binance.th references
> under `docs/` are **not** drift: the native `LiteBinanceClient` gateway still
> ships and `binance-th-spot` is still a live-certified target in the SaaS
> catalog (`xauby/saas/catalog.py`), so those docs are describing real support.

**A SaaS preset's verdict is derived, never typed.** `xauby/saas/preset_specs.py`
holds the hand-authored half (identity, `execution_profile`, and `live_certified`
— approval is genuinely an operator's call). `certification_status`,
`certification_note` and `backtest` come from
`xauby/saas/certificates/<preset_id>.json`, emitted by
`scripts/certify_preset.py`; a spec that declares any of them raises at import.
Each record fingerprints the config it measured, so editing `execution_profile`
revokes the certificate — and if the preset is also `live_certified`, the catalog
refuses to build until someone re-certifies or adds an `operator_override`
(`decided_by` / `decided_at` / `reason`). Approving something that failed the
gate is allowed; XAU does it. Doing so silently is not.

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

**Maturity gates live.** `Strategy.maturity` is `research | paper | production`,
declared on the plugin (`xauby/strategies/base.py`). The engine refuses to run
anything that is not `production` on a `mode: live` pair — and **undeclared is
not production**, so a new plugin fails closed. Only `xauby_actionzone` and
`supertrend_ema200` are declared production today; both trade real money. Legacy
`research` / `paper-test` tags still resolve, so existing plugins are unchanged.
Adding a live pair on any other strategy will refuse to start, which is the
point: promote a plugin by certifying it, not by editing a whitelist.

Production note: the `*_short` strategies (`donchian_short`, `supertrend_short`,
`rsi2_short`) and other R&D plugins (`rsi2_meanrev`, `vol_breakout`) are research
only (tagged `research`). The engine hard-blocks any `research`-tagged strategy
from a `live` pair (`_load_strategy_for_symbol`), so they are sim/backtest only —
never map them in `regime_router.mapping` for production. On the current OKX swap
baseline the short path is **active on BTC only** (`supertrend_ema200`); XAU went
long-only on 2026-07-29, so `xauby_actionzone` no longer stops and reverses in
production. The `research`-tagged `*_short` plugins remain sim/backtest only.
(`cdc_action_zone` is a legacy alias for `xauby_actionzone`, resolved in
`STRATEGY_ID_ALIASES`.)

Shorts in general: a strategy emits `open_short`/`close_short`
(`xauby/strategies/signal.py`); the engine routes them to
`execute_open_short`/`execute_close_short` and is gated per-pair by
`allowed_sides` + `short_live_enabled` (live) and the swap adapter's
`capabilities`. `MarketContext.position_side` tells a strategy whether an open
position is LONG or SHORT so it can pick the right exit.

## Testing

Plain `unittest`, no extra runner. Always set `PYTHONPATH=.`.

The full suite and frontend build run on standard GitHub-hosted Ubuntu runners.
Never run them on the 1 vCPU / 2 GB trading VPS. On the VPS, run only the
targeted tests for the files changed and leave the full gate to the PR workflow.

```bash
# Full suite (139 modules)
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

Compute-heavy backtests, grids, and optimizers run only on GitHub-hosted Actions
or directly on a capable workstation. Do not run them on the trading VPS. The
BTC SuperTrend grid is manually dispatched from Actions and uses multiprocessing
in one job, capped by GitHub's six-hour hosted-job limit. CI and research runner
policy is documented in `docs/github-hosted-actions.md`.

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
- Where a pair enables partial TP, the TUI and the Pilot Workspace surface it as
  `PTP` / `Partial TP` with `pending` or `banked` state from the exported
  position; both hide the field when `partial_tp_pct` is 0. No current pair
  enables it — see the XAU note above.
- A partial TP under a `minimal_roi` rung is unreachable, because a full exit
  always wins the tick on both the live and replay paths. `validate_exit_config`
  enforces this at startup; do not "fix" it by raising the ladder, which changes
  the strategy.
- Strategy + indicator plugins ship together.

## Conventions

- Trading mode resolution: `--simulate`/`--live`/`--read-only` flags, YAML
  `simulate_only`/`read_only`, env `LIVE_TRADING`/`SIMULATE_ONLY`/`BOT_READ_ONLY`,
  and per-asset `mode`. Live trading requires `--live` AND `LIVE_TRADING=true` AND
  `simulate_only: false` AND per-asset `mode: live`. **Default to simulation.**
- `risk_pct` is kept aligned (currently `0.02`) across `trading`,
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
- Check local disk space (`df -h`) before heavy runs — backtest data fetch,
  optimizer sweeps, `regime_strategy_eval.py --grid`, full test suite. Backtest
  cache + JSONL logs under `core/` fill disk fast and aren't gitignored from
  disk usage, just from git.

## Git workflow for this environment

**`AGENTS.md` holds the shared multi-agent rules — read it before pushing.** It is
the single source of truth for both Claude and Codex; the essentials:

- `main` is what CI validates, what Vercel deploys, **and what the live trading
  engine pulls and restarts on** (`scripts/deploy_from_github.sh` defaults to it).
- **Never force-push `main`** — the VPS deploys with `git merge --ff-only`, so a
  rewritten history makes deployment refuse to proceed.
- One branch, one agent. Prefix Claude's work `claude/*`.
- On the VPS, never edit `/opt/xauby/current`; it is the active release symlink.
  Work in a separate clone and change production only through a controlled,
  rollback-capable deployment.
- VPS deploys are manual and position-aware. Vercel deploys from `main` on its own.
- A live tracked position may stay open during a controlled restart: the restart
  preflight permits positions represented in DB/state and blocks untracked or
  ambiguous exchange state. Exposure does not require waiting for flat when the
  operator explicitly authorizes the deploy, backups exist, preflight reports
  `SAFE`, and the post-restart exchange-versus-state reconciliation confirms the
  same symbol, side, quantity, and entry price with no unexpected open orders.
- On the SaaS/systemd host, activate an atomic release and restart
  `xauby-control.service` plus the affected `xauby-engine@<tenant>.service`.
  Do not use the checkout-scoped restart script to launch a parallel engine;
  keep the previous release/config as rollback targets and roll back if service,
  health, or reconciliation checks fail.
- Treat `scripts/audit_release_readiness.py` as a fail-closed deployment gate.
  Run its static checks against `/etc/xauby/tenants/<tenant>/`, then its runtime
  DB/replay checks after restart. `replay_validate.py` now exits non-zero when
  every signal was skipped; use `--require-short` for the Phase-0 proof.

### Production config is NOT in the repo checkout

Deploying code and changing config are **two separate operations against two
separate locations**. Editing `bot_config.yaml` or `coin_whitelist.json` in the
repo changes nothing in production, no matter how many times you deploy.

| what | where |
|------|-------|
| code | `/opt/xauby/current` → active release symlink |
| **config** | **`/etc/xauby/tenants/<tenant>/`** (`bot_config.yaml`, `coin_whitelist.json`, `secrets.env`) |
| runtime data | `XAUBY_HOME` (+ `XAUBY_INSTANCE_ID`), default `core/` |

The mechanism is `config_root()` in `xauby/runtime/paths.py`: it returns
`XAUBY_CONFIG_DIR` (or the cwd), and `PairRegistry` **joins
`whitelist_json_path` onto it** — so a tenant reads its own whitelist from its
own directory. `runtime_root()` relocates mutable data independently.

Consequences that have already caused incidents:

- **One strategy key can live in four places**: repo YAML, repo whitelist, tenant
  YAML (often *twice* — under `strategy.config.<id>` and again under
  `mode_indicator_profiles`), and the tenant whitelist, which wins at runtime.
  Changing a subset is how a value silently fails to take effect, or takes effect
  for one code path only.
- **A startup guard written against repo config is enforced against tenant
  config.** `validate_exit_config` raises inside `LiteTradingEngine.start()`
  before either lock is acquired, so a guard that passes locally and fails on the
  tenant's config converts a routine deploy into an outage. Check the tenant
  files *before* shipping a guard, not after.
- Tenant files carry their own owner and ACLs (`xauby-owner-<tenant>`). Back them
  up with `cp -p` and edit in place; overwriting them with a copy from the repo
  destroys the ownership.

### Two locks, and only one of them is cross-checkout

- `core/.engine.lock` — scoped to a **checkout**. It does not see an engine
  started from a different directory.
- `/var/lib/xauby/account_locks/account_<hash>.lock` — scoped to an **exchange
  account**, and this is the one that actually protects capital. A second live
  engine started from a work clone fails closed here ("Another LIVE xAuby
  instance is already trading this exchange account") within seconds, before any
  order is placed. Confirmed 2026-07-27.

To verify a config change actually reached the strategy, read the status line
rather than assuming: `D1: OFF` means the gate is inactive, `D1: UNKNOWN` means
the 1d frame has not arrived yet (shorts blocked — fail-safe), and a real zone
(`RED`/`GREEN`/…) means it is live.

Then, for this environment specifically:

- Develop on the designated feature branch; create it locally if missing.
- Commit with clear messages; push with `git push -u origin <branch>` (retry with
  exponential backoff on network errors).
- Do NOT open a pull request unless explicitly asked.
- Never push to a different branch without explicit permission.
