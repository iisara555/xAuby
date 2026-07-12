# xAuby Code Quality Audit - 2026-07-12

## Executive Summary

This audit reviewed the current `main` worktree, including uncommitted CDC stop-and-reverse and WebUI changes. The strongest current risks are not lack of tests; the repo has a broad suite. The risks are drift between the active whitelist/config and older tests, duplicated live-entry guard logic, and large modules where order lifecycle behavior is hard to reason about.

Current test health is blocked at collection by an untracked test that imports a missing function. A focused live/trading/webui suite also fails in older audit tests because test fixtures still reference removed or inactive symbols (`XAUTUSDT`, `BTCUSDT`) under strict whitelist mode and because some live broker tests stub exchange orders as dicts while the live broker now expects `Order` objects.

## Evidence Collected

- `git status --short --branch`: branch is `main`; dirty worktree includes CDC/replay/dispatch changes, WebUI login changes, untracked `tests/test_unrealized_pnl_net.py`, and untracked `weekly_reviews/review_2026-07-05.md`.
- `git diff --stat`: 12 tracked files changed, 866 insertions and 92 deletions.
- `wc -l`: largest Python hotspots are `xauby/engine/loop.py` (2419 lines), `xauby/engine/orders.py` (2208), `xauby/webui/server.py` (1175), `xauby/database/db.py` (1076), `xauby/observability/replay.py` (971).
- `./venv/bin/python -m pytest --collect-only -q`: 984 tests collected, then collection fails on `tests/test_unrealized_pnl_net.py`.
- Focused suite: `tests/test_audit_fixes.py tests/test_trading_engine.py tests/test_short_dispatch.py tests/test_cdc_long_short.py tests/test_replay_shorts.py tests/test_backtest_runtime_merge.py tests/test_webui.py` produced 96 passed, 17 failed.
- `./venv/bin/python scripts/scan_secrets.py`: one medium finding in `xauby/webui/static/login.js` on `password.required`; appears to be a scanner false positive, but should be allowlisted or renamed.

## Findings

### High - Reverse open path bypasses normal entry guard parity

The new stop-and-reverse helper in `xauby/engine/loop.py` opens the reverse position after a successful close with only state, RegimeRouter, and max-position checks (`open_reverse_after_close`, around lines 2005-2050). It does not reuse the same full entry safety gates used for normal idle entries around lines 1930-1969. Reverse entries must intentionally bypass loss/re-entry cooldowns because the normal CDC stop-and-reverse case is a fresh opposite entry immediately after a losing close; applying loss cooldown here reintroduces the phase-lock bug. They should still respect operator/safety guards such as Telegram pause, WebSocket reconnect cooldown, macro guard, RegimeRouter, max-open limits, and live flat confirmation. For reverse-to-short, `execute_open_short` also lacks the Telegram pause check that `execute_buy` has in `xauby/engine/orders.py` lines 1170-1181.

Impact: a live stop-and-reverse could open a fresh short immediately after close even while a global pause or reconnect cooldown is intended to block new entries, or it could be blocked by loss cooldowns and only reverse after profitable exits.

Recommended fix: extract one shared `entry_allowed(symbol, side, action_context)` guard with an explicit `reverse_after_close` action context. In that context, bypass loss/re-entry cooldown checks but keep Telegram pause, reconnect cooldown, macro guard, RegimeRouter, max-open, and live flat-confirmation checks. Add tests for both normal and reverse contexts.

### High - Full test collection is currently broken

Untracked `tests/test_unrealized_pnl_net.py` imports `estimate_net_unrealized_pnl` from `xauby.engine.loop` at line 5, but that symbol does not exist. This blocks full pytest collection.

Impact: any CI or operator workflow that runs pytest over the whole test tree will fail before executing tests.

Recommended fix: either implement `estimate_net_unrealized_pnl` as a small pure helper or remove/rename the untracked test until the implementation exists. If implemented, use it from `LoopMixin.update_state_json` so the test covers production behavior.

### High - WebUI labels net PnL but engine exports gross unrealized PnL

`xauby/engine/loop.py` computes `position.unrealized_pnl` as gross price movement at lines 869-874. `xauby/webui/server.py` forwards that value as `position.unrealized_pnl` and separately expects `unrealized_pnl_gross`, fees, and funding fields at lines 477-483. `xauby/webui/static/app.js` labels `pos.unrealized_pnl` as "Net PnL" at lines 838-839.

Impact: the operator UI can show gross PnL as net PnL, overstating live position quality by entry fee, exit fee, and funding. The untracked `test_unrealized_pnl_net.py` is pointing at this real gap.

Recommended fix: add one pure net-unrealized PnL helper that returns gross, estimated entry fee, estimated exit fee, funding, net PnL, and net percentage. Export both `unrealized_pnl` as net and `unrealized_pnl_gross` explicitly, or rename UI labels so gross/net semantics cannot drift.

### High - Strict whitelist source of truth has drifted from older tests and docs

`coin_whitelist.json` now has one enabled asset, `XAU`, resolving to `XAUUSDT` with `xauby_actionzone`, live mode, long/short enabled, and active per-pair strategy parameters such as `fresh_zone_window: 1`. Older tests in `tests/test_audit_fixes.py` still force or assert `XAUTUSDT` and `BTCUSDT` specs at lines 33-39, and several engine tests still assume `BTCUSDT` exists. Under `architecture.whitelist_strict`, those symbols fail with missing strategy or unknown context.

Impact: important regression tests for sim/live gating, reconcile, semi-auto symbol targeting, and PnL accounting no longer validate the active architecture.

Recommended fix: update tests to build an explicit temporary whitelist fixture when they need multi-symbol live/sim mixes. Do not depend on deployed `coin_whitelist.json` for multi-symbol regression tests.

### Medium - Live broker test contract drift hides order lifecycle coverage

`LiveBroker.execute_close` expects `client.place_order` to return an `Order`-like object with `order_id`, `status`, `amount`, and `raw_payload` attributes (`xauby/engine/brokers/live_broker.py` lines 134-148). Several older audit tests stub `place_order` with dictionaries, for example `tests/test_audit_fixes.py` lines 399-405 and 436-442, causing AttributeError before PnL assertions can run.

Impact: tests intended to protect live close, partial fills, and untradeable remainder handling are not currently testing that behavior.

Recommended fix: centralize exchange order test factories in `tests/mocks.py`, and make all live broker/order tests use the same `Order` contract as the exchange gateway interface.

### Medium - Core execution modules are too large for safe review

`xauby/engine/loop.py` and `xauby/engine/orders.py` together exceed 4600 lines and contain strategy dispatch, risk guards, state export, broker routing, order accounting, stop-loss reconciliation, partial fills, and notifications. Recent stop-and-reverse work had to thread logic across strategy, loop, orders, and replay, which increases the chance of parity gaps.

Impact: small feature changes can miss one execution path. Review cost is high, and test failures are harder to localize.

Recommended fix: split the execution surface by responsibility: `entry_guards`, `position_closure`, `reverse_dispatch`, `state_export`, and `order_accounting`. Do this after stabilizing the current test suite.

### Medium - Tooling gate is minimal

`pyproject.toml` only defines build metadata and package discovery. There is no committed lint, formatting, type-check, import-cycle, complexity, or coverage configuration.

Impact: code quality depends on manual review and ad hoc pytest selection. Large-file drift, unused imports, broad exception swallowing, and type contract drift are not caught early.

Recommended fix: add a lightweight, non-invasive tool gate first: `ruff check`, `ruff format --check`, and focused pytest commands. Add mypy/pyright only after core interfaces are typed enough to avoid high-noise adoption.

### Low - Secret scanner needs a WebUI false-positive allowlist

`scripts/scan_secrets.py` flags `xauby/webui/static/login.js:31` for the `password.required` assignment. This appears to be UI auth metadata, not a secret value.

Impact: scanner noise can train operators to ignore real findings.

Recommended fix: add a narrow allowlist for this exact frontend metadata path/key or rename the field to avoid secret-like assignment patterns.

## Remediation Backlog

### Do Now

1. Fix or remove `tests/test_unrealized_pnl_net.py` so full pytest collection works.
2. Make reverse entries reuse normal entry safety guards with explicit context: bypass loss/re-entry cooldowns for `reverse_after_close`, but keep Telegram pause, reconnect cooldown, macro guard, RegimeRouter, max-open, and live flat-confirmation checks.
3. Fix gross/net unrealized PnL semantics in state export and WebUI labels.
4. Convert multi-symbol regression tests to use temporary whitelist fixtures instead of deployed whitelist assumptions.

### Do Next

1. Normalize live order test stubs to return `Order` objects via shared factories.
2. Add a focused CI/preflight command set:
   - `./venv/bin/python -m pytest --collect-only -q`
   - `./venv/bin/python -m pytest tests/test_audit_fixes.py tests/test_trading_engine.py tests/test_short_dispatch.py tests/test_cdc_long_short.py tests/test_replay_shorts.py tests/test_backtest_runtime_merge.py tests/test_webui.py`
   - `./venv/bin/python scripts/scan_secrets.py`
3. Add `ruff` config and run in check-only mode before adopting formatting changes.
4. Add explicit tests for WebUI gross/net/funding display semantics.

### Defer

1. Split `engine/loop.py` and `engine/orders.py` into smaller modules once tests are green.
2. Add static typing to broker/order/state-export boundaries.
3. Add coverage reporting and thresholds after the suite is stable enough not to block on collection.

## Suggested Acceptance Criteria

- Full pytest collection exits 0.
- Focused trading/webui suite exits 0.
- Reverse entries bypass loss/re-entry cooldowns but cannot open when Telegram pause, reconnect cooldown, macro guard, RegimeRouter, max-open, or live flat-confirmation blocks them.
- WebUI "Net PnL" equals gross PnL minus estimated fees and funding for both long and short positions.
- Test fixtures no longer require active deployed symbols except tests explicitly validating deployed config.
