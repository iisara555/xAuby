# XAU (4h) — 4-strategy comparison, 13 July 2026

Markdown companion to `xauby_4strategy_gold_comparison_2026-07-13.pdf` (same
directory). The PDF is the presentation copy; this file exists so the numbers are
greppable, diffable, and citable from config and code review.

**This is the certificate for the live XAU configuration.** It supersedes the
config-search recommendations in `../actionzone_config_search_2026-07.md` — see
"Relationship to the earlier study" below.

## Protocol

| Element | Setting |
|---|---|
| Engine | `PositionSimulator` / `ReplayEngine` — the same production replay path live parity is validated against, not a vectorized backtest |
| Data | PAXGUSDT 4h as proxy for XAUUSDT, 2020-08 → 2026-07, 12,810 bars |
| Costs | fee 0.05%, slippage 2 bps, funding 0.004%/8h (matches OKX live) |
| Sizing | `risk_pct` 2%/trade, cap 25% |
| Validation | full period + IS/OOS 70/30 + 5-fold walk-forward + trade-level bootstrap CI (3,000 resamples) + top-5-winner stress test |

Strategies 2–4 were run on their **default configs**, which were tuned for BTC
(Donchian and SuperTrend+EMA200 both tag `required_timeframes: 1h`). This is a
comparison of what happens when they are pointed at gold as-is — not a claim that
they cannot work on gold after retuning.

## Full period (2020-08 → 2026-07)

| Strategy | Net | PF | WR | MDD | Trades | Sharpe | Calmar |
|---|---|---|---|---|---|---|---|
| **ActionZone (live config)** | **+95.9%** | **2.00** | 45.9% | 8.3% | 133 | 1.35 | 1.48 |
| Donchian Trend | +5.5% | 1.43 | 40.2% | 5.0% | 97 | 0.43 | 0.19 |
| SMC Pro | −5.2% | 0.76 | 37.3% | 9.1% | 177 | −0.52 | −0.10 |
| SuperTrend+EMA200 | −0.7% | 0.92 | 42.6% | 3.8% | 68 | −0.10 | −0.03 |

## IS/OOS 70/30

| Strategy | IS Net | IS PF | OOS Net | OOS PF | OOS Sharpe |
|---|---|---|---|---|---|
| **ActionZone** | **+36.7%** | **1.75** | **+50.2%** | **2.27** | 2.26 |
| Donchian Trend | −2.6% | 0.72 | +8.9% | 3.60 | 1.72 |
| SMC Pro | −8.1% | 0.49 | +3.7% | 1.57 | 1.08 |
| SuperTrend+EMA200 | −3.0% | 0.34 | +2.4% | 1.69 | 0.86 |

ActionZone is the only strategy profitable in **both** windows. The other three
lose in IS (2020–2023) and recover in OOS (~2023–2026), which is the gold bull
run (~$1,950 → ~$4,170) — any long-biased trend follower looks good there. That
pattern is the warning sign, not the edge.

## 5-fold walk-forward (profit factor per fold, non-overlapping ~2,562 bars each)

| Strategy | Fold1 | Fold2 | Fold3 | Fold4 | Fold5 | Profitable folds |
|---|---|---|---|---|---|---|
| **ActionZone** | 1.22 | 0.77 | 1.38 | 3.35 | 2.15 | **4/5** |
| Donchian Trend | 0.59 | 0.79 | 0.43 | 1.16 | 4.08 | 2/5 |
| SMC Pro | 0.27 | 0.31 | 0.64 | 1.09 | 1.70 | 2/5 |
| SuperTrend+EMA200 | 0.22 | 0.73 | 0.22 | 0.78 | 1.70 | 1/5 |

## Bootstrap CI + stress test

| Strategy | Trades | PF 90% CI | P(PF<1.0) | PF after cutting top-5 winners | Net after cut |
|---|---|---|---|---|---|
| **ActionZone** | 133 | [1.32, 3.02] | **0.2%** | **1.59** | **+56.1%** |
| Donchian Trend | 97 | [0.84, 2.29] | 13.3% | 0.79 | −2.6% |
| SMC Pro | 177 | [0.55, 1.04] | 92.0% | 0.60 | −8.8% |
| SuperTrend+EMA200 | 68 | [0.53, 1.57] | 60.5% | 0.49 | −4.1% |

`P(PF<1.0)` is the share of 3,000 trade-level resamples that came out losing.

## Conclusion

Keep ActionZone as the sole XAU strategy. There is no statistical basis for
switching or adding another strategy right now. To make Donchian / SMC Pro /
SuperTrend viable on gold they would need to be (1) retuned for gold 4h rather
than run on BTC defaults, (2) re-measured with this same walk-forward + bootstrap
protocol, and (3) checked specifically for edge outside the recent bull leg.

## Relationship to the earlier study

`../actionzone_config_search_2026-07.md` (2026-07-12) evaluated the
then-live config, which used `fresh_zone_window: 1`, and reported **0/5**
profitable walk-forward folds along with a phase-lock hazard: with
`enable_short: true` and `fz1` the reversal entry is always blocked, so the
strategy locks into whichever side it entered first.

`fresh_zone_window: 3` was applied to `coin_whitelist.json` after that study,
which resolves the phase-lock. **This report measures the post-`fz3` config and
finds 4/5 profitable folds** — so the earlier "0/5" figure describes a
configuration that is no longer deployed, and its long-only / D1-filter
recommendations do not apply to the current one.

## Open items against this certificate

Recorded here so the caveats travel with the numbers:

1. **Proxy asset.** The data is PAXGUSDT 4h spot standing in for XAUUSDT-SWAP.
   The PDF notes an OKX cross-check in an earlier round of the same session, but
   that cross-check is not committed. Re-running this protocol on OKX
   XAU-USDT-SWAP data is tracked as P0.1/P0.2 in `../roadmap_2026H2.md`.
2. **Exit-table assumption not stated.** The report does not list the strategy
   parameters it ran with. The top-5-winner stress test drops net by 39.8pp
   (95.9 → 56.1), i.e. ~8.0% per winner — which matches the live
   `minimal_roi` rung-1 cap of 8.0% closely enough to indicate the ladder was
   active in this run. That is good news for parity, and it means **the
   `minimal_roi` ladder is part of what was certified: changing it re-opens
   certification.** Worth confirming directly against the run's config rather
   than inferring it from the stress-test delta.
3. **`partial_tp_pct: 12.0` is unreachable** in both live and replay, because
   `minimal_roi` rung 1 (8.0%) sits below it and a full exit always wins the
   tick. It therefore contributed nothing to these results and should be removed
   from the config as dead settings — see P0.0 in `../roadmap_2026H2.md`.
