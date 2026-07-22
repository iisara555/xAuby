# Telegram integration

## Setup

1. Create a bot via [@BotFather](https://t.me/BotFather)
2. Get your chat id, for example by messaging [@userinfobot](https://t.me/userinfobot)
3. Configure `.env`:

```env
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

4. Enable polling in `bot_config.yaml`:

```yaml
notifications:
  alert_channel: telegram
  telegram_command_polling_enabled: true
```

## Commands

Only the configured `TELEGRAM_CHAT_ID` is authorized.

| Command | Description |
|---------|-------------|
| `/help`, `/start` | Command list |
| `/status` | All pairs: price, mode, position, last signal |
| `/pnl` | 7-day and 30-day portfolio PnL plus per pair |
| `/regime` | Regime snapshot per pair |
| `/last` | Recent closed trades per pair |
| `/health` | Engine health snapshot: mode, pause state, pairs, open positions, feed/WS issues |
| `/pause` | Confirm emergency pause; blocks new BUY orders without closing positions |
| `/resume` | Confirm resume; allows new BUY orders again |

## Automatic alerts

| Event | Level |
|-------|-------|
| Engine started / stopped | trade / info |
| BUY / SELL filled | trade |
| SimBroker order filled | trade |
| Macro guard blocked BUY | info |
| Regime score / label change | info |
| RegimeRouter handoff / NO_TRADE state | info / position |
| Trailing stop updated | position |
| SL restore failure | position / critical |
| Daily digest / weekly review | info |

## Multi-pair message layout

When multiple pairs are active, messages show portfolio totals first and then per-pair lines.

`/status` example (current single-pair baseline: OKX XAUUSDT, `xauby_actionzone`):

```text
xAuby Status - 1 active pair(s)
Equity: 85.00 USDT | Global: LIVE
- XAUUSDT 4h | LIVE | xauby_actionzone | 4496.84 | IDLE | HOLD
```

With more than one whitelisted pair, additional lines stack the same way, one
per active pair.

`/pnl` shows a portfolio block first, then `SYMBOL: trades | net` per pair.

Daily digest shows a 24h portfolio line, then a per-pair block with position, mode, and regime shorthand.

Implementation: `xauby/notifications/multi_pair_format.py`.

## Semi-auto mode

```yaml
trading:
  mode: semi_auto
notifications:
  semi_auto_confirm_timeout_seconds: 60
```

The bot sends an inline keyboard (`Confirm BUY` / `Skip`). Callbacks call `confirm_semi_auto_buy()` or `skip_semi_auto_buy()` on the engine.

## Emergency pause

`/pause` and `/resume` are two-step commands. The text command only asks for
confirmation; the inline button applies the runtime control. Pause blocks new
BUY orders across signal, manual, and semi-auto paths. Existing positions are
not closed.

Critical alerts include `Status` and `Ack` buttons by default. `Status` returns
the same engine health snapshot as `/health`; `Ack` records an operator
acknowledgement in chat only.

## RegimeRouter operator note

The current live XAU pair keeps `regime_router_enabled: false` — it runs its
single strategy (`xauby_actionzone`) directly, no router. A live routed pair
requires explicit per-asset `regime_router_live_confirmed: true`; otherwise the
engine forces that pair to sim and sends an operator warning.

## Test connectivity

```bash
./venv/bin/python tests/test_all_telegram_alerts.py
./venv/bin/python tests/test_multi_pair_telegram_send.py
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| No messages | Check `TELEGRAM_ENABLED`, token, and chat id |
| Commands ignored | Chat id mismatch; only one authorized chat |
| Markdown parse errors | Check `telegram_failures.log` if enabled |
| Stop spam on restart | Engine sends stop only after loop started; avoid duplicate processes |
| Mode looks wrong | Check per-asset `mode` and RegimeRouter live-confirmation gate |
