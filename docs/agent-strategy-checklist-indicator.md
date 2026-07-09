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
- analyze(ctx) must be deterministic and must behave as a pure function of ctx:
  do not store mutable cross-call state on the strategy instance
- The same ctx must produce the same Signal, even if analyze(ctx) is called
  repeatedly for dashboard refresh, replay validation, or the same candle
- Use only data provided by ctx:
  ctx.df_primary, ctx.df_regime, ctx.current_price, ctx.has_position,
  ctx.position_side, ctx.stop_loss, ctx.sl_confirmed, ctx.config, ctx.extras
- Do not place orders, cancel orders, import engine internals, read/write the
  database, call network services, open files, use eval/exec, or touch
  Telegram/dashboard directly
- Do not import side-effect libraries such as requests, httpx, websocket,
  sqlalchemy, sqlite3, subprocess, or os
- Do not implement position sizing inside the strategy unless the existing
  engine contract explicitly asks for a sizing hint. The engine owns sizing.
- The indicator must subclass xauby.strategies.indicators.base.Indicator
- Indicator compute(df, config) must return the input DataFrame with added
  indicator columns
- Indicator compute(df, config) should use pandas vectorized operations for
  full recompute; avoid per-row Python loops over the whole history
- Every Signal should include indicators and checklist diagnostics

Work in this order:
1. Summarize the trading thesis in 5-8 bullets
2. Define the best market regime and regimes to avoid
3. Define primary timeframe, regime timeframe, min_bars, and asset assumptions
4. Define candle-closure assumptions and timestamp alignment across timeframes
5. Define entry rules, exit rules, hold rules, stop/risk hints, and invalidation
6. Define reversal policy when exit and opposite entry are both true
7. Define config knobs with defaults, types, min/max boundaries, and rationale
8. Define indicator columns with stable column names
9. Design checklist items for every important gate
10. Create strategy.py, __init__.py, and the required indicator plugin
11. Add strategy/indicator registry mapping if required
12. Add an example config block for bot_config.yaml or docs
13. Add tests covering BUY/SELL/HOLD, config validation, indicator columns,
    threshold edges, NaN handling, and sandbox-safe behavior
14. Explain how to backtest or replay-validate the strategy before live use

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

## Signal And Base Class Schema Reference

Agents must follow the project contracts below exactly. Do not invent alternate
field names such as `sl`, `take_profit`, `entry_score`, or `debug_info` unless
they are placed under `metadata`.

### Strategy Base Class

Every strategy must inherit from `xauby.strategies.Strategy`.

```python
from xauby.strategies import Strategy, MarketContext, Signal, register

@register("<strategy_id>")
class MyStrategy(Strategy):
    name = "<strategy_id>"
    version = "0.1.0"
    author = "unknown"
    description = ""
    tags = []
    required_timeframes = ["4h"]
    min_bars = 100

    @classmethod
    def default_config(cls) -> dict:
        return {}

    def validate_config(self) -> list[str]:
        return []

    def analyze(self, ctx: MarketContext) -> Signal:
        ...
```

Required behavior:

- `analyze(ctx)` must always return a `Signal`
- `analyze(ctx)` must not raise for normal market-data edge cases
- `default_config()` returns every tunable knob used by the strategy
- `validate_config()` returns `list[str]` containing both advisory warnings and
  fatal configuration errors
- Fatal validation messages must include the word `must`; the inherited
  `config_errors()` method automatically filters `validate_config()` messages
  containing `must` into the fatal list used by strict mode
- Do not override `config_errors()` unless the strategy truly needs custom
  fatal-versus-advisory logic that cannot be expressed with the `must`
  convention
- Optional lifecycle hooks exist (`on_start`, `on_stop`, `on_trade_filled`) but
  new strategies should avoid relying on mutable lifecycle state unless the
  replay/backtest implications are fully tested

### MarketContext Input

`analyze(ctx)` receives a read-only market snapshot:

| Field | Type | Meaning |
|---|---|---|
| `symbol` | `str` | Trading symbol, for example `XAUTUSDT` |
| `timeframe_primary` | `str` | Primary candle timeframe, for example `4h` |
| `df_primary` | `pd.DataFrame` | Primary OHLCV candles |
| `current_price` | `float` | Latest ticker/current price |
| `has_position` | `bool` | Whether the engine currently has a position |
| `position_side` | `str \| None` | `LONG`, `SHORT`, or `None` |
| `stop_loss` | `float` | Active stop price, or `0.0` if none |
| `sl_confirmed` | `bool` | Engine-confirmed stop-loss breach |
| `timeframe_regime` | `str \| None` | Optional higher timeframe |
| `df_regime` | `pd.DataFrame \| None` | Higher-timeframe candles |
| `config` | `dict` | Strategy-specific merged config |
| `engine_config` | `dict` | Full read-only engine config |
| `extras` | `dict` | Optional engine-supplied extras, such as regime/macro data |

`engine_config` is for read-only diagnostics and display context only. Strategy
entry/exit gates should not branch on global engine settings such as exchange,
fee tier, portfolio risk, or unrelated bot-wide options. Put strategy decisions
behind strategy-scoped keys in `ctx.config` instead.

### Extras Schema

`ctx.extras` is an optional extension bag supplied by the engine or surrounding
runtime. Keys may be absent. Strategies must always use `.get()` with explicit
defaults and must not assume any key is present.

The extras contract is intentionally conservative. If a key is not standardized
for the current runtime, the strategy must treat it as unavailable and return
HOLD or use a documented safe fallback.

| Key | Type | Meaning |
|---|---|---|
| `macro_regime` | `str \| None` | Optional macro regime label, for example `RISK_ON`, `RISK_OFF`, or `NEUTRAL` |
| `macro_guard_score` | `float \| None` | Optional 0.0-1.0 guard/filter score where higher means more supportive |
| `regime` | `dict \| None` | Optional regime snapshot supplied by a router/classifier |
| `regime_trend` | `str \| None` | Optional trend label such as `BULL`, `BEAR`, `RANGE`, or `NEUTRAL` |
| `volatility_state` | `str \| None` | Optional volatility label such as `QUIET`, `NORMAL`, `HIGH`, or `EXTREME` |
| `funding_rate` | `float \| None` | Optional funding rate for derivative markets |
| `market_session` | `str \| None` | Optional session label if the runtime provides one |

Example:

```python
macro_score = ctx.extras.get("macro_guard_score")
macro_ok = macro_score is None or float(macro_score) >= config["macro_min_score"]

regime = ctx.extras.get("regime") or {}
trend_label = regime.get("trend") or ctx.extras.get("regime_trend") or "UNKNOWN"
```

### Signal Output

Use `buy()`, `sell()`, `hold()`, `open_short()`, or `close_short()` helpers from
`xauby.strategies` whenever possible.

| Field | Type | Required | Meaning |
|---|---|---:|---|
| `action` | `str` | yes | One of `BUY`, `SELL`, `HOLD` |
| `reason` | `str` | yes | Human-readable reason for logs/UI |
| `confidence` | `float` | no | 0.0-1.0 confidence score |
| `timestamp` | `float` | auto | UTC epoch seconds |
| `strategy_name` | `str` | auto | Plugin id |
| `timeframe` | `str` | auto | Primary timeframe |
| `stop_loss_price` | `float \| None` | no | Absolute stop-loss price |
| `stop_loss_distance` | `float \| None` | no | Stop distance in price units |
| `trail_distance` | `float \| None` | no | Strategy-specific trailing distance |
| `volatility` | `float \| None` | no | Volatility hint, commonly ATR |
| `indicators` | `dict` | no | Raw indicator snapshot for diagnostics |
| `status_summary` | `str` | no | Short one-line heartbeat/UI summary |
| `checklist` | `list[dict]` | no | Gate-by-gate diagnostics |
| `metadata` | `dict` | no | Free-form extra diagnostics |
| `intent` | `str \| None` | auto | `OPEN`, `CLOSE`, or `HOLD` |
| `position_side` | `str \| None` | auto | `LONG`, `SHORT`, or `None` |

Risk hint policy:

- Prefer sending one canonical stop hint per signal
- For current order sizing, a positive `stop_loss_distance` is treated as the
  canonical risk distance when present
- If no distance is supplied, a positive `stop_loss_price` can be converted into
  a risk distance
- If neither stop hint is supplied, the engine falls back to ATR/config behavior
  using `volatility` when applicable
- A positive `trail_distance` is an explicit strategy trailing hint
- Strategies should not round stop prices or distances to exchange tick size.
  Return full-precision floats; exchange precision and tick-size rounding belong
  in the engine/broker layer
- Position sizing remains the engine's job; strategies should not calculate
  order quantity or portfolio allocation

### Determinism And Statelessness

Strategy analysis must be deterministic:

- Do not store signal memory in instance attributes such as `self.last_signal`
  or `self.trigger_seen`
- Do not mutate `self.config`, `ctx.config`, `ctx.extras`, or the input
  DataFrames inside `analyze(ctx)`
- Do not depend on wall-clock time, random numbers, API calls, filesystem state,
  or database state
- If state is required, it must be supplied explicitly by the engine through
  `ctx.extras` and covered by replay tests
- Repeated calls with the same `ctx` must return equivalent signals

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

### Timeframe And Candle Alignment

- [ ] Defines whether the final row in `df_primary` is assumed to be a closed
  candle. If not guaranteed, the strategy must explicitly ignore unfinished
  candles or require a closed-candle flag/timestamp convention
- [ ] Does not use values from a higher-timeframe candle before that candle is
  closed
- [ ] Aligns `df_regime` to `df_primary` by timestamp without look-ahead
- [ ] Defines timezone assumptions for all timestamps, preferably UTC
- [ ] Handles missing or stale `df_regime` by returning HOLD or falling back to
  a documented safe behavior

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
  remain valid, and whether freshness is measured by bar index or timestamp
- [ ] Defines a no-chase or cooldown rule when price is extended

Freshness pattern:

```python
trigger = computed["trigger_ok"].fillna(False)
trigger_indices = computed.index[trigger]
last_trigger_idx = trigger_indices[-1] if len(trigger_indices) else None
current_idx = computed.index[-1]
bars_since = (
    None
    if last_trigger_idx is None
    else computed.index.get_loc(current_idx) - computed.index.get_loc(last_trigger_idx)
)
fresh_ok = bars_since is not None and bars_since <= int(config["fresh_window_bars"])
```

Checklist item:

```python
{
    "label": "Freshness",
    "value": "none" if bars_since is None else f"{bars_since} bars",
    "ok": bool(fresh_ok),
    "hint": f"Need <= {config['fresh_window_bars']} bars after trigger",
}
```

### Exit Rules

- [ ] Defines exit behavior when the thesis is invalidated
- [ ] Defines exit behavior when trend or momentum fails
- [ ] Defines profit protection, such as trailing distance or partial take profit
- [ ] Defines SELL behavior for closing long positions and short logic if used
- [ ] Defines behavior when `ctx.sl_confirmed` is true
- [ ] Defines HOLD behavior while a position is open but exit gates are not met
- [ ] Defines reversal policy when exit and opposite entry are both true.
  Default policy: close the existing position first, then wait for a later
  evaluation/bar before opening the new direction unless the engine explicitly
  supports same-tick flip

### Risk Hints

- [ ] Sets `confidence` between 0.0 and 1.0
- [ ] Provides one suitable stop hint when needed:
  `stop_loss_price`, `stop_loss_distance`, or `volatility`
- [ ] Avoids sending conflicting stop hints. If multiple hints are sent, the
  strategy documents which one is canonical
- [ ] Provides `trail_distance` when strategy-specific trailing is required
- [ ] Defines ATR period and multiplier if ATR is used
- [ ] Avoids stops that are too tight for noise or too wide for acceptable R:R
- [ ] Does not calculate order quantity, account allocation, or leverage unless
  the engine contract explicitly provides such a hook

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
- [ ] Column names avoid collisions. Prefer strategy-specific prefixes such as
  `<strategy_id>_rsi` when the same chart can load multiple indicators with
  different periods or meanings
- [ ] Full-history calculations use pandas vectorized operations, rolling
  windows, `ewm`, `shift`, `where`, or `numpy` arrays instead of Python loops
  over every row
- [ ] If the indicator is expensive, document whether incremental computation
  is possible for new candles
- [ ] Strategy logic handles NaN values before making decisions

### Display Config

- [ ] Provides `display_config["metrics"]` for values shown in the UI
- [ ] Uses `zone_column`, `zone_label`, and `buy_zones` when zones exist
- [ ] Uses `lines` for chart overlays
- [ ] Uses `zones` with RGB tuples for zone color mapping. RGB values are
  integer tuples in the 0-255 range, for example `(34, 197, 94)`
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

Default reversal policy: when a position is open, evaluate close/exit logic
before any new entry logic. If exit and opposite entry are both true, return the
close signal first. Do not open the opposite direction in the same signal unless
the engine has an explicit same-tick flip contract and tests cover it. This same
policy applies to long-to-short and short-to-long reversals.

```python
if not enough_bars:
    return hold(
        "not enough bars",
        confidence=0.0,
        indicators=snapshot,
        checklist=checklist,
        status_summary="waiting for more candles",
    )

# Position management comes before new entries.
if ctx.has_position and exit_ok:
    return sell(
        "exit: thesis invalidated",
        confidence=exit_confidence,
        indicators=snapshot,
        checklist=checklist,
        status_summary="exit signal",
    )

if ctx.has_position:
    return hold(
        "position open; waiting for exit",
        confidence=hold_confidence,
        indicators=snapshot,
        checklist=checklist,
        status_summary="position managed",
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

Short-capable strategy pattern:

```python
if ctx.has_position and ctx.position_side == "SHORT" and short_exit_ok:
    return close_short(
        "cover short: thesis invalidated",
        confidence=exit_confidence,
        indicators=snapshot,
        checklist=checklist,
        status_summary="short exit",
    )

if ctx.has_position and ctx.position_side == "LONG" and long_exit_ok:
    return sell(
        "close long: thesis invalidated",
        confidence=exit_confidence,
        indicators=snapshot,
        checklist=checklist,
        status_summary="long exit",
    )

if ctx.has_position:
    return hold(
        "position open; waiting for exit",
        confidence=hold_confidence,
        indicators=snapshot,
        checklist=checklist,
        status_summary="position managed",
    )

if long_entry_ok:
    return buy("open long: setup confirmed", confidence=entry_confidence)

if short_entry_ok:
    return open_short("open short: setup confirmed", confidence=entry_confidence)
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
- Column name collision:
- Performance/incremental compute:
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
- in-sample date range:
- out-of-sample date range:
- walk-forward windows:
- market regimes tested:
- fee/slippage assumptions:
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
- [ ] Strategy is deterministic for repeated calls with identical `ctx`
- [ ] Config validation separates fatal errors from advisory warnings
- [ ] Tests cover insufficient data, failed setup, successful entry,
  successful exit, and invalid config
- [ ] Tests cover exact threshold boundaries
- [ ] Tests cover NaN handling and zero-volume edge cases
- [ ] Fuzz or randomized config tests confirm `analyze(ctx)` does not throw for
  valid parameter ranges
- [ ] Runtime-bound tests or benchmarks confirm `analyze(ctx)` is fast enough
  for repeated live ticks
- [ ] Backtests include in-sample, out-of-sample, walk-forward, and separate
  trend/range/high-volatility regimes
- [ ] Backtests include realistic fee and slippage assumptions
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
- Strategy stores mutable cross-call state in instance attributes
- Strategy calculates order size or leverage instead of returning analysis hints
- Higher-timeframe regime data is joined in a way that leaks future candles
- Magic numbers are scattered through the code
- Checklist only reports the final decision, not the gates that failed
- Indicator column names are unstable or overwrite source columns unnecessarily
- Indicator recomputes expensive logic with slow row-by-row Python loops
- Tests only cover the BUY path
- Backtest review only looks at win rate and ignores drawdown, expectancy,
  trade count, exposure, and slippage
