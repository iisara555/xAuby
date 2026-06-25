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

`/status` example:

```text
xAuby Status - 2 active pair(s)
Equity: 85.00 USDT | Global: LIVE
- XAUTUSDT 4h | LIVE | cdc_action_zone | 4496.84 | IDLE | HOLD
- BTCUSDT 1h | SIM | supertrend_ema200 | 72650.16 | IDLE | HOLD
```

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

## RegimeRouter operator note

BTC currently runs RegimeRouter in sim soak. XAUT remains live with router off. A live routed pair requires explicit per-asset `regime_router_live_confirmed: true`; otherwise the engine forces that pair to sim and sends an operator warning.

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
