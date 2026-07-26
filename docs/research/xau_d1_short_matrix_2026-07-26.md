# XAU 2x2 — the regime router is not needed

**Date:** 2026-07-26. **Harness:** `scripts/xau_d1_short_matrix.py`.
**Data:** OKX XAUT-USDT 4h + 1d, 8808 bars, 2022-07-19 → 2026-07-26 (4.02y).
**Asked:** prove option E (regime router for XAU) is actually good, and that the
system supports it, before doing anything.

**Answer: option E is unnecessary. A one-key config change dominates it.**

## Why the router cannot do what option E described

`RegimeRouter.validate_mapping` resolves every mapping target against
`available_strategies()` (`xauby/engine/regime_router.py`), so the router maps
regime → **strategy id**. It cannot route between two *config variants* of the
same strategy, which is what option E asked for (long-only+D1 vs long+short of
`xauby_actionzone`). Implementing E would have meant registering duplicate
strategy plugins or extending the mapping schema.

None of that is needed, because **`xauby_actionzone` already contains a
per-direction regime gate**. In `strategies/cdc_action_zone/strategy.py`,
`use_d1_regime_filter` gates both sides symmetrically:

- longs require D1 zone in `GREEN/YELLOW/ORANGE` (line ~438)
- shorts require D1 zone in `RED/BLUE/LBLUE` (line ~472)

So "long+short **with** D1 confirmation" is a legal, already-supported config
that no certificate had ever measured. That is the cell this document tests.

## The 2x2

|  | D1 off | D1 on |
|---|---|---|
| long-only | factorial cell | **cert** config |
| long+short | **DEPLOYED** | **candidate** |

### Continuous 4-year run — the headline numbers

| variant | n | WR% | PF | net% | MDD% | CAGR% | Calmar | Sharpe |
|---|---|---|---|---|---|---|---|---|
| long-only  D1 off | 155 | 38.71 | 1.63 | 64.41 | 11.12 | 13.32 | 1.20 | 1.10 |
| long-only  D1 on **(cert)** | 110 | 44.55 | **1.96** | **72.86** | **9.22** | 14.76 | **1.60** | **1.35** |
| long+short D1 off **(DEPLOYED)** | 296 | 32.77 | **1.17** | 31.02 | **17.66** | 7.03 | 0.40 | 0.51 |
| long+short D1 on **(candidate)** | 171 | 39.18 | 1.51 | 64.56 | 12.20 | 13.35 | 1.09 | 1.07 |
| *buy & hold gold* | — | — | — | *136.68* | *25.11* | *23.90* | *0.95* | — |

### The current gold drawdown (2026-03 → 2026-06, peak 2026-02)

| variant | compounded | +months | trades |
|---|---|---|---|
| long-only D1 off | +9.15% | 2/4 | 27 |
| long-only D1 on (cert) | +1.24% | 1/4 | 13 |
| long+short D1 off (DEPLOYED) | **+17.76%** | 3/4 | 57 |
| long+short D1 on (candidate) | **+13.73%** | 3/4 | 35 |
| buy & hold gold | **-23.21%** | — | — |

## 1. The candidate strictly dominates the deployed config

| measure | candidate | deployed | winner |
|---|---|---|---|
| PF | **1.51** | 1.17 | candidate |
| net % | **64.56** | 31.02 | candidate |
| MDD % | **12.20** | 17.66 | candidate |
| Sharpe | **1.07** | 0.51 | candidate |
| Calmar | **1.09** | 0.40 | candidate |
| trades (cost drag) | **171** | 296 | candidate |
| worst month % | **-6.18** | -12.01 | candidate |
| current drawdown | +13.73 | **+17.76** | deployed |

**Seven of eight axes favor the candidate.** The single loss is the current
drawdown window, where the candidate still returns +13.73% — against the cert
config's +1.24% and buy-and-hold's -23.21%. It gives up ~4pp of crisis alpha and
buys back 5.5pp of max drawdown, half the tail risk, and 42% fewer trades.

**There is no defensible reason to keep `use_d1_regime_filter: false` on XAU.**

## 2. The mechanism: unconfirmed shorts in uptrends

Reading the D1-off column tells you what actually breaks:

| D1 off | PF |
|---|---|
| long-only | 1.63 |
| long+short | **1.17** |

Adding the short side *without* regime confirmation cuts PF from 1.63 to 1.17.
Shorts were firing into uptrends — which matches the deployed config's worst
months all being bull months (-12.01%, -11.53%). Turning D1 on restores PF to
1.51 while keeping the bear/chop edge, because the filter blocks shorts unless
the daily zone is genuinely bearish.

So the short side is not the problem. **Shorting without daily confirmation is.**

## 3. All four configs lose to buy-and-hold on raw return

| | net % (4.02y) | MDD % | Calmar |
|---|---|---|---|
| best config (cert) | 72.86 | 9.22 | 1.60 |
| **buy & hold gold** | **136.68** | 25.11 | 0.95 |

Gold rose 136.68% over this window. The best config captured **53%** of that.
Three of four configs beat buy-and-hold on a risk-adjusted basis (Calmar 1.60 /
1.20 / 1.09 vs 0.95) — the deployed config is the only one that loses on **both**
raw return and risk-adjusted return.

This matters because `derivatives.max_leverage` is **1**: there is no headroom to
lever a lower-drawdown strategy up to match buy-and-hold's return. Over this
window the honest value proposition is **less than half the drawdown, at roughly
half the return** — not outperformance. A four-year gold bull is the hardest
possible tape for a strategy that goes to cash, so this is a fair sample for
that specific weakness and an unfair one for the short side.

## 4. Does the system support the candidate? Yes — one key

`coin_whitelist.json` → XAU → `strategy_params.use_d1_regime_filter: false` → `true`.

Everything else is already in place:

- `confirm_timeframe: "1d"` is **already set** on the XAU whitelist entry.
- No validation rejects `enable_short: true` + `use_d1_regime_filter: true`;
  `validate_config` only checks RSI bounds and `vol_min_ratio`.
- No regime router, so no `regime_router_enabled` and no per-pair
  `regime_router_live_confirmed` sign-off gate.
- No code change, no new strategy plugin, no indicator work.

**One correction worth stating plainly:** this is *not* a case of data that is
already loaded simply becoming consumed. `SymbolContext.timeframe_regime`
(`xauby/engine/symbol_context.py:235`) returns `None` when
`use_d1_regime_filter` is falsy, so `loop.py` currently never loads 1d candles
for XAU. Flipping the key **starts** a 1d candle load each tick. That is the
designed path for the property, but it means:

- a controlled restart is required (not a hot config reload),
- 1d history must be warm enough for the CDC zone on D1 (EMA-based; the pair
  already fetches `backfill_target_bars: 250`),
- and one extra candle fetch per tick per pair.

## Recommendation

Change `use_d1_regime_filter` to `true` for XAU. It is strictly better than what
is deployed on seven of eight measures, it keeps most of the crisis alpha that
justified enabling shorts, and it needs no router.

This is **not** a certificate. Before live:

1. Re-run the acceptance protocol on the candidate (WFA + bootstrap), since the
   config would then be a *new* deployed config with no certificate of its own —
   exactly the failure this whole thread exists to stop.
2. The bear sample remains **n=1 complete month**; the 4-month live drawdown is
   supporting evidence, not certification.
3. Position-aware rollout: XAU has `disable_stop_loss: true` and
   `position_pct: 0.95`, so the switch must wait for a flat position.

## Limitations

- Monthly-reset figures in the by-phase section inflate totals relative to the
  continuous run (e.g. candidate +118.60% monthly vs +64.56% continuous). **Use
  the continuous table for any headline claim**; phase numbers are attribution
  only.
- XAUT-USDT is a proxy for XAU-USDT-SWAP (correlation 0.99/1.00, measured in
  `venue_data_revalidation_2026-07-26.md`); its spot book is thinner than the
  swap, so fills are if anything optimistic.
- 4.02 years covers one gold regime cycle with a single clean bear month. The D1
  filter's benefit is measured mostly against bull whipsaw, not against a
  sustained bear market.
- Funding is the flat `backtest.funding_rate_8h` approximation. The candidate
  holds far fewer short-bars than the deployed config, so it is less sensitive to
  that assumption — which is itself an argument in its favor.
- Buy-and-hold is always invested and measured on 1d closes; it is a benchmark,
  not a matched-exposure comparison.
