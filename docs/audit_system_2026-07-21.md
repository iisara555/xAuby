# System audit — frontend/backend consistency, owner + tenant surfaces

Date: 2026-07-21. Scope: Pilot Workspace (`Website/`), SaaS control plane
(`xauby/saas/`), engine state export (`xauby/engine/`, `xauby/observability/`),
and the contracts between them, for both the owner (platform_admin) and tenant
roles. Read-only audit; one HIGH config fix applied in the same change (see F-1).

## Surface map

| Layer | Pieces |
|---|---|
| Tenant UI | dashboard (`app/page.tsx`), settings (trading/exchange/security tabs), activity, signal, login/invite/reset flows |
| Owner UI | `app/admin/page.tsx` — users list + invite issuing only |
| API | 45 endpoints: auth (15), profile/catalog (7), runtime (5), bot control (4), exchange/live (5), orders (3), trade-pin (2), admin (5) |
| Engine state | v2 schema: `aggregate` + `by_symbol{SYM}` + `focus_symbol`, per-pair `position` / `equity_breakdown` / `signal_meta` / `regime` / `recent_events` |
| Tenant compile | `supervisor.apply_profile` — catalog presets → bounded yaml/whitelist; **raw strategy config is never accepted** |

## Findings (severity-ranked)

### F-1 HIGH — `max_open_positions` enforced key vs edited key (repo baseline) — FIXED here
The BTC deploy raised `portfolio.max_open_positions` to 3, but **nothing reads
that key**: the engine BUY gate reads `trading.max_open_positions`
(`engine/loop.py:2087`) and the config resolver reads
`risk.max_open_positions` (`runtime/trading_config.py:367`) — both still 1.
On the repo-engine baseline, with XAU holding a position, BTC entries were
silently blocked with "max_open_positions 1 reached". Tenant engines were NOT
affected (`apply_profile` writes `trading.max_open_positions = pair_count`).
**Fix applied:** all three keys set to 2 with cross-reference comments.
**Root cause class:** three same-named keys in different blocks with different
consumers; recommend a startup consistency check mirroring the `risk_pct` guard.

### F-2 MEDIUM — owner UI covers a fraction of the owner API
`admin/page.tsx` exposes only *users list* and *invite issuing*. The
consequential owner actions — `POST /admin/tenants/{id}/approve-live`,
`POST /admin/users/{id}/status` (suspend/reactivate), `GET /admin/tenants`
(capacity/slots) — have **no UI**; the owner must curl them with a CSRF token
and TOTP-verified session, which is error-prone for exactly the actions that
gate real money. Recommend an "Operations" section on the admin page.

### F-3 MEDIUM — manual-order preview sizing diverges from certified sizing
`orders/preview` risk-sizes with a synthetic stop distance of `mark * 0.02`
(`app.py`), while the certified BTC preset stops at 3×ATR (≈3% on 4h). A
manual `strategy_handoff` open can therefore be ~1.5× larger than the same
signal sized by the engine. Bounded by `min(preset cap, allocation_pct)` so
exposure stays capped, but preview quantity ≠ engine quantity for the same
trade. Recommend sizing the preview from the pair's live ATR (available in the
runtime snapshot's indicators).

### F-4 LOW — `read_curated_config` flattens per-pair sizing to the focus pair
`max_position_per_trade_pct` in the compiled summary comes from the *active*
symbol's per-pair sizing only; the settings risk summary shows one number even
when pairs differ. Display-only (enforcement is per-pair), but can mislead.

### F-5 LOW — admin read endpoints skip the shared `admin_user` dependency
`GET /admin/users` and `GET /admin/tenants` re-implement the role+TOTP check
inline with `Depends(current_user)` instead of `Depends(admin_user)`. Same
outcome today; drift risk if the shared dependency gains checks later.

### F-6 LOW — doc drift
`README_DEV.md` and parts of `docs/` still describe the Binance-spot era
(CLAUDE.md itself flags this). `docs/configuration.md` was corrected for the
pair table and `risk_pct` in `9d71b50`; the deeper architecture docs still lag
the OKX-swap baseline.

## Verified-resolved (regressions fixed on main during this cycle)
- SHORT position snapshots freezing (mark price/PnL stale on the position card) — `9d71b50`
- Swap exposure collapsing to bare PnL → allocation bar stuck at 2% — `7041079`
- Sim pairs reporting the live portfolio total next to virtual cash — `463a3df`
- Halt (`POSITION SIDE MISMATCH`) not gating close/partial-TP paths; engine
  kill patterns reaching other deployments' engines; account-lock dir split —
  `3e28081`
- Settings risk summary hardcodes (3% daily cap, fixed 5% buffer, single
  position cap) — now read from `supervisor.read_curated_config` (real yaml)
- Catalog OKX BTC preset drift (1h/long-only/insufficient) — now 4h/long+short
  with certified backtest figures
- Equity card reading a stale pair snapshot as the portfolio total — dashboard
  now prefers `aggregate.total_equity_usdt`

## Strengths worth keeping
- **Live activation defense-in-depth:** env gate (`XAUBY_LIVE_ACTIVATION_ENABLED`)
  → target cert → preset cert → tested connection <30 min → TOTP + Trade PIN +
  CSRF + origin check → engine-slot reservation → audit log. Risk-increasing
  profile edits force live re-approval (`LIVE_ADDITIVE_RISK_KEYS`).
- **Credential handling:** AES-256-GCM envelope with AAD bound to
  tenant+target+version, per-encrypt nonce, 0600 materialization under a
  tmpfs `RuntimeDirectory`, decrypt helper isolated from the web process.
- **Bounded tenant config:** UI can only select certified presets and bounded
  risk values; `apply_profile` compiles them — tenants can never inject raw
  strategy/engine config.
- **State schema v2** (`aggregate`/`by_symbol`) consumed consistently by TUI
  and web after this cycle's fixes; staleness surfaced (`stale` >120 s state,
  >5 s price).
- **Manual trading flow:** preview→challenge (60 s TTL, digest)→PIN-confirmed
  execution with idempotency key, feature-gated by env, fully audited.

## Recommended next steps (in order)
1. Add a startup guard asserting `trading.max_open_positions ==
   risk.max_open_positions >= live pair count` (mirror of `MAX_SANE_RISK_PCT`).
2. Owner Operations UI for approve-live / suspend / tenant slots (F-2).
3. ATR-based preview sizing for manual orders (F-3).
4. Fold the certification pipeline (previous discussion) into catalog
   generation so preset/backtest blocks can never drift by hand again.
