# `xauby.strategies` — Strategy Plugin Framework

See also [README_DEV.md](../../README_DEV.md) for the strategy + indicator plugin pairing requirement.

The trading engine (`xauby.engine.trading.LiteTradingEngine`) is **strategy-agnostic**.
It only knows how to:

* place orders / manage positions
* manage risk (sizing, stop-loss, trailing)
* persist trade history, push dashboard / websocket updates, send Telegram alerts

It does **not** know about CDC zones, ICT setups, macro overlays, indicators,
or any specific trading idea. Those live as **plugins** in this folder.

```
Trading Engine
└── StrategyRunner (sandbox)
    └── Strategy Interface (xauby.strategies.Strategy)
        ├── cdc_action_zone/      # CDC Action Zone (4H + D1 regime)
        ├── ict/                  # (your future ICT setup)
        ├── macro/                # (your future macro overlay)
        └── hybrid/               # (your future hybrid system)
```

---

## Contract

Every plugin is a single subclass of `xauby.strategies.Strategy` that
returns a `Signal` from `analyze(ctx)`:

```python
from xauby.strategies import Strategy, MarketContext, Signal, register, buy, hold

@register("my_strategy")
class MyStrategy(Strategy):
    version = "0.1.0"
    author = "Your Name"
    description = "One-line description of the strategy."
    tags = ["trend", "4h"]
    required_timeframes = ["4h", "1d"]
    min_bars = 100

    @classmethod
    def default_config(cls) -> dict:
        return {
            "my_param": 42,
            "threshold": 0.5,
        }

    def validate_config(self) -> list[str]:
        warnings = []
        if self.config.get("threshold", 0.5) < 0:
            warnings.append("threshold must be >= 0")
        return warnings

    def analyze(self, ctx: MarketContext) -> Signal:
        # 1. Do whatever analysis you want on ctx.df_primary / ctx.df_regime
        # 2. Decide BUY / SELL / HOLD
        # 3. Return a Signal (optionally with risk hints for the engine)
        return hold("waiting for setup")
```

What the strategy **must not** do:

* place orders / cancel orders
* read / write the database
* touch the websocket, Telegram, or the dashboard
* import `LiteTradingEngine`
* import network libraries (`requests`, `httpx`, `websocket`)
* import database libraries (`sqlalchemy`, `sqlite3`)

The `StrategyRunner` sandbox enforces these constraints at runtime.

---

## Signal fields

What the engine reads from the returned `Signal`:

| Field               | Category   | Purpose                                                       |
|---------------------|------------|---------------------------------------------------------------|
| `action`            | Execution  | `BUY` / `SELL` / `HOLD` — the only execution signal           |
| `reason`            | Execution  | Human-readable string for logs / Telegram / dashboard         |
| `confidence`        | Risk       | 0.0–1.0 score (for filtering / multi-strategy ensembles)      |
| `stop_loss_price`   | Risk       | Absolute SL price — engine uses *instead of* ATR-based SL     |
| `stop_loss_distance`| Risk       | SL distance (price units) — engine uses *instead of* ATR SL   |
| `trail_distance`    | Risk       | Trailing stop distance — engine uses *instead of* ATR trailing |
| `volatility`        | Risk       | Volatility hint (e.g. ATR) for position sizing / trailing     |
| `timestamp`         | Provenance | UTC epoch seconds (auto-filled by sandbox)                    |
| `strategy_name`     | Provenance | Plugin id (auto-filled by sandbox)                            |
| `timeframe`         | Provenance | Primary timeframe (auto-filled by sandbox)                    |
| `status_summary`    | Diagnostics| One-line status for engine heartbeat logs                     |
| `indicators`        | Diagnostics| Raw indicator snapshot — surfaced to dashboard/state JSON     |
| `checklist`         | Diagnostics| Structured gate items for dashboard (see below)                 |

Each checklist item is a dict: `label`, `value`, `ok`, optional `hint`, optional `columns` (list of pipe-separated tail segments for rich TUI rows), and optional `bar` (`range` or `progress`).

Example tail: `"columns": ["Need: 0.5x"]` renders as `Vol Ratio : 0.75x [████] 25% │ Need: 0.5x`.
| `metadata`          | Diagnostics| Free-form bag for backtests, ML, etc.                         |

---

## Plugin Metadata

Every strategy exposes marketplace-facing metadata via
`strategy_manifest("<id>")`, `available_strategy_manifests()`, and
`Strategy.describe()`. Built-in strategies can optionally add a
`strategy.yaml`, `strategy.yml`, or `manifest.yaml` next to `strategy.py`;
missing fields fall back to class metadata.

```python
from xauby.strategies import strategy_manifest

strategy_manifest("cdc_action_zone")
# {
#     "manifest_version": 1,
#     "id": "cdc_action_zone",
#     "name": "cdc_action_zone",
#     "version": "1.0.0",
#     "author": "xAuby",
#     "description": "CDC Action Zone trend-following ...",
#     "tags": ["trend", "swing", "4h", "ema"],
#     "required_timeframes": ["4h", "1d"],
#     "min_bars": 100,
#     "config_schema": { ... },
#     "default_config": { ... },
#     "permissions": {"network": False, "database": False, "orders": False},
#     "source": "builtin",
#     "entrypoint": "xauby.strategies.cdc_action_zone.strategy:CDCActionZoneStrategy",
# }
```

---

## Config Isolation

Each strategy declares its defaults via `default_config()` and validates
its config on load via `validate_config()`.

The engine merges configs as: `Strategy.default_config() | YAML config block`,
so YAML values always override defaults. Strategies access their config
via `self.config` (constructor) or `ctx.config` (per-tick).

```yaml
strategy:
  active: "cdc_action_zone"

strategies:
  cdc_action_zone:
    rsi_min: 40.0
    rsi_max: 75.0
    vol_min_ratio: 1.0
    require_fresh_zone: true
    fresh_zone_window: 3      # bars after GREEN crossover still eligible
    ap_smoothing: 2           # 2 = Piriya V3 / TradingView CDC parity
    use_d1_regime_filter: false
    # ... other overrides; defaults come from the plugin
```

Tune `fresh_zone_window` offline with `scripts/backtest_fresh_window.py`.

---

## Strategy Sandbox

The engine wraps every strategy in a `StrategyRunner` which:

* **Timeout** — `analyze()` must complete within a configurable limit (default 5s).
* **Exception isolation** — crashes return a safe `HOLD` signal.
* **Signal validation** — checks action, confidence range, SL/trail values.
* **Performance logging** — warns when `analyze()` takes >1s.
* **Static capability scan** — at load time, the strategy's source is parsed
  (AST) and rejected/flagged if it imports engine-internal modules
  (anything under `xauby.` other than `xauby.strategies.*`), network/DB/process/
  filesystem libraries, or uses `eval`/`exec`/`__import__`/`open` and
  sandbox-escape attributes (`__globals__`, `__subclasses__`, …). See
  `sandbox_scan.py`.
* **Provenance stamping** — auto-fills `timestamp`, `strategy_name`, `timeframe`.

Configure in `bot_config.yaml`:

```yaml
strategy:
  active: "cdc_action_zone"
  sandbox_timeout: 5.0
architecture:
  # warn (false) vs reject (true) plugins that fail the capability scan.
  # Set true for untrusted/marketplace plugins; first-party plugins pass clean.
  strategy_sandbox_strict: false
```

> Note: the static scan is defense-in-depth, not a true sandbox. Full isolation
> of untrusted code requires a separate process / seccomp / RestrictedPython.

---

## Adding a new strategy

1. Create a new package: `xauby/strategies/<your_id>/`
2. Inside it, drop a `strategy.py` with a class decorated by
   `@register("<your_id>")`.
3. Implement `default_config()` and `validate_config()`.
4. Add the matching config block in `bot_config.yaml`:

   ```yaml
   strategy:
     active: "<your_id>"

   strategies:
     <your_id>:
       my_param: 42
   ```

No engine code changes required. On startup the engine auto-discovers
every subpackage of `xauby.strategies` and instantiates the one named
in `strategy.active`.

---

## Selecting / listing strategies programmatically

```python
from xauby.strategies import available_strategies, load_strategy

print(available_strategies())   # ['cdc_action_zone', ...]

strat = load_strategy("cdc_action_zone", config={"rsi_min": 50})
print(strat.describe())
```

---

## Roadmap-friendly

This layout was designed to be ready for:

* **Strategy marketplace**: each plugin is self-contained with metadata,
  config schema, and validation — can be shipped as a single directory.
* **Multi-pair systems**: `MarketContext.symbol` is part of the contract,
  so a strategy can be used per-pair.
* **Backtesting engine**: backtests call `Strategy.analyze(ctx)` directly
  with replayed `MarketContext`s — see `xauby/backtest/engine.py` and
  `scripts/replay_backtest.py` for plugin replay against stored candles.
* **Live run validation**: `scripts/replay_validate.py <run_id>` re-runs this
  plugin at each logged tick and diffs `Signal.action` vs live
  `signal_evaluated` events (see `xauby/observability/replay_validation.py`).
* **AI-assisted strategies**: ML strategies just consume `df_primary`
  and return a `Signal` like any other plugin.
* **Macro overlays**: write a `macro_*` strategy plugin and select it
  via config; or layer it as a filter inside a hybrid plugin.
