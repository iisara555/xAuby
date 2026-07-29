# BTCUSDT SuperTrend — OKX profit-factor grid

- **Run date:** 2026-07-29
- **Venue/data:** OKX `BTC-USDT-SWAP`, native 4h entries plus native 1d confirmation
- **Strategy:** `supertrend_ema200`
- **Reproduction harness:** `scripts/btc_supertrend_okx_pf_grid.py` and
  `scripts/btc_supertrend_okx_pf_grid_shard.py`
- **Authoritative grid run:** [GitHub Actions 30452921217](https://github.com/iisara555/xAuby/actions/runs/30452921217), commit `e5c0b54`
- **Merged result SHA-256:** `1142f4bd28cdc45dcb97bc1abae542b9382f2a01beadfca72a7a95afdca79af4`
- **Tracked raw grid:** `docs/research/artifacts/btc_supertrend_okx_pf_grid_2026-07-29.results.json.gz`
  (gzip SHA-256 `705028cec0f1d432d8c2c540576317be2d3f5837ad75ee981259af108e6cd90e`)

## Verdict

The complete 576-cell IS/OOS grid does **not** support switching BTC to
long-only + D1. At the current structural settings that shape produced IS PF
0.816 and -1.51% net from 26 trades. Its apparently high OOS PF 3.021 rests on
only 10 trades and does not repair the negative in-sample result.

The highest valid OOS PF came from retaining both directions, applying the D1
gate to LONG entries only, and leaving SHORT entries ungated. All other
structural settings stay equal to live:

```yaml
enable_short: true
use_d1_regime_filter: true
use_d1_regime_filter_long: true
use_d1_regime_filter_short: false
ema_period: 200
atr_period: 10
supertrend_mult: 4.0
confirm_bars: 1
entry_on_flip_only: true
sl_atr_mult: 3.0
trailing_atr_mult: 2.0
exit_on_supertrend_flip: true
exit_on_ema_loss: true
```

This candidate improved OOS PF only modestly, from 1.549 to 1.598, while OOS
net fell from +6.36% to +4.99% and trades fell from 49 to 35. The prudent
decision is therefore to **keep the current live config for now** and retain
LONG-D1/SHORT-ungated as the next candidate for full-history, fold, and forward
shadow validation. No live config or certificate was changed by this run.

## Required comparisons

| case | IS PF / net / MDD / trades | OOS PF / net / MDD / trades | decision |
|---|---:|---:|---|
| current live: long+short, D1 off | 1.494 / +12.10% / 9.81% / 86 | 1.549 / +6.36% / 3.87% / 49 | keep live pending finalist checks |
| long-only + D1, live structure | 0.816 / -1.51% / 4.01% / 26 | 3.021 / +4.34% / 3.10% / 10 | reject: negative IS and minimum-size OOS sample |
| highest valid OOS PF: LONG D1 on, SHORT D1 off | 1.572 / +11.78% / 8.79% / 68 | **1.598** / +4.99% / 3.76% / 35 | next conservative candidate |
| balanced rank: same shape, EMA-loss exit off | **1.641** / +13.08% / 9.06% / 68 | 1.590 / +5.08% / 3.76% / 35 | not preferred before finalist checks |

The balanced cell maximizes `min(IS PF, OOS PF)`, but removing the live
EMA-loss exit is a more material change. The highest-OOS candidate changes only
the LONG D1 gate and preserves the live exit behavior.

## Directional/D1 shape comparison

Best valid cell within each shape after the pre-declared gates:

| shape | best structure | IS PF | OOS PF | OOS net | OOS MDD | OOS trades |
|---|---|---:|---:|---:|---:|---:|
| long-only D1 off | none valid | — | — | — | — | — |
| long-only D1 on | m2.5, a14, SL3, trail1.5, EMA exit on | 1.479 | 1.383 | +1.47% | 2.86% | 17 |
| long+short D1 off | m4, a10, SL3, trail2, EMA exit on | 1.494 | 1.549 | +6.36% | 3.87% | 49 |
| long+short D1 on | m4, a10, SL3, trail2, EMA exit on | 1.305 | 1.501 | +3.24% | 3.62% | 23 |
| LONG D1 on, SHORT D1 off | m4, a10, SL3, trail2, EMA exit on | **1.572** | **1.598** | +4.99% | 3.76% | 35 |
| LONG D1 off, SHORT D1 on | m4, a10, SL3, trail2, EMA exit on | 1.253 | 1.475 | +4.60% | 3.73% | 37 |

The top six OOS cells form one neighboring cluster: LONG D1 on / SHORT D1 off,
multiplier 4, ATR 10, trailing 2, across stop multipliers 2/2.5/3 and both
EMA-exit choices. That reduces, but does not eliminate, the risk that the top
cell is an isolated parameter accident. It also shows that gating SHORT entries
hurt this BTC sample; this directional asymmetry is the reverse of the earlier
XAU result.

## Protocol

- Native 4h selection series: 14,504 bars from 2019-12-16 04:00 UTC through
  2026-07-29 08:00 UTC.
- Native 1d confirmation series: 2,435 bars from 2019-11-27 16:00 UTC through
  2026-07-27 16:00 UTC.
- Chronological split: 70/30 at 2024-08-03 04:00 UTC. OOS receives 300 prior
  4h bars for indicator warmup, and those bars cannot trade.
- Grid: 6 direction/D1 shapes × 96 structural cells = **576 cells**.
- Structural axes: SuperTrend multiplier 2.5/3/3.5/4; ATR 10/14; stop ATR
  2/2.5/3; trailing ATR 1.5/2; EMA-loss exit off/on. EMA 200, one confirmation
  bar, flip-only entry, and SuperTrend-flip exit remain fixed.
- Validity gate: IS trades >= 30, OOS trades >= 10, and positive net in both.
  **99** cells passed and **0** cells failed to execute.
- Ranking: OOS PF under the gate; balanced ranking maximizes
  `min(IS PF, OOS PF)` under the same gate.
- Replay uses the production config resolver and preserves configured fee,
  slippage, funding, sizing, and exit semantics.

The eight grid shards each completed 72 cells. Their duplicate-free union was
verified to contain all 576 declared IDs before producing the merged result.

## D1 correctness and execution boundary

The pre-run audit found that the previous SuperTrend implementation did not
consume the D1 flags, so an initial exploratory run in which D1 variants
collapsed to identical results was discarded. The authoritative run above uses
a config-gated D1 confirmation on the last **closed** daily candle, with separate
LONG and SHORT controls and an explicit no-lookahead timestamp check. Defaults
remain off, so this research code does not alter current live behavior.

All eight corrected grid shards succeeded. GitHub rejected the separate
lightweight finalist job before runner assignment because of the account
billing/spending-limit gate. Consequently, optional full-history finalist and
five-fold checks are marked **not run**; they are not silently treated as
passing. This result is a completed grid report, not a new certificate.

Production remains outside this research boundary. The D1 implementation is on
the research branch, and neither tenant config nor the live engine was changed
or restarted.

## Limitations

- OOS participates in ranking across 576 cells and is not a pristine unseen
  holdout.
- The winning candidate has 35 OOS trades; PF uncertainty remains material.
- Historical OKX funding is represented by the configured flat approximation.
- Full-history, five-fold, and forward-shadow evidence are still required
  before replacing the current certificate or applying a live config change.
