# Telegram integration

Two ways to wire this up, depending on how you run xAuby:

- **Single operator / self-hosted** — set the env vars and YAML keys yourself. See [Setup](#setup) below.
- **Pilot Workspace (multi-tenant SaaS)** — each tenant connects their own bot from the
  web UI. See [Pilot Workspace](#pilot-workspace) at the end.

Either way the engine is the same: it reads `TELEGRAM_ENABLED`, `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID` from its environment once at startup, plus the `notifications:` block
from its `bot_config.yaml`.

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

`/status` example (current OKX XAU + BTC baseline):

```text
xAuby Status - 2 active pair(s)
Equity: 85.00 USDT | Global: LIVE
- XAUUSDT 4h | LIVE | xauby_actionzone | 4496.84 | IDLE | HOLD
- BTCUSDT 4h | LIVE | supertrend_ema200 | 118420.00 | IDLE | HOLD
```

Each additional whitelisted pair stacks the same way, one line per active pair.

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

The current live XAU and BTC pairs keep `regime_router_enabled: false` — each
runs its configured strategy directly, without the router. A live routed pair
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

## Pilot Workspace

In the multi-tenant workspace each tenant brings **their own bot** (BYO). A shared
platform bot is not possible: Telegram's `getUpdates` is exclusive per token, so
several tenant engines polling one token would fight over updates (409 Conflict) and
steal each other's commands.

**Operator flow:** Settings → **Alerts** tab → paste the @BotFather token and the chat
id → *Encrypt & save* → *Send test message* → restart the engine.

| Endpoint | Purpose |
|----------|---------|
| `POST /api/v1/telegram/connect` | Validate, encrypt and store the token |
| `POST /api/v1/telegram/test` | Server-side `getMe` + `sendMessage` (throttled) |
| `PATCH /api/v1/telegram/preferences` | Alert-category toggles |
| `DELETE /api/v1/telegram` | Disconnect and disable alerts |

The bot token is encrypted at rest with AES-256-GCM (`xauby/saas/credentials.py`), bound
to the tenant by an AAD of `xauby:<tenant_id>:telegram:v1`, and is **never returned by any
endpoint** — only `token_last4` and the bot username are exposed. It reaches the engine
through the 0600 tmpfs env file written by `TenantSupervisor.materialize_credentials`,
which systemd loads via `EnvironmentFile=`.

The workspace toggles map onto the same YAML keys documented above:

| Workspace toggle | bot_config.yaml |
|------------------|-----------------|
| Trade lifecycle | `notifications.notify_position_updates` |
| Risk & safety | `notifications.notify_guard_blocks`, `notify_regime_changes` |
| System health | `monitoring.heartbeat_interval_minutes` (60 / 0) |
| Periodic reports | `weekly_review.send_telegram`, `daily_digest.send_telegram` |
| Allow commands | `notifications.telegram_command_polling_enabled` |

Notes:

- **Critical alerts always send**, bypassing every toggle (`xauby/engine/alerts.py:12`).
- Credential *and* preference changes need an **engine restart** — both are read once at
  construction. The UI prompts; it never restarts a running engine for you.
- Commands are disabled automatically for `@channel` ids: `getUpdates` reports numeric
  chat ids, so the poller's exact-match authorization can never succeed for one.
- For a **group** chat id (leading `-`), any group member can run commands including
  `/pause`. The UI warns about this.
