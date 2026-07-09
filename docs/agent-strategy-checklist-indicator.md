# Agent Prompt: Strategy Checklist and Indicator

Use this document as a copy-paste prompt and engineering checklist for asking
an external agent to design or implement a new xAuby strategy and its matching
indicator plugin.

The strategy must behave as a pure market analyst: it reads market context and
returns a `Signal`. It must not place orders, touch the database, call network
services, write files, send Telegram messages, or interact with the dashboard.

> Note: This is an engineering workflow, not financial advice.

---

## Copy-Paste Prompt For An Agent

```text
You are a senior quant/dev agent building a trading strategy and indicator for
the xAuby project.

Goals:
1. Design a new strategy plugin under xauby/strategies/<strategy_id>/
2. Create a matching indicator plugin under xauby/strategies/indicators/
3. Return a clear checklist explaining every signal gate for dashboard/TUI use
4. Keep the strategy sandbox-safe and independent from engine internals

Hard constraints:
- The strategy must subclass xauby.strategies.Strategy
- The strategy must return a Signal from analyze(ctx)
- Use only data provided by ctx:
  ctx.df_primary, ctx.df_regime, ctx.current_price, ctx.has_position,
  ctx.position_side, ctx.stop_loss, ctx.sl_confirmed, ctx.config, ctx.extras
- Do not place orders, cancel orders, import engine internals, read/write the
  database, call network services, open files, use eval/exec, or touch
  Telegram/dashboard directly
- Do not import side-effect libraries such as requests, httpx, websocket,
  sqlalchemy, sqlite3, subprocess, or os
- The indicator must subclass xauby.strategies.indicators.base.Indicator
- Indicator compute(df, config) must return the input DataFrame with added
  indicator columns
- Every Signal should include indicators and checklist diagnostics

Work in this order:
1. Summarize the trading thesis in 5-8 bullets
2. Define the best market regime and regimes to avoid
3. Define primary timeframe, regime timeframe, min_bars, and asset assumptions
4. Define entry rules, exit rules, hold rules, stop/risk hints, and invalidation
5. Define config knobs with defaults, types, min/max boundaries, and rationale
6. Define indicator columns with stable column names
7. Design checklist items for every important gate
8. Create strategy.py, __init__.py, and the required indicator plugin
9. Add strategy/indicator registry mapping if required
10. Add an example config block for bot_config.yaml or docs
11. Add tests covering BUY/SELL/HOLD, config validation, indicator columns,
    and sandbox-safe behavior
12. Explain how to backtest or replay-validate the strategy before live use

Required response format:
- Strategy Spec
- Indicator Spec
- Checklist Design
- Files Changed
- Config Example
- Tests
- Risks and Tuning Notes
```

---

## Strategy Design Checklist

Use this checklist before writing code. It forces the trading idea to become
explicit before implementation.

### Identity

- [ ] `strategy_id` is snake_case, for example `ema_vwap_pullback`
- [ ] Class name is descriptive, for example `EmaVwapPullbackStrategy`
- [ ] Strategy declares `version`, `author`, `description`, and `tags`
- [ ] Strategy declares `required_timeframes`, for example `["4h", "1d"]`
- [ ] Strategy declares `min_bars` large enough for all indicators

### Market Thesis

- [ ] Clearly states whether it is trend, mean reversion, breakout, scalp,
  hedge, short, or hybrid
- [ ] Defines the regime where signals are allowed
- [ ] Defines regimes where the strategy should return HOLD
- [ ] Defines suitable assets, such as XAUTUSDT, BTCUSDT, or SOLUSDT
- [ ] Defines where the edge is expected and where it likely decays

### Entry Rules

- [ ] Uses multiple entry gates, not a single isolated indicator
- [ ] Includes a trend filter such as EMA200, Supertrend, Donchian midline,
  or higher-timeframe regime
- [ ] Includes a momentum filter such as RSI, MACD slope, ROC, or ADX
- [ ] Includes a volatility or volume filter such as ATR%, Bollinger width,
  or volume ratio when relevant
- [ ] Defines the trigger candle, such as close above level, pullback reclaim,
  breakout confirmation, or zone crossover
- [ ] Defines a fresh signal window, such as how many bars after the trigger
  remain valid
- [ ] Defines a no-chase or cooldown rule when price is extended

### Exit Rules

- [ ] Defines exit behavior when the thesis is invalidated
- [ ] Defines exit behavior when trend or momentum fails
- [ ] Defines profit protection, such as trailing distance or partial take profit
- [ ] Defines SELL behavior for closing long positions and short logic if used
- [ ] Defines behavior when `ctx.sl_confirmed` is true
- [ ] Defines HOLD behavior while a position is open but exit gates are not met

### Risk Hints

- [ ] Sets `confidence` between 0.0 and 1.0
- [ ] Provides one suitable stop hint when needed:
  `stop_loss_price`, `stop_loss_distance`, or `volatility`
- [ ] Provides `trail_distance` when strategy-specific trailing is required
- [ ] Defines ATR period and multiplier if ATR is used
- [ ] Avoids stops that are too tight for noise or too wide for acceptable R:R

### Config

- [ ] Every parameter is declared in `default_config()`
- [ ] `validate_config()` catches invalid values
- [ ] Fatal validation messages use the word `must` so strict mode can detect
  true configuration errors
- [ ] Numeric values have reasonable boundaries
- [ ] No unexplained magic numbers are buried inside `analyze()`
- [ ] Defaults are conservative enough for first-pass backtesting

---

## Indicator Design Checklist

Indicators compute columns, snapshots, legends, panels, and chart/TUI metadata.
They should not replace the strategy's decision logic.

### File And Registry

- [ ] Create `xauby/strategies/indicators/indicator_<strategy_id>.py`
- [ ] Register it with `@register("<strategy_id>")` or another stable name
- [ ] Subclass `Indicator`
- [ ] If the chart should load it automatically, update
  `DEFAULT_STRATEGY_CHART_MAP`

### Compute Contract

- [ ] `compute(df, config)` returns a DataFrame
- [ ] It preserves input columns and appends stable indicator columns
- [ ] It handles empty DataFrames without crashing
- [ ] It handles insufficient bars without crashing
- [ ] It uses stable names such as `ema_fast`, `ema_slow`, `atr`, `rsi`,
  `zone`, `trend_ok`, or `vol_ratio`
- [ ] Strategy logic handles NaN values before making decisions

### Display Config

- [ ] Provides `display_config["metrics"]` for values shown in the UI
- [ ] Uses `zone_column`, `zone_label`, and `buy_zones` when zones exist
- [ ] Uses `lines` for chart overlays
- [ ] Uses `zones` with RGB tuples for zone color mapping
- [ ] Snapshot values are short and human-readable
- [ ] Snapshot output contains no ANSI color codes

---

## Checklist Item Pattern

The strategy should include `checklist` in every `Signal`, including HOLD
signals, so the user can see which gate passed or failed.

Basic item:

```python
{
    "label": "Trend",
    "value": "close > EMA200",
    "ok": bool(trend_ok),
    "hint": "Trade only with the higher-timeframe trend",
}
```

Progress item:

```python
{
    "label": "Volume",
    "value": f"{vol_ratio:.2f}x",
    "ok": vol_ratio >= config["vol_min_ratio"],
    "hint": f"Need >= {config['vol_min_ratio']:.2f}x",
    "bar": {
        "type": "progress",
        "max": 2.0,
        "threshold": config["vol_min_ratio"],
        "value": vol_ratio,
    },
}
```

Range item:

```python
{
    "label": "RSI",
    "value": f"{rsi:.1f}",
    "ok": config["rsi_min"] <= rsi <= config["rsi_max"],
    "hint": f"Need {config['rsi_min']}-{config['rsi_max']}",
    "bar": {
        "type": "range",
        "min": 0.0,
        "max": 100.0,
        "low": config["rsi_min"],
        "high": config["rsi_max"],
        "value": rsi,
    },
}
```

Recommended checklist labels:

- `Regime`
- `Trend`
- `Setup`
- `Trigger`
- `Momentum`
- `Volatility`
- `Volume`
- `Risk`
- `Position`
- `Exit`

---

## Signal Decision Template

Use a decision flow like this so signals are easy to debug.

```python
if not enough_bars:
    return hold(
        "not enough bars",
        confidence=0.0,
        indicators=snapshot,
        checklist=checklist,
        status_summary="waiting for more candles",
    )

if ctx.has_position and exit_ok:
    return sell(
        "exit: thesis invalidated",
        confidence=exit_confidence,
        indicators=snapshot,
        checklist=checklist,
        status_summary="exit signal",
    )

if not ctx.has_position and all_entry_gates_ok:
    return buy(
        "entry: setup confirmed",
        confidence=entry_confidence,
        stop_loss_distance=stop_distance,
        trail_distance=trail_distance,
        volatility=atr,
        indicators=snapshot,
        checklist=checklist,
        status_summary="entry signal",
    )

return hold(
    "waiting for setup",
    confidence=hold_confidence,
    indicators=snapshot,
    checklist=checklist,
    status_summary="hold",
)
```

---

## Indicator Spec Template

Ask the agent to fill this out before writing indicator code.

```text
Indicator ID:
Strategy Pair:
Purpose:
Input Columns:
- open
- high
- low
- close
- volume

Output Columns:
- <column_name>: <meaning>
- <column_name>: <meaning>

Config:
- <param>: default=<value>, type=<type>, min=<min>, max=<max>

Zones:
- GREEN: buy/long-friendly
- RED: exit/avoid/short-friendly
- NEUTRAL: no edge

Metrics For Dashboard:
- label=<label>, key=<column>, ok=<condition>, hint=<text>

Edge Cases:
- Empty DataFrame:
- Not enough bars:
- NaN handling:
- Zero volume:
```

---

## Strategy Spec Template

```text
Strategy ID:
Class Name:
Version:
Author:
Description:
Tags:
Required Timeframes:
Minimum Bars:

Trading Thesis:

Best Market Regime:

Avoid Market Regime:

Entry Gates:
1.
2.
3.

Exit Gates:
1.
2.
3.

Risk Hints:
- Stop:
- Trail:
- Volatility:
- Confidence:

Config Knobs:
- name:
  default:
  type:
  min/max:
  reason:

Checklist:
- label:
  value:
  ok:
  hint:

Backtest Plan:
- symbols:
- timeframe:
- date range:
- baseline strategy:
- metrics:
```

---

## Files An Agent Should Usually Create

A new strategy should usually include at least:

```text
xauby/strategies/<strategy_id>/__init__.py
xauby/strategies/<strategy_id>/strategy.py
xauby/strategies/indicators/indicator_<strategy_id>.py
tests/test_<strategy_id>.py
```

If the indicator should appear automatically on chart/TUI, update:

```text
xauby/strategies/indicators/registry.py
```

If the strategy should be enabled, add config like:

```yaml
strategy:
  active: "<strategy_id>"

strategies:
  <strategy_id>:
    enabled: true
    # strategy-specific knobs here
```

---

## Acceptance Criteria

The work is complete only when all items pass:

- [ ] Strategy imports without error
- [ ] Strategy auto-discovery sees the new plugin
- [ ] Indicator registry sees the new indicator when applicable
- [ ] `analyze(ctx)` returns a `Signal` on every path
- [ ] BUY/SELL/HOLD signals have human-readable reasons
- [ ] `Signal.checklist` covers all important gates
- [ ] `Signal.indicators` includes a snapshot of key values
- [ ] Strategy has no forbidden side effects
- [ ] Config validation separates fatal errors from advisory warnings
- [ ] Tests cover insufficient data, failed setup, successful entry,
  successful exit, and invalid config
- [ ] Backtest or replay validation passes before live use

---

## Common Indicator Set

Choose indicators based on the thesis. Do not use all of them by default.

### Trend

- EMA/SMA: trend direction, pullback, dynamic support/resistance
- EMA200: long-term bias filter
- Supertrend: trend state and trailing reference
- Donchian Channel: breakout and trend boundary
- VWAP: value or pullback reference

### Momentum

- RSI: overbought/oversold and momentum confirmation
- ROC: rate of change
- MACD histogram or slope: momentum acceleration
- ADX: trend strength filter

### Volatility

- ATR: stop distance, trail distance, and volatility sizing
- Bollinger Band width: squeeze or range expansion
- Keltner Channel width: squeeze confirmation

### Volume

- Volume SMA ratio: breakout confirmation
- OBV slope: accumulation/distribution proxy
- Volume spike: event filter, used carefully to avoid late entries

### Regime

- Higher-timeframe EMA slope
- Higher-timeframe close versus EMA200
- Realized volatility percentile
- Trend/range classifier
- Macro guard score from `ctx.extras` when available

---

## Red Flags

Ask the agent to stop and redesign if any of these appear:

- The strategy buys from one indicator with no regime or risk gate
- Exit rules are vague or missing
- Behavior while a position is already open is undefined
- Logic uses future data or look-ahead bias
- Logic uses the current unfinished candle unintentionally
- Magic numbers are scattered through the code
- Checklist only reports the final decision, not the gates that failed
- Indicator column names are unstable or overwrite source columns unnecessarily
- Tests only cover the BUY path
- Backtest review only looks at win rate and ignores drawdown, expectancy,
  trade count, exposure, and slippage

