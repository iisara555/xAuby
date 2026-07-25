# Textual TUI

xAuby ships a **Textual** terminal UI that reads live engine state. It does not run the trading loop itself and it opens the database in read-only mode.

## Screens

| Screen | Keys | Purpose |
|--------|------|---------|
| Dashboard | `1`, `d` | Charts, pair table, regime, position, strategy/mode badges |
| Trade log | `2`, `t` | Closed trades and history |
| Incident explorer | `4`, `i` | Run timelines from event store |
| Menu | launcher / env | Quick actions for engine, dashboard, tests, config helpers |

Global:

| Key | Action |
|-----|--------|
| `[`, `0` | Previous symbol |
| `]`, `9` | Next symbol |
| `q` | Quit |

## Strategy-aware charts and legends

Charts and legends follow the selected pair's active strategy. The source of truth is:

1. `coin_whitelist.json` and `bot_config.yaml` decide active strategy for the pair.
2. `architecture.strategy_chart_indicators` maps strategy id to indicator plugin ids.
3. Indicator plugin `display_config` provides zone names, colors, line labels, and metrics.

Current display mapping:

| Strategy | Zones | Lines |
|----------|-------|-------|
| `xauby_actionzone` (CDC Action Zone) | Blue / Green / Red / Neutral | EMA12, EMA26 |
| `supertrend_ema200` | ST Bull / ST Bear / Neutral | SuperTrend, EMA200 |
| `bbkc_squeeze` | Compressed / Breakout / Neutral | Bollinger Bands, Keltner Channels |
| `bbrsi_mean_reversion` | Oversold / Neutral / Overbought | Bollinger Bands |
| `btc_ema_pullback` | Trend / Pullback / Reclaim / Neutral | EMA Fast, EMA Slow, EMA Trend |
| `ict_lite_strategy` | Sweep Low / Reclaim / MSS / Neutral | EMA Fast, EMA Slow, Recent High, Recent Low |
| `rsi2_meanrev` | Buy Setup / Oversold / Exit / Neutral | EMA200, SMA5 |
| `vol_breakout` | Breakout / ATR Expansion / Neutral | Range High, Exit EMA |
| `xauby_donchian_trend` | Trend / Breakout / Neutral | Donchian High, Donchian Low |

If the legend looks like CDC while the pair uses another strategy, check the indicator mapping first.

## Screenshots

Captured exports (regenerate anytime):

| View | File |
|------|------|
| Dashboard (wide) | [dashboard-wide.svg](screenshots/dashboard-wide.svg) |
| Trade log | [tradelog.svg](screenshots/tradelog.svg) |
| Incidents | [incidents.svg](screenshots/incidents.svg) |
| Menu | [menu.svg](screenshots/menu.svg) - also used in root README |
| Quick Config | [quick-config.svg](screenshots/quick-config.svg) |

```bash
./venv/bin/python scripts/capture_tui_screenshots.py
```

### Dashboard wide layout

![Dashboard wide layout](screenshots/dashboard-wide.svg)

### Trade log

![Trade log screen](screenshots/tradelog.svg)

### Incident explorer

![Incident explorer](screenshots/incidents.svg)

### Launcher menu

![Launcher menu](screenshots/menu.svg)

### Quick Config

Native Textual config editor (grouped `OptionList`, arrow/Enter/mouse), reached
from the launcher menu → Quick Configuration or `xauby --config`. Each category
opens a submenu of toggles / number+range modals / pickers; secrets are masked.
Set `XAUBY_CONFIG_TERMINAL=1` for the legacy terminal editor (no-TTY).

![Quick Config](screenshots/quick-config.svg)

## Layout breakpoints

The UI adapts to terminal width (see `xauby/ui/textual_tui/layout.py`):

| Width | Layout |
|-------|--------|
| >= 110 cols | Two columns: chart left, stats right |
| 75-109 | Single column, full-width panels |
| < 75 | Compact phone rows, shorter chart |

Charts use each pair's `primary_timeframe` from the whitelist. Current baseline: two live pairs — XAU (OKX XAUUSDT) on 4H with a 1D confirm timeframe, and BTC (OKX BTCUSDT) on 4H with no confirm timeframe.

## Position partial TP display

When a position is open and the strategy config includes `partial_tp_pct`, the
positions panel shows a `PTP` row with:

- fraction to bank, e.g. `50%`
- trigger price, e.g. `@ 4,604.21`
- status: `pending` until the one-shot partial close succeeds, then `banked`

The Signal/checklist panel shows the same `Partial TP` status so an operator can
confirm whether the open remainder has already banked the configured leg. This
reads from `core/logs/xauby_bot_state.json`; if the state file is stale, confirm
the engine process before trusting the displayed target.

## Running the TUI

With tmux, recommended on VPS:

```bash
./scripts/start_dashboard_tmux.sh
./scripts/attach_dashboard_tmux.sh
```

Standalone:

```bash
python -m xauby.ui.textual_tui.app
```

From launcher:

```bash
python launcher.py
```

## Environment tips

| Variable | Recommendation |
|----------|----------------|
| `NO_COLOR` | Unset; otherwise Textual renders monochrome |
| `TERM` | `xterm-256color` or `truecolor` |
| `COLORTERM` | `truecolor` for gradients |

`start_dashboard_tmux.sh` already unsets `NO_COLOR` and sets color terminal vars.

## State file

The TUI watches:

```text
core/logs/xauby_bot_state.json
```

If the file is stale or missing, panels show empty data. Confirm the engine process is running:

```bash
pgrep -af run_xauby
```

## Compact wireframe

```text
+-- xAuby --------------------------------+
| XAUUSDT                       LIVE      |
+-----------------------------------------+
| strategy-aware chart + legend           |
+-----------------------------------------+
| Pair | Price | Mode | Pos | Sig | Regime|
| XAU  | 4496  | LIVE | IDLE| HOLD|  -    |
+-----------------------------------------+
```

With more than one whitelisted pair, `[`/`]` cycles between them and the table
grows one row per pair, same as the multi-pair layout used before the XAU-only
baseline.
