# XAU deployed config — what is actually wrong with it, by regime

**Date:** 2026-07-26. **Harness:** `scripts/xau_phase_breakdown.py`.
**Data:** OKX XAUT-USDT 4h/1d, 40 complete months (2023-03 → 2026-06).
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
| deployed | bull | 37 | 19 | **+26.75** | 0.83 | **-12.01** | 506 |
| deployed | bear | 1 | 1 | **+9.52** | 9.52 | +9.52 | 8 |
| deployed | sideways | 2 | 2 | **+17.18** | 8.28 | +5.87 | 19 |
| deployed | ALL | 40 | 22 | +62.66 | 1.42 | -12.01 | 533 |
| cert | bull | 37 | 24 | **+164.38** | 2.74 | -3.36 | 200 |
| cert | bear | 1 | 1 | **+1.83** | 1.83 | +1.83 | 4 |
| cert | sideways | 2 | 0 | **-2.10** | -1.05 | -2.10 | 3 |
| cert | ALL | 40 | 25 | +163.55 | 2.53 | -3.36 | 207 |

Head-to-head, compounded: **bear +7.69pp to deployed, sideways +19.28pp to
deployed, bull -137.63pp to deployed.**

## 1. The bear/chop claim is real — and it is happening now

Gold peaked at 5340 in 2026-02 and closed 2026-06 at 4072: a **-22.9% decline**
over four months. That is the regime the deployed config is built for, and it is
the current market.

| 2026-03 → 2026-06 | compounded | +months | trades |
|---|---|---|---|
| **deployed** | **+17.76%** | 3/4 | 57 |
| cert | +1.24% | 1/4 | 13 |
| **buy & hold gold** | **-22.88%** | — | — |

| month | phase | deployed | cert |
|---|---|---|---|
| 2026-03 | bull | +8.02% (n=15) | +4.58% (n=7) |
| 2026-04 | bull | +1.58% (n=14) | -0.35% (n=4) |
| 2026-05 | bull | -3.04% (n=16) | -2.85% (n=2) |
| 2026-06 | sideways | **+10.69%** (n=12) | 0.00% (n=0) |

In 2026-06 the cert config took **zero trades** — the D1 filter kept it out. The
deployed config made +10.69%. In the four-month decline it beat buy-and-hold by
about **40 percentage points**. That is genuine crisis alpha, not noise.

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

| 2023-03 → 2026-06 | return | max DD |
|---|---|---|
| deployed | +62.66% | — |
| cert | +163.55% | — |
| **buy & hold gold** | **+121.86%** | -25.11% |

The deployed config captured roughly **half** of what doing nothing captured. The
cert config beat buy-and-hold, but note most of that is beta: gold itself rose
+190.94% over the bull stretch, so long-only trend-following in a historic gold
bull is largely tracking the asset.

**(c) Its damage is concentrated in bull, which dominates gold's history.** All
four of the deployed config's worst months are bull months: -12.01% (2024-07),
-11.53% (2024-06), -7.02% (2023-04), -6.44% (2023-07). Bull was 37 of 40 months
(90%) here. A config whose failure mode
is "sustained uptrend" is structurally expensive on an asset that trends up.

Turnover compounds this: 533 trades vs 207 for the same period, so ~2.6x the
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
| bull | cert (long-only + D1) | +137.63pp |
| bear | deployed (long+short) | +7.69pp |
| sideways | deployed (long+short) | +19.28pp |

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
  deployed config returns **+31.02%** (`venue_data_revalidation_2026-07-26.md`),
  not the +62.66% that monthly compounding produces here. The spans also differ
  (40 months vs 4.02 years). **Use +31.02% / PF 1.17 as the headline; use this
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
