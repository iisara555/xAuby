# XAU certification run — shorts fail the pre-registered gate

> **CORRECTION 2026-07-27.** Stage 2 (walk-forward) and Stage 3 (bootstrap) were
> regenerated: the original harness traded its 300-bar warmup lead-in, so monthly
> windows overlapped and every per-month figure was inflated. **Stage 1 — the
> acceptance gate, and the verdict of this document — is unaffected**, because it
> uses continuous full-frame replays with no windowing. The gate edges, the PF /
> MDD / Sharpe table, and the conclusion that no long+short config is certifiable
> all stand exactly as published. Regenerated via
> `scripts/xau_windowed_regen.py` through `xauby.backtest.walkforward`.

**Date:** 2026-07-26. **Harness:** `scripts/certify_xau_candidate.py`.
**Data:** OKX XAUT-USDT 4h + 1d, 8809 bars, 2022-07-19 → 2026-07-26 (4.02y).
**Cross-check:** `scripts/evaluate_okx_xau_migration.py` on native XAU-USDT-SWAP.

**Verdict: no long+short XAU config is certifiable on this data. The config that
survives is `long-only, D1 filter ON` — which is what the July research actually
measured all along.**

This reverses the recommendation in `xau_d1_short_matrix_2026-07-26.md`. That
document was right that long+short+D1 dominates the *deployed* config; it was
wrong to recommend it without first applying the repo's own admission criterion.

## Stage 1 — acceptance gate (pre-registered)

`bot_config.yaml -> backtest.acceptance` states the bar for turning shorts on:
net-positive **and** beating the equivalent long-only config by
`min_profit_edge_pp: 5.0`.

| long+short config | net % | long-only baseline | net % | edge | result |
|---|---|---|---|---|---|
| long+short D1 on | 64.59 | long-only D1 on | 72.86 | **-8.27pp** | ❌ FAIL |
| long+short D1 off *(deployed)* | 31.04 | long-only D1 off | 64.41 | **-33.36pp** | ❌ FAIL |

**Admissible long+short configs: NONE.**

Independent cross-check — the separate zone simulator in
`evaluate_okx_xau_migration.py`, on ~1y of **native XAU-USDT-SWAP** (2259 bars):

| mode | net % | MDD % | PF |
|---|---|---|---|
| long_only | 23.03 | 20.96 | 2.02 |
| short_only | 3.37 | 15.67 | 1.14 |
| long_short | 27.18 | 25.27 | 1.52 |

edge **+4.15pp** → **FAIL** (below 5.0pp).

Two datasets, two independent simulators, same verdict. Note the direction
differs: on the short native window shorts add a little (+4.15pp, still short of
the bar); over four years they subtract. That is consistent with the recent gold
decline flattering the short side, which is exactly the sampling trap documented
in `xau_regime_attribution_2026-07-26.md`.

## Continuous reference — all four cells

| config | n | PF | net % | MDD % | Sharpe | Calmar |
|---|---|---|---|---|---|---|
| long-only D1 off | 155 | 1.63 | 64.41 | 11.12 | 1.10 | 1.20 |
| **long-only D1 on** | 110 | **1.96** | **72.86** | **9.22** | **1.35** | **1.60** |
| long+short D1 off *(deployed)* | 296 | 1.17 | 31.04 | 17.66 | 0.51 | 0.40 |
| long+short D1 on | 171 | 1.51 | 64.59 | 12.20 | 1.07 | 1.09 |
| *buy & hold gold* | — | — | *136.68* | *25.11* | — | *0.95* |

`long-only D1 on` wins every risk-adjusted axis. **The deployed config is last on
every axis.**

## Stage 2 — walk-forward, frozen config, 48 months

`long-only D1 on`, no per-window re-optimization, 300-bar warmup lead-in per month:

| phase | months | +months | compounded % | worst month % | trades |
|---|---|---|---|---|---|
| bull | 37 | 21 | +53.60 | -2.85 | 96 |
| bear | 1 | 1 | +3.30 | +3.30 | 2 |
| sideways | 2 | 0 | -1.42 | -1.42 | 2 |
| **ALL** | **48** | **25** | **+69.22** | **-2.85** | 116 |

25 of 48 months positive (52%); worst month -2.85%.

For reference, `long-only D1 off` scores 30/48 and +67.71% compounded with a
worse worst-month (-6.33%) and 163 trades — near-identical on return, worse on
tails and cost. The D1 filter is buying tail protection, not return.

*(Published as +163.55% over 40 months. That figure was inflated by the traded
warmup and by dropping the 8 months whose phase could not be labelled; it is
recorded here so it cannot be reintroduced.)*

## Stage 3 — bootstrap, 10,000 resamples of monthly returns

| config | median | 90% CI | P(profitable) |
|---|---|---|---|
| long-only D1 on | +68.65% | [+29.42%, +125.10%] | 99.96% |
| long-only D1 off | +68.27% | [+20.84%, +134.99%] | 99.63% |
| *long+short D1 off* | *+39.58%* | *[-14.82%, +127.76%]* | *86.79%* |
| *L:D1off S:D1on (now live)* | *+67.33%* | *[+4.75%, +164.11%]* | *96.46%* |

**Read this with the caveat, not the headline.** A P(profitable) near 100% is
*not* evidence of safety. Note the corrected spread is far more informative than
the original: the config now running live has a 90% lower bound of **+4.75%** —
close to zero — where the long-only configs sit near +20-29%.

The bootstrap resamples months i.i.d. from a window in which **37 of the 40
labelled months are bull** while gold rose 137%, so every resample is another
bull-heavy sequence. It measures sampling variability *within this regime mix*;
it says nothing about a sustained bear market, and it discards autocorrelation
and drawdown path. A near-100% figure here mostly restates that gold went up.

## What this means for the deployed config

The deployed config (`long+short, D1 off`) fails the admission gate by 33
percentage points, is last on every continuous risk metric, and loses to
buy-and-hold on both raw and risk-adjusted return. **Nothing in this run supports
keeping it.**

## What is being given up by dropping shorts

Stated plainly, because it is real: in the current gold decline (2026-03 → 06,
peak 2026-02) `long-only D1 on` returned **-4.74%** while `long+short D1 off`
returned **+8.75%** and the now-live `L:D1off S:D1on` **+11.22%** (buy-and-hold
-23.21%). Switching to long-only forfeits that. *(First published as +1.24% /
+17.76% / +13.73% — inflated by the traded warmup; the ordering is unchanged and
the long-only config is now negative rather than merely flat.)*

The pre-registered gate still wins, and the reason matters more than the result:
using a favorable 4-month window to overturn a criterion fixed in advance over
four years is precisely the move that produced the mislabeled July certificate.
The gate was committed before these numbers existed; that is what makes it
usable.

**If the criterion itself is wrong, change it as a deliberate policy decision —
not as a way to make this result disappear.** A defensible case exists that a
net-return-only gate is the wrong test for a hedge component, and that a
drawdown- or Calmar-based criterion would suit it better. That change must be
argued and committed *before* re-running, or it is just post-hoc selection with
extra steps. Note it would not rescue the deployed config either way: long-only
D1 on beats long+short D1 on on MDD (9.22% vs 12.20%) and Calmar (1.60 vs 1.09)
as well as on return.

The legitimate route back to shorts: accumulate real bear-market coverage (the
sample here is **one** complete bear month), or pre-register a different
criterion, then re-run this harness unchanged.

## Scope and limitations

- **Certified on a proxy.** XAUT-USDT, correlation 0.99/1.00 to XAU-USDT-SWAP
  (`venue_data_revalidation_2026-07-26.md`). Native swap history is 1.29y, too
  short for this protocol. Spot books are thinner than the swap, so fills are if
  anything optimistic.
- **One gold cycle, one clean bear month.** The single largest limitation on
  every number above.
- **Monthly-reset difference.** WFA compounding (+69.22%) and the continuous run
  (+72.86%) are close here but are not the same measure — each WFA month restarts
  flat. Use the continuous table for headline claims; WFA for stability and phase
  attribution.
- **Bootstrap is i.i.d. and regime-bound** — see Stage 3.
- Buy-and-hold is always invested, measured on 1d closes: a benchmark, not a
  matched-exposure comparison. With `max_leverage: 1` there is no headroom to
  lever a lower-drawdown config up to gold's raw return.
- Bar count moved 8808 → 8809 between this run and the 2x2 (a new candle closed),
  shifting net% by ~0.03pp. Immaterial, noted for exact reproducibility.
- This run does **not** certify a config change into live. Applying it requires a
  flat position (XAU has `disable_stop_loss: true`, `position_pct: 0.95`) and a
  controlled restart; enabling D1 also *starts* a 1d candle load that does not
  happen today (`SymbolContext.timeframe_regime` returns None when the flag is
  off).
