# Per-side D1 gating on XAU — hypothesis tested and falsified

**Date:** 2026-07-26. **Harness:** `scripts/certify_xau_candidate.py`.
**Code:** per-side D1 gating added to `xauby_actionzone` (commit `d77cc81`).
**Data:** OKX XAUT-USDT 4h + 1d, 8809 bars, 2022-07-19 → 2026-07-26 (4.02y).

## The hypothesis

The daily CDC zone **lags**. Gating shorts on it means entering a decline only
after the daily has already flipped — visible in the certification data, where
`long-only D1 on` took **zero trades in 2026-06** while gold fell. So: keep the
daily confirmation on longs (it suppresses bull whipsaw) but let shorts fire on
the 4H flip alone.

Testable, plausible, and it required real code: the strategy had one
`use_d1_regime_filter` flag covering both directions.

**Result: falsified on both axes, and the mirror configuration is better.**

## Four-year continuous run

| config | PF | net % | MDD % | Sharpe | Calmar | n | gate edge |
|---|---|---|---|---|---|---|---|
| **long-only D1 on** | **1.96** | **72.86** | **9.22** | **1.35** | **1.60** | 110 | n/a |
| long-only D1 off | 1.63 | 64.41 | 11.12 | 1.10 | 1.20 | 155 | n/a |
| long+short D1 on *(symmetric)* | 1.51 | 64.59 | 12.20 | 1.07 | 1.09 | 171 | −8.27pp ❌ |
| L:D1off S:D1on *(mirror)* | 1.38 | 56.54 | 14.42 | 0.89 | 0.83 | 216 | −7.87pp ❌ |
| **L:D1on S:D1off** ← **requested** | **1.23** | **37.78** | 14.32 | 0.63 | 0.59 | 251 | **−35.08pp** ❌ |
| long+short D1 off *(deployed)* | 1.17 | 31.04 | 17.66 | 0.51 | 0.40 | 296 | −33.37pp ❌ |

The requested variant ranks **5th of 6**, and its **−35.08pp is the worst gate
edge of any cell** — worse even than the deployed config.

## The current gold drawdown — the hypothesis' own use case

2026-03 → 2026-06, gold peaked 2026-02 and fell 22.9%. This is precisely where
"free the shorts so they catch declines earlier" was supposed to pay.

| config | compounded | +months | worst month | n |
|---|---|---|---|---|
| **L:D1off S:D1on** *(mirror)* | **+22.61%** | 3/4 | −3.57% | 49 |
| long+short D1 off *(deployed)* | +17.76% | 3/4 | −3.04% | 57 |
| long+short D1 on *(symmetric)* | +13.73% | 3/4 | −5.18% | 35 |
| **L:D1on S:D1off** ← **requested** | **+9.23%** | 2/4 | −4.66% | 43 |
| long-only D1 off | +9.15% | 2/4 | −1.20% | 27 |
| long-only D1 on | +1.24% | 1/4 | −2.85% | 13 |
| *buy & hold gold* | *−23.21%* | — | — | — |

**The requested variant fails at the thing it was designed for.** In the actual
decline it returned +9.23% — behind the deployed config (+17.76%), behind
symmetric D1-on (+13.73%), and less than half the mirror (+22.61%). Freeing the
shorts did not catch the decline earlier; it caught more noise.

## What the mirror reveals — the mechanism

Gating shorts on D1 and freeing longs beats the requested variant on **every**
axis: PF 1.38 vs 1.23, net 56.54% vs 37.78%, Sharpe 0.89 vs 0.63, and +22.61% vs
+9.23% in the drawdown.

**D1 confirmation matters MORE for shorts than for longs — including during
declines.** That is the opposite of the lag intuition, and the trade counts show
why:

| | trades | net % |
|---|---|---|
| symmetric D1 on | 171 | 64.59 |
| free the shorts (requested) | 251 | 37.78 |

Freeing shorts adds **80 trades and subtracts 27 percentage points**. Those extra
shorts are net-losing.

The reason the lag argument fails: in a *real* decline the daily zone flips and
**stays** RED, so the gate barely delays the good shorts. What it blocks is the
4H flipping RED and back on counter-trend bounces — which happens both in
uptrends and inside declines. The gate removes churn, not opportunity. The
+22.61% mirror result is the cleanest evidence: shorts filtered by D1 did better
during the decline than shorts running free.

Note also that **both** asymmetric cells lose to symmetric D1-on (PF 1.51). The
asymmetry does not help in either direction — the daily filter is worth having on
both sides.

## Acceptance gate

All four short-bearing cells fail `backtest.acceptance`
(`min_profit_edge_pp: 5.0`). The requested variant fails by the widest margin.
Nothing here changes the standing conclusion: **`long-only, D1 on` is the only
XAU config that survives the protocol.**

## What was kept

The code change stands on its own merits and is retained:

- `use_d1_regime_filter_long` / `use_d1_regime_filter_short`, both defaulting to
  `None` = follow `use_d1_regime_filter`, so **no existing config changes
  behavior**.
- `_needs_d1()` in `pair_registry` so an asymmetric config still loads 1d candles
  (the engine only fetches them when `SymbolContext.timeframe_regime` is set).
- The checklist shows the "D1 Regime" row only for a side that is actually gated.
- 18 tests in `tests/test_cdc_per_side_d1_gate.py`.

Testing the **mirror** alongside the requested variant is what produced the
mechanism. Measuring only the direction that sounded right would have yielded
"fails the gate" with no explanation of why.

## Limitations

- Same as the certification run: one gold cycle, **one** complete bear month, a
  proxy instrument (XAUT-USDT, correlation 0.99/1.00 to XAU-USDT-SWAP), flat
  funding approximation, and a 37/40 bull-month sample.
- The drawdown window is **4 months**. It is the live-relevant slice, not a
  statistically sufficient one, and it is where the mirror's advantage is largest
  — treat the +22.61% as suggestive.
- The mirror is *not* being proposed. It fails the gate (−7.87pp) and has worse
  MDD (14.42% vs 9.22%) than the certified long-only config. It is reported
  because it explains the mechanism, not as a candidate.
- Monthly-reset compounding applies to the drawdown table (each month restarts
  flat); the four-year table is continuous. They are not directly comparable.
