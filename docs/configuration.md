# Configuration

## File map

| File | Committed | Purpose |
|------|-----------|---------|
| `.env` | No | API keys, `LIVE_TRADING`, Telegram |
| `.env.example` | Yes | Template for new installs |
| `bot_config.yaml` | Yes | Engine, Strategy, Portfolio, notifications, retention, optimizer |
| `coin_whitelist.json` | Yes | Active assets, timeframes, per-pair strategy, execution mode, router gates |

## Source of truth model

Configuration is split into three levels:

| Level | Owns | Must not own |
|-------|------|--------------|
| Engine Config | Exchange, logging, monitoring, notifications, global risk guard, feature flags | Strategy signal thresholds or exit parameters |
| Strategy Config | RSI, ATR, volume filter, SL, trailing, breakeven, strategy timeframe, signal/exit logic | Exchange credentials or portfolio allocation |
| Portfolio Config | Position sizing, allocation, capital management | Indicator rules or signal thresholds |

Rule: every value that can change a backtest result must live under Strategy Config or Portfolio Config. Engine code should be strategy-agnostic.

### Canonical runtime view

Platform-facing tools should read `canonical_runtime_config(cfg)` from
`xauby.runtime.trading_config` instead of re-parsing legacy YAML blocks. It
returns a read-only snapshot with `exchange_id`, `quote_asset`, `simulate_only`,
`read_only`, per-symbol strategy config, and per-symbol portfolio config.

The live engine still uses `resolve_trading_config()` internally; the canonical
view is additive and intended for the web console, marketplace validation, and
config inspection.

### Exchange block

`bot_config.yaml -> exchange:` is the source of truth for exchange selection and
exchange-wide economics. `xauby.runtime.exchange_config` exposes the resolvers
used everywhere so live, sim, backtest, and replay stay in parity:

| Key | Resolver | Notes |
|-----|----------|-------|
| `provider` | `resolve_provider(cfg)` | `binance` (native client) or `ccxt` (generic adapter). Drives `create_exchange_client()`. |
| `quote_asset` | `resolve_quote_asset(cfg)` | Default quote currency (e.g. `USDT`, `THB`). Whitelist/portfolio `quote_asset` still win when set. The live engine reads cash balances and sizes orders against this asset (`_quote_asset()` / `LiveBroker.get_quote_balance`). Note: internal state/dashboard labels are still literally `USDT`; the displayed value is correct but the label is cosmetic pending a UI pass. |
| `fee_pct` | `resolve_fee_pct(cfg)` | Taker fee fraction (`0.001` = 0.1%). Precedence: `exchange.fee_pct` → `backtest.fee_pct` → `trading.fee_pct` → `0.001`. Per-symbol `sim_fee_pct` (whitelist) overrides for that pair. |
| `api_key_env` / `api_secret_env` / `base_url_env` | `resolve_exchange_credentials(cfg)` | Names of the env vars holding the credentials. Default to `<PREFIX>_API_KEY` etc. where `<PREFIX>` derives from `ccxt_id`/`provider`/`name` (so `binance`→`BINANCE_*`, `kraken`→`KRAKEN_*`). The engine, health probe, and gateway factory all read through this one resolver instead of hardcoding `BINANCE_*`. |

Do not hardcode the fee or quote asset in new code — go through these resolvers
so a backtest result cannot diverge from live.

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OKX_API_KEY` | - | Active OKX REST / WebSocket API key |
| `OKX_API_SECRET` | - | Active OKX API secret |
| `OKX_API_PASSPHRASE` | - | Active OKX API passphrase |
| `LIVE_TRADING` | `false` | Additional gate for real orders |
| `SIMULATE_ONLY` | from YAML | CLI `--live` / `--simulate` override |
| `BOT_READ_ONLY` | `false` | Skip order placement |
| `TELEGRAM_ENABLED` | `false` | Master switch for alerts |
| `TELEGRAM_BOT_TOKEN` | - | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | - | Authorized chat for commands |
| `DEFAULT_SYMBOL` | - | Optional single-pair override |
| `XAUBY_DEFAULT_SYMBOL` | `XAUTUSDT` | Legacy dashboard fallback when the whitelist is empty/unreadable; the current whitelist supplies `XAUUSDT` and `BTCUSDT` |
| `XAUBY_HOME` | `core` | Base directory for runtime data (DB, lock, sim balances). Relocate to run instances side by side |
| `XAUBY_INSTANCE_ID` | - | Optional sub-namespace under the runtime root (e.g. a tenant/account id) for multi-instance isolation |

`BINANCE_API_KEY`, `BINANCE_API_SECRET`, and `BINANCE_WS_URL` are retained only
for the optional legacy `provider: binance` plugin; they are not used by the
committed OKX baseline.

### Regime macro weights

The market-regime classifier combines DXY, rates, and news into a macro bias.
The weights are configurable so the classifier is not hardwired to gold:

```yaml
macro_sentiment_guard:
  macro_weights:   # defaults shown (gold thesis)
    news: 1.0
    dxy: -1.0      # USD strength bearish for gold; flip for a USD-correlated asset
    fred: -1.0     # high rates bearish for gold
```

## Current pair baseline

| Symbol | Mode | Strategy | Primary TF | Confirm TF | Sides | Leverage | Router | Notes |
|--------|------|----------|------------|------------|-------|----------|--------|-------|
| `XAUUSDT` | `live` | `xauby_actionzone` | `4h` | `1d` | Long + short | 1x | Off | `PAXGUSDT` backtest proxy |
| `BTCUSDT` | `live` | `supertrend_ema200` | `4h` | none | Long + short | 1x | Off | Certified OKX 4H preset |

`risk_pct` is intentionally kept at `0.02` (2%) in trading, portfolio, and
per-symbol sizing. `trading.max_open_positions`, `risk.max_open_positions`, and
`portfolio.max_open_positions` are all `2` so neither live pair is silently
blocked by a lower concurrency cap.

## Partial take-profit

Strategy configs may enable a one-shot partial take-profit:

```yaml
strategy:
  params:
    cdc_action_zone:
      partial_tp_pct: 12.0
      partial_tp_fraction: 0.5
```

When an open position reaches `partial_tp_pct` return from entry, the engine
closes `partial_tp_fraction` of the remaining position with a reduce-only close
on live swaps (or the SimBroker in sim), records a partial closed trade, marks
`trade_states.partial_tp_taken=1`, and leaves the rest to the normal strategy
exit. The trigger is one-shot per position and persists across restarts.

For the current XAU live baseline this banks 50% at +12% and lets the remainder
ride until CDC exits. UI surfaces show the target as `PTP`, `Partial TP`, or
`PTP banked` once taken.

## Whitelist schema

```json
{
  "version": 1,
  "quote_asset": "USDT",
  "assets": [
    {
      "symbol": "SOL",
      "enabled": true,
      "primary_timeframe": "1h",
      "confirm_timeframe": "",
      "strategy": "supertrend_ema200",
      "mode": "sim",
      "allowed_sides": ["long"],
      "leverage": 1.0,
      "short_live_enabled": false,
      "regime_router_enabled": true,
      "regime_router_live_confirmed": false,
      "sim_fee_pct": 0.1
    }
  ]
}
```

| Field | Values | Notes |
|-------|--------|-------|
| `symbol` | base asset such as `BTC` | Normalizes to `BTCUSDT` using `quote_asset` |
| `enabled` | bool | Disabled assets are ignored |
| `primary_timeframe` | e.g. `1h`, `4h` | Used by live, backtest, and chart |
| `confirm_timeframe` | e.g. `1d` or empty | Strategy dependent |
| `strategy` | plugin id | Required when `architecture.whitelist_strict: true` |
| `strategy_params` | object | Per-pair overrides merged into strategy config |
| `mode` | `sim`, `live` | Requires `per_symbol_execution_mode: true` |
| `allowed_sides` | list of `long`, `short` | Explicit position sides allowed for the pair |
| `leverage` | number | Pair leverage; committed live pairs use 1x |
| `short_live_enabled` | bool | Additional fail-closed gate required before live short orders |
| `regime_router_enabled` | bool | Pair-level RegimeRouter gate |
| `regime_router_live_confirmed` | bool | Required before a live pair may route strategies |
| `sim_fee_pct` | number | Percent, e.g. `0.1` = 0.1% |

`data.pairs` in YAML is derived from enabled whitelist assets when `architecture.sync_yaml_pairs_from_whitelist: true`.

## Architecture flags

Current committed state:

```yaml
architecture:
  whitelist_strict: true
  indicator_registry_enabled: true
  sync_yaml_pairs_from_whitelist: true
  tui_indicator_registry: true
  per_symbol_execution_mode: true
  sim_broker_enabled: true
  regime_router_enabled: true
  regime_confidence_filter: true
  event_bus_enabled: true
  strategy_chart_indicators:
    xauby_actionzone: [xauby_actionzone]
    supertrend_ema200: [supertrend, ema200]
    btc_ema_pullback: [btc_ema_pullback]
    ict_lite_strategy: [ict_lite]
    bbkc_squeeze: [bbkc_squeeze]
    bbrsi_mean_reversion: [bbrsi_mean_reversion]
```

| Flag | Purpose |
|------|---------|
| `whitelist_strict` | Whitelist-only pair/strategy source with fail-fast validation |
| `indicator_registry_enabled` | Chart uses `IndicatorRegistry` instead of hard-coded CDC lines |
| `sync_yaml_pairs_from_whitelist` | Keeps `data.pairs` synced from whitelist assets |
| `tui_indicator_registry` | TUI checklist/legend comes from registry adapter |
| `per_symbol_execution_mode` | Allows each whitelisted pair to select `sim` or `live` independently |
| `sim_broker_enabled` | Sim orders use persistent SimBroker ledger |
| `regime_router_enabled` | Master RegimeRouter switch |
| `regime_confidence_filter` | Optional confidence gate after enough history exists |
| `event_bus_enabled` | In-process subscribers receive emitted observability events |

Rollback: set the relevant flag to `false`. No code revert is required.

## RegimeRouter

```yaml
regime_router:
  debounce_candles: 3
  recovery_candles: 3
  force_close_candles: 6
  confidence_threshold: 0.65
  mapping:
    BULL_BREAKOUT: xauby_donchian_trend
    BULL_TREND_STRONG: xauby_donchian_trend
    BULL_TREND_WEAK: xauby_donchian_trend
    LOW_VOL_ACCUMULATION: bbrsi_mean_reversion
    LOW_VOL_RANGE: bbrsi_mean_reversion
    VOLATILITY_EXPANSION: xauby_donchian_trend
    SIDEWAYS_CHOP: bbrsi_mean_reversion
    BEAR_TREND_WEAK:
    PANIC_SELL:
    BEAR_BREAKDOWN:
    BEAR_TREND_STRONG:
```

Empty mapping values mean `None` / NO_TRADE.

Safety behavior:

- Global `architecture.regime_router_enabled` and per-asset `regime_router_enabled` must both be true.
- Live routing additionally requires per-asset `regime_router_live_confirmed: true`.
- Without live confirmation, the engine forces that pair to sim.
- NO_TRADE blocks new BUY entries, tightens trailing SL to 1x ATR, and can force close after `force_close_candles`.
- Strategy handoff keeps an open position on the old strategy until it closes.

## Strategy chart indicators

Chart zones, chart lines, TUI legend, and checklist text come from indicator plugin metadata.

| Strategy | Zones | Lines |
|----------|-------|-------|
| `cdc_action_zone` | Blue / Green / Red / Neutral | EMA12, EMA26 |
| `supertrend_ema200` | ST Bull / ST Bear / Neutral | SuperTrend, EMA200 |
| `bbkc_squeeze` | Compressed / Breakout / Neutral | Bollinger Bands, Keltner Channels |
| `bbrsi_mean_reversion` | Oversold / Neutral / Overbought | Bollinger Bands |
| `btc_ema_pullback` | Trend / Pullback / Reclaim / Neutral | EMA Fast, EMA Slow, EMA Trend |
| `ict_lite_strategy` | Sweep Low / Reclaim / MSS / Neutral | EMA Fast, EMA Slow, Recent High, Recent Low |
| `rsi2_meanrev` | Buy Setup / Oversold / Exit / Neutral | EMA200, SMA5 |
| `vol_breakout` | Breakout / ATR Expansion / Neutral | Range High, Exit EMA |

If a strategy has no indicator plugin or no mapping, chart/legend parity is considered incomplete.

## Risk alignment checklist

These should be consistent before live trading:

| Key | Current / typical value |
|-----|--------------------------|
| `trading.risk_pct` | `0.02` |
| `portfolio.position_sizing.risk_pct` | `0.02` |
| `portfolio.symbols.<SYMBOL>.position_sizing.risk_pct` | `0.02` |
| `trading.max_position_per_trade_pct` | `25.0` |
| `portfolio.symbols.<SYMBOL>.position_sizing.max_position_per_trade_pct` | `25.0` |

Global risk guards still apply across all symbols. Per-symbol strategy/risk state is isolated; portfolio capital is shared by design.

## Backtest and optimizer

Backtest commands:

```bash
python scripts/replay_backtest.py --symbol XAUUSDT --config bot_config.yaml
python scripts/replay_backtest.py --symbol BTCUSDT --config bot_config.yaml
```

Low-CPU optimizer:

```bash
python scripts/optimize_pair_configs.py
```

Strategy selection:

```bash
python scripts/select_pair_strategy.py --symbol BTCUSDT --candidates cdc_action_zone,supertrend_ema200,bbkc_squeeze,bbrsi_mean_reversion
python scripts/select_pair_strategy.py --symbol BTCUSDT --candidates cdc_action_zone,supertrend_ema200,bbkc_squeeze,bbrsi_mean_reversion --apply
```

Use dry-run first. `--apply` writes the best passing candidate to YAML.

## Notifications block

```yaml
notifications:
  alert_channel: telegram
  send_alerts: true
  notify_position_updates: true
  notify_guard_blocks: true
  notify_regime_changes: true
  regime_score_threshold: 10
  telegram_command_polling_enabled: true
```

## Scheduled reports

```yaml
daily_digest:
  enabled: true
  hour_utc: 10
  send_telegram: true

weekly_review:
  enabled: true
  day_of_week: 6
  hour_utc: 17
  period_days: 7
```

With multiple active pairs, messages use portfolio totals plus per-pair lines. See [telegram.md](telegram.md).

## Hot reload

```yaml
data:
  hybrid_dynamic_coin_config:
    hot_reload_enabled: true
    reload_interval_seconds: 30
    require_supported_market: true
```

Unsupported pairs are dropped after `exchangeInfo` validation.

## Quick Config menu (launcher)

The launcher menu → **Quick Configuration** (or `xauby --config`) opens a native
Textual config editor: a grouped `OptionList` hub whose categories push submenus
of toggles, number+range modals, and pickers (arrow/Enter/mouse; secrets are
masked). It lives in `xauby/ui/textual_tui/quick_config/` (a declarative field
schema over the same writers). Writes still go through the `ruamel.yaml`
round-trip (`_edit_bot_yaml` / `_set_yaml_path` in `xauby/launcher/config_io.py`),
so **comments and structure in `bot_config.yaml` are preserved** across edits.
The legacy terminal editor remains available via `XAUBY_CONFIG_TERMINAL=1`.

| Group | Edits |
|-------|-------|
| Trading & Risk | mode (sim/live), per-pair risk percentage, SL ATR, breakeven, D1 filter, strategy |
| Global Trading | `trading.max_open_positions`, `interval_seconds`, `timeframe`, `min_order_amount`, `max_position_per_trade_pct` |
| Risk Guards | `risk.drawdown_guard.{enabled,max_drawdown_pct,close_positions}`, `max_daily_loss_pct`, `max_consecutive_losses` |
| Portfolio | `portfolio.initial_balance`, per-symbol `allocation_pct` |
| Strategy Params | per-pair `rsi_min/max`, `vol_min_ratio`, `trailing_atr_mult`, `cool_down_minutes` (strict mode → whitelist `strategy_params`; legacy mode → `strategy.symbols.<SYM>`) |
| Regime Router | enable, `confidence_threshold`, debounce/recovery/force-close candles, instant-switch, and the regime→strategy mapping (pick from registered strategies — no ids to type) |
| Multi-Pair & Dashboard | timeframes, pairlist, dashboard focus |
| Telegram Alerts & Schedule | Telegram credentials/test (`.env`), plus `weekly_review`/`daily_digest` scheduling, `notifications.regime_score_threshold`, `semi_auto_confirm_timeout_seconds` |
| Macro Guard | sentiment guard, FRED, AI news |
| Exchange | guided **switch-exchange wizard**, `exchange.{provider,ccxt_id,quote_asset,fee_pct}`, **per-exchange API credentials** (writes the right `.env` var via `credential_env_names`), **test connection**, and `exchange_plugin_registry_enabled` |
| System Features | architecture flags for per-symbol execution and the event bus |

Notes:

- **Switching exchange is fully menu-driven:** the Exchange wizard sets
  provider/ccxt_id/quote, prompts for that exchange's credentials (e.g.
  `KRAKEN_API_KEY`), and runs a connection test — no hand-editing `.env`.
- Editing `risk_pct` writes all three aligned keys (`trading.risk_pct`,
  `portfolio.position_sizing.risk_pct`, per-symbol) as fractions, and the legacy
  `risk.max_risk_per_trade_pct` in percent — keeping live/backtest parity.
- With `architecture.whitelist_strict: true`, per-pair strategy selection and
  strategy parameters are written to `coin_whitelist.json`, which is the runtime
  source of truth. Legacy mode continues to write `strategy.symbols.<SYMBOL>`.
- All groups except pairlist membership change engine-init-only config, so the
  menu offers a restart when the engine is running. Pairlist changes hot-reload.
