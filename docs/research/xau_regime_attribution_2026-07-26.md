# XAU deployed config — what is actually wrong with it, by regime

> **CORRECTION 2026-07-27.** Every table here was regenerated. The original
> harness traded its 300-bar warmup lead-in, so consecutive monthly windows
> overlapped by roughly a month and every per-month figure was inflated — the
> "deployed" config was double-counting trades (533 reported, 311 real). It also
> covered 40 months rather than the 48 available, because unlabelled early months
> were dropped entirely instead of counted in the total.
>
> **Note this document compares "deployed" against "cert", and at the time both
> labels meant different configs than they do now** — "deployed" was
> `long+short, D1 off`, which was replaced on 2026-07-26. Rows are relabelled
> accordingly. Regenerated via `scripts/xau_windowed_regen.py`; continuous
> figures never used windowing and are untouched.
>
> The largest single change: `long-only + D1 on` monthly-compounded was reported
> at **+163.55%** and is **+69.22%**. It trades least, so the warmup overlap
> inflated it most. The monthly view no longer shows a large gap between the two
> configs; the continuous view still does, and always did.

**Date:** 2026-07-26. **Harness:** `scripts/xau_phase_breakdown.py`.
**Data:** OKX XAUT-USDT 4h/1d, 48 complete months (2022-07 → 2026-06).
**Regenerated:** 2026-07-27 via `scripts/xau_windowed_regen.py`.
**Question asked:** the deployed config scores well in bear trends — shouldn't it
be certified?

**Short answer: the bear-trend claim is correct, and it holds up on the live
market right now. But it is not what the July PDF claims, and over the full
sample the deployed config loses to simply holding gold.**

## Method

Both configs replay over the **same** candles, month by month, each month with a
300-bar warmup lead-in. Phase labels use the BTC certificate's convention (1d
close vs EMA200 + slope, from closes strictly before each month, no look-ahead).

| variant | meaning |
|---|---|
| **deployed** | long+short, D1 filter off, ROI ladder — what runs live today |
| **cert** | long-only, D1 filter on — what `xau_4strategy_comparison_2026-07-13.md` measured |

## Result by phase

| config | phase | months | +months | compounded % | avg mo % | worst mo % | trades |
|---|---|---|---|---|---|---|---|
| long+short D1 off | bull | 37 | 18 | **+5.54** | — | **-12.02** | 249 |
| long+short D1 off | bear | 1 | 1 | **+5.99** | — | +5.99 | 4 |
| long+short D1 off | sideways | 2 | 2 | **+17.67** | — | +2.28 | 9 |
| long+short D1 off | ALL | 48 | 25 | +39.37 | — | -12.02 | 311 |
| long-only D1 on | bull | 37 | 21 | **+53.60** | — | -2.85 | 96 |
| long-only D1 on | bear | 1 | 1 | **+3.30** | — | +3.30 | 2 |
| long-only D1 on | sideways | 2 | 0 | **-1.42** | — | -1.42 | 2 |
| long-only D1 on | ALL | 48 | 25 | +69.22 | — | -2.85 | 116 |

Head-to-head, compounded: **bear +2.69pp to long+short, sideways +19.09pp to
long+short, bull -48.06pp to long+short.** The direction of every comparison is
the same as first published; only the magnitudes shrank.

## 1. The bear/chop claim is real — and it is happening now

Gold peaked at 5340 in 2026-02 and closed 2026-06 at 4072: a **-22.9% decline**
over four months. That is the regime the deployed config is built for, and it is
the current market.

| 2026-03 → 2026-06 | compounded | +months | trades |
|---|---|---|---|
| **long+short D1 off** | **+8.75%** | 2/4 | 25 |
| long-only D1 on | -4.74% | 0/4 | 3 |
| **buy & hold gold** | **-22.88%** | — | — |

| month | phase | long+short D1 off | long-only D1 on |
|---|---|---|---|
| 2026-03 | bull | +0.12% | -1.95% |
| 2026-04 | bull | -3.02% | -2.85% |
| 2026-05 | bull | -2.66% | 0.00% |
| 2026-06 | sideways | **+15.05%** | 0.00% |

In 2026-05 and 2026-06 the long-only config took **no trades at all** — the D1
filter kept it out — while long+short made +15.05% in June. Across the decline it
beat buy-and-hold by about **31 percentage points**. That is genuine crisis
alpha, not noise. (First published as +17.76% vs +1.24% and "40 percentage
points"; the direction held, the magnitude did not.)

**So the answer to "shouldn't this be certified?" is: yes, but for this.** The
evidence supports certifying it as a **falling/choppy-market specialist**. It
does not support the PDF's "PF 2.00, MDD 8.3%, all-weather" framing.

## 2. What is actually wrong — three things, none of them "it loses money"

**(a) The PDF describes a different config.** It reports PF 2.00 / MDD 8.3% and
labels it the live config. Those numbers belong to long-only + D1
(`xau_deployed_config_reproduction_2026-07-26.md`). The deployed config measures
PF 1.17 / MDD 17.66% over 4 years. The defect is the document, not the strategy.

**(b) Over the full sample it loses to holding gold.** This is the strongest
argument against a general-purpose certificate:

| monthly-compounded, 48 months | return |
|---|---|
| long+short D1 off | +39.37% |
| long-only D1 on | +69.22% |
| *buy & hold gold, 2023-03 → 2026-06* | *+121.86% (max DD -25.11%)* |

Both configs captured well under what doing nothing captured. The corrected
figures make this **worse**, not better: `long-only D1 on` was reported at
+163.55%, comfortably ahead of buy-and-hold, and is actually +69.22% — barely
half of it. The claim that the cert config beat buy-and-hold does not survive
the correction on this measure. (The continuous 4-year run tells the same story:
+72.86% against gold's +136.68%.)

**(c) Its damage is concentrated in bull, which dominates gold's history.** Its
worst month is -12.02%, in bull, and its bull-phase total is +5.54% against the
long-only config's +53.60%. Bull is 37 of the 40 labelled months here. A config
whose failure mode is "sustained uptrend" is structurally expensive on an asset
that trends up.

Turnover compounds this: 311 trades vs 116 for the same period, so ~2.7x the
fee, slippage, and funding drag.

## 3. Why a bear certificate cannot be issued on statistics alone

**The bear sample is one complete month.** Sideways is two. No amount of
favorable arithmetic makes n=1 a certification. The four-month live drawdown is
strong supporting evidence and it is out-of-sample relative to any tuning, but it
is still four months.

Certifying the bear claim properly needs gold's earlier bear markets
(2013-2015, 2021-2022), which requires history this venue does not have —
XAU-USDT-SWAP starts 2025-04, XAUT-USDT starts 2022-07.

## 4. The two configs are complementary, which points somewhere specific

| regime | winner | margin |
|---|---|---|
| bull | long-only + D1 | +48.06pp |
| bear | long+short | +2.69pp |
| sideways | long+short | +19.09pp |

This is not "one config is better." It is two specialists with opposite
strengths — which is precisely the case the **regime router** in this codebase
exists to handle, and it is currently **off** for XAU
(`regime_router_enabled: false`). Routing gold to long-only+D1 in confirmed
uptrends and to long+short in breakdowns/chop is the option this data actually
argues for, and neither certificate has ever evaluated it.

That is a proposal, not a result. It would need its own validation, and the
router's live path is gated behind `regime_router_live_confirmed` per pair.

## Limitations — read before quoting any number here

- **The monthly-reset method is for regime attribution, not for headline
  returns.** Each month restarts at balance 1000 and closes flat, so positions do
  not carry across month boundaries. The continuous 4-year run of the same
  `long+short D1 off` config returns **+31.02%**
  (`venue_data_revalidation_2026-07-26.md`), not the +39.37% that monthly
  compounding produces here. **Use the continuous figures as headlines; use this
  document only for the by-regime comparison.**
- **n=1 bear month, n=2 sideways months.** Stated again because it is the single
  biggest constraint on every conclusion above.
- Phase labels lag by design: the EMA200 slope stayed "bull" through 2026-03..05
  while gold was already falling ~23%. Hence the separate drawdown table — the
  phase split alone understates how much of the deployed config's edge came from
  the decline.
- Buy-and-hold is measured on 1d closes and is always invested; the strategies
  are not. It is a benchmark, not a matched-exposure comparison.
- XAUT-USDT is a proxy for XAU-USDT-SWAP (correlation 0.99/1.00, measured in
  `venue_data_revalidation_2026-07-26.md`). Its spot book is thinner than the
  swap, so fills here are, if anything, optimistic.
- Funding is the flat `backtest.funding_rate_8h` approximation. The deployed
  config holds shorts, which *receive* funding in the simulator — with 2.6x the
  turnover, funding assumptions matter more for it than for the cert config.
