# xAuby Developer Guide

## Current architecture state

The committed baseline is OKX USDT-settled perpetual swap via CCXT, single
live pair (XAU), long-only, 1x leverage. The current runtime uses:

| Capability | Current state |
|------------|---------------|
| Whitelist source of truth | Enabled with `architecture.whitelist_strict: true` |
| Indicator registry | Enabled for terminal chart and TUI legend |
| Per-symbol execution mode | Enabled so each pair can be `sim` or `live` |
| SimBroker | Enabled for per-symbol sim orders |
| RegimeRouter | Globally enabled, but gated per asset — XAU keeps it off |
| Event bus | Enabled |
| Regime confidence filter | Enabled after a 30-sample warmup |
| Statistical regime crosscheck | Disabled (advisory-only when armed) |

Current pair state (`coin_whitelist.json`):

| Symbol | Mode | Strategy | Router |
|--------|------|----------|--------|
| `XAU` (XAUUSDT) | `live` | `xauby_actionzone` (CDC Action Zone V3) | Off |

A live pair cannot use RegimeRouter unless the asset has both `regime_router_enabled: true` and `regime_router_live_confirmed: true`. Without live confirmation the engine forces that pair to sim and emits an operator warning.

A separate multi-tenant SaaS control plane (`xauby/saas/`) runs isolated
copies of this same engine for other operators, invite-only. It is not part
of this single-owner architecture state — see the root
[README.md#saas-control-plane](README.md#saas-control-plane).

## Strategy + indicator plugin pairing

Every new Strategy Plugin under `xauby/strategies/<name>/` must ship a corresponding Indicator Plugin under `xauby/strategies/indicators/indicator_<name>.py` or explicitly reuse an existing compatible indicator plugin.

The indicator plugin feeds:

- Terminal chart rendering (`xauby/ui/chart.py`)
- TUI dashboard panels (`xauby/ui/dashboard.py`, Textual widgets)
- Legend and color metadata via `display_config`

PRs that add a strategy without chart/legend metadata should be treated as incomplete because the dashboard would drift from the actual strategy.

## Indicator plugin contract

Implement `xauby/strategies/indicators/base.Indicator`:

- `compute(df, config) -> pd.DataFrame` appends named columns; no ANSI in plugins
- `display_config` defines zones, lines, metrics, labels, colors, and optional thresholds
- `snapshot()` and `panel_items()` are optional overrides; defaults derive from `display_config`

Register with `@register("plugin_name")` in `xauby/strategies/indicators/registry.py`.

Wire chart mapping in `bot_config.yaml`:

```yaml
architecture:
  strategy_chart_indicators:
    my_strategy: [my_strategy]
```

Current mappings:

| Strategy | Indicator plugin list |
|----------|-----------------------|
| `xauby_actionzone` (CDC Action Zone) | `[xauby_actionzone]` |
| `xauby_donchian_trend` | `[xauby_donchian_trend]` |
| `supertrend_ema200` | `[supertrend, ema200]` |
| `btc_ema_pullback` | `[btc_ema_pullback]` |
| `ict_lite_strategy` | `[ict_lite]` |
| `bbkc_squeeze` | `[bbkc_squeeze]` |
| `bbrsi_mean_reversion` | `[bbrsi_mean_reversion]` |

## Architecture feature flags

All flags live under `bot_config.yaml` -> `architecture:`. Routing rules live under `regime_router:`.

| Flag | Purpose |
|------|---------|
| `whitelist_strict` | Whitelist is sole pair/strategy source; fail-fast validation |
| `strategy_sandbox_strict` | Reject (vs. warn) plugins that fail the static AST sandbox scan |
| `strategy_validate_strict` | Reject (vs. warn) a strategy with non-empty `config_errors()` at start |
| `indicator_registry_enabled` | Chart computes indicators via `IndicatorRegistry` |
| `tui_indicator_registry` | TUI checklist/legend from `IndicatorDisplayAdapter` |
| `per_symbol_execution_mode` | Per-asset `mode: sim|live` plus Sim/Live PnL split |
| `sim_broker_enabled` | Route sim orders through `SimBroker` |
| `regime_router_enabled` | Master switch for RegimeRouter; per-asset flag also required |
| `regime_confidence_filter` | Block regime switches when confidence is below threshold after 30 history rows |
| `regime_statistical_crosscheck` | Fit an independent Gaussian-mixture regime model each tick; advisory only, never overrides routing |
| `event_bus_enabled` | Dispatch `EventEmitter` events to in-process subscribers |
| `api_circuit_breaker_enabled` | Wrap outbound exchange REST calls in a token-bucket rate limiter + circuit breaker |

Rollback is intentionally simple: set a flag to `false` without reverting code. For router rollback, disable either the global `architecture.regime_router_enabled` flag or the per-asset `regime_router_enabled` field.

## Safe rollout and promotion

Adding a second live/sim pair follows the same pattern the codebase already
supports (per-symbol `mode`, isolated `SymbolContext`, RegimeRouter gating):

1. Add the pair to `coin_whitelist.json` with `mode: sim` first.
2. Review the SimBroker ledger, trade frequency, PnL, regime switch history, and replay validation.
3. Keep `regime_confidence_filter` conservative until at least 30 useful `regime_history` rows exist for the new pair.
4. If router behavior is acceptable, set that pair's `regime_router_live_confirmed: true` only after explicit operator sign-off.
5. Do not touch the XAU live pair's config while validating a new one.

## Whitelist per-asset fields

```json
{
  "symbol": "BTC",
  "strategy": "supertrend_ema200",
  "mode": "sim",
  "primary_timeframe": "1h",
  "regime_router_enabled": true,
  "regime_router_live_confirmed": false,
  "sim_fee_pct": 0.1
}
```

- `mode: live` with `regime_router_enabled` but without `regime_router_live_confirmed` forces sim for safety.
- `sim_fee_pct` is in percent (`0.1` means 0.1%; the live XAU entry uses `0.05`).
- `strategy_params` in `coin_whitelist.json` are merged into live/backtest strategy config.
- `primary_timeframe` and `confirm_timeframe` belong to the pair/strategy source of truth and should not be duplicated elsewhere unless intentionally overridden for a test.

## RegimeRouter behavior

- Config mapping: `regime_router.mapping` in `bot_config.yaml`
- 3-candle debounce before regime switch (`debounce_candles`)
- NO_TRADE regimes block new BUY orders
- Existing open position keeps stop protection during NO_TRADE
- NO_TRADE tightens trailing SL to 1x ATR
- Recovery requires 3 tradeable candles (`recovery_candles`)
- Force close after 6 NO_TRADE candles (`force_close_candles`)
- Strategy handoff keeps the old strategy owner until the open position is closed

## Backtest and live parity

Backtest, live engine, replay validation, TUI indicators, and optimizer should resolve config through the same strategy/portfolio resolver. Avoid adding strategy-impacting values in engine-only config blocks.

Values that affect signal or exits belong under:

- `strategy.config.<strategy_id>`
- `strategy.symbols.<SYMBOL>`
- `coin_whitelist.json -> assets[].strategy_params` when the override is pair-specific

Values that affect sizing belong under:

- `portfolio.position_sizing`
- `portfolio.symbols.<SYMBOL>.position_sizing`

## Tests

Targeted architecture suite:

```bash
PYTHONPATH=. python3 -m unittest \
  tests.test_chart_registry_path \
  tests.test_indicator_display_adapter \
  tests.test_indicator_registry \
  tests.test_sim_broker \
  tests.test_per_symbol_execution_mode \
  tests.test_regime_router_mapping \
  tests.test_no_trade_handoff \
  tests.test_confidence_filter \
  -v
```

Full suite:

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -q
```

SaaS control plane suite (`xauby/saas/`, separate FastAPI service):

```bash
PYTHONPATH=. python3 -m unittest \
  tests.test_saas_control_plane \
  tests.test_saas_auth_backup \
  tests.test_saas_runtime \
  -v
```

New plugins require strategy tests, indicator tests, and chart legend coverage.
