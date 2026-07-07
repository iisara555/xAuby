# Regime Detector Accuracy Report

_Generated from real 4h history in `research_data/` (Global Binance klines).
Reproduce with `scripts/regime_forward_validate.py`._

## What "accuracy" means for a regime detector

A regime classifier is **not** a next-bar price predictor, so "% correct" is the
wrong yardstick — there is no labelled ground truth for "the market was in regime
X". A regime detector earns its keep by three measurable properties instead:

1. **Volatility separation** — do the regimes actually partition the market into
   states with different realised volatility? This is the primary job: it drives
   position sizing and stop width.
2. **Persistence** — does a regime last long enough to act on, or does the label
   flip every bar (churn)?
3. **Directional tilt** — do bull-family regimes out-drift bear-family regimes?
   This is expected to be *small* for any classifier; it is a bonus, not the
   headline.

We quantify (1) and (3) with **eta²** — the fraction of the per-bar return (or
|return|) variance explained by the regime label — and attach a **permutation
p-value** (probability a random relabelling separates the data as well). We
quantify (2) with the mean consecutive-run length and the raw flip rate.

## Headline results

Measured over every 4h bar with the same 250-bar window the live engine feeds
the classifier (PAXGUSDT ≈ 12.8k bars / ~5.8y, BTCUSDT ≈ 17.5k bars / ~8y).

| Metric | PAXGUSDT (gold proxy, live pair) | BTCUSDT (cross-check) |
|---|---|---|
| **Volatility separation** eta² (p) | **0.041** (p = 0.0005) | **0.034** (p = 0.0005) |
| **Directional separation** eta² (p) | 0.0004 (p = 0.85, noise) | **0.0019** (p = 0.0015) |
| **Persistence** (mean run / flip rate) | 6.6 bars / 15.1% | 5.2 bars / 19.2% |
| **Ordering** (bull vs bear drift) | +1.3 vs −0.0 bps ✓ | +5.8 vs +0.5 bps ✓ |
| **GMM cross-check agreement** | 50.0% | 45.2% |

**Verdict:** the detector does its primary job well. Volatility separation is
strong and highly significant on both assets (p ≤ 0.001), regimes are persistent
(mean run > 3 bars, so the 3-candle debounce is well-calibrated), and the bull/bear
drift ordering is correct. The directional edge is weak (near-zero on gold,
small-but-significant on BTC) — which is normal and expected. **Use the regime to
size on volatility, not to predict the next bar's direction.**

## Per-regime breakdown

Columns: `%time` share of bars; `drift_bps` mean next-bar return; `vol%` realised
volatility (stdev of next-bar returns) — **this is the separation that matters**;
`fwd6%` median 6-bar-ahead return; `hit%` P(6-bar return > 0); `run` mean run
length; `t½` implied half-life.

### PAXGUSDT (gold proxy)

| regime | %time | drift_bps | vol% | fwd6% | hit% | run | t½ |
|---|--:|--:|--:|--:|--:|--:|--:|
| BULL_TREND_WEAK | 28.8 | 1.4 | 0.37 | 0.05 | 53 | 10.6 | 7.0 |
| BEAR_TREND_WEAK | 20.5 | −0.3 | 0.34 | 0.00 | 49 | 9.4 | 6.1 |
| SIDEWAYS_CHOP | 16.4 | 0.3 | 0.33 | −0.01 | 47 | 6.7 | 4.3 |
| LOW_VOL_ACCUMULATION | 12.9 | 0.7 | 0.29 | 0.01 | 50 | 5.9 | 3.7 |
| BULL_TREND_STRONG | 6.3 | 2.0 | 0.51 | 0.04 | 51 | 3.8 | 2.3 |
| LOW_VOL_RANGE | 4.7 | 0.4 | 0.34 | 0.05 | 54 | 4.6 | 2.8 |
| VOLATILITY_EXPANSION | 3.0 | 1.7 | 0.64 | 0.04 | 51 | 7.5 | 4.8 |
| BULL_BREAKOUT | 2.3 | −1.5 | 0.91 | 0.03 | 51 | 2.1 | 1.0 |
| BEAR_BREAKDOWN | 1.8 | 2.7 | 0.44 | 0.05 | 52 | 3.2 | 1.9 |
| BEAR_TREND_STRONG | 1.7 | −0.3 | 0.60 | 0.11 | 57 | 3.5 | 2.0 |
| PANIC_SELL | 1.6 | 0.7 | 0.57 | 0.13 | 57 | 4.8 | 3.0 |

The `vol%` column spans ~0.29% (LOW_VOL_ACCUMULATION) to ~0.91% (BULL_BREAKOUT) —
a **~3x spread**, which is the strong volatility separation the eta² captures.

### BTCUSDT (cross-check)

| regime | %time | drift_bps | vol% | fwd6% | hit% | run | t½ |
|---|--:|--:|--:|--:|--:|--:|--:|
| BULL_TREND_STRONG | 18.4 | 6.9 | 1.29 | 0.13 | 53 | 6.8 | 4.4 |
| BULL_TREND_WEAK | 14.9 | 2.9 | 1.06 | 0.09 | 53 | 5.1 | 3.2 |
| SIDEWAYS_CHOP | 14.6 | −0.8 | 1.15 | 0.04 | 51 | 6.0 | 3.8 |
| BEAR_TREND_WEAK | 13.6 | −0.3 | 1.04 | 0.02 | 50 | 5.0 | 3.1 |
| LOW_VOL_RANGE | 9.7 | −1.0 | 1.14 | −0.12 | 47 | 4.7 | 2.9 |
| BEAR_TREND_STRONG | 9.2 | 8.2 | 1.35 | 0.36 | 58 | 5.2 | 3.3 |
| PANIC_SELL | 6.7 | −11.6 | 2.04 | −0.02 | 50 | 8.5 | 5.5 |
| VOLATILITY_EXPANSION | 4.4 | 5.9 | 1.69 | 0.08 | 51 | 5.7 | 3.6 |
| LOW_VOL_ACCUMULATION | 4.1 | −3.0 | 0.89 | −0.10 | 46 | 3.1 | 1.8 |
| BULL_BREAKOUT | 2.4 | 15.4 | 1.72 | 0.46 | 59 | 2.2 | 1.1 |
| BEAR_BREAKDOWN | 1.8 | 11.9 | 1.81 | 0.20 | 53 | 3.0 | 1.7 |

Notable real-market nuance: on BTC the `BEAR_TREND_STRONG` / `BEAR_BREAKDOWN`
regimes carry a **positive** forward drift (mean-reversion bounce after a
flush), while `PANIC_SELL` is the only regime with clearly negative drift
(−11.6 bps) and the highest volatility (2.04%). The detector isolates the
genuinely dangerous state well.

## Independent GMM cross-check

`architecture.regime_statistical_crosscheck` fits an unsupervised Gaussian
mixture over the (trend, log-volatility) plane each tick and maps the result to
the same coarse families as the rule-based regime (UP / DOWN / RANGE / HIGH_VOL).

Over history the two independent methods agree on the coarse family **50.0% of
the time on gold and 45.2% on BTC**. That is deliberately *not* near-100%: the
GMM is a genuine second opinion (it clusters the return distribution, the rule
engine thresholds the EMA stack), so a disagreement is a real signal that the
current read is ambiguous — surfaced as `stat_agreement=False` for an operator or
a confidence gate to act on. It never overrides routing.

## Limitations & honest caveats

- **eta² is small in absolute terms** (~0.04). Markets are mostly noise; no
  regime model explains a large share of bar-to-bar variance. 0.04 with
  p ≤ 0.001 means the separation is real and robust, not that it is large.
- **Gold history is proxied** by PAXGUSDT (the live pair XAUUSDT lacks deep
  history); the backtest proxy is documented in `coin_whitelist.json`.
- **Thresholds are still hand-tuned**, not fit per asset. The GMM cross-check is
  the first step toward a data-driven model; a future step would fit the
  thresholds (or an HMM) per asset and re-run this report to compare.
- Forward returns are **in-sample descriptive statistics**, not a tradeable
  backtest — they characterise the regimes, they do not prove a strategy is
  profitable. Strategy P&L per regime lives in `scripts/regime_fit_benchmark.py`.

## Reproduce

```bash
# Per-symbol separation, persistence, and per-regime forward stats
PYTHONPATH=. python3 scripts/regime_forward_validate.py \
  --csv research_data/backtest_candles_4h_paxgusdt_full.csv --timeframe 4h --horizon 6
PYTHONPATH=. python3 scripts/regime_forward_validate.py \
  --csv research_data/backtest_candles_4h_btcusdt_full.csv  --timeframe 4h --horizon 6
```
