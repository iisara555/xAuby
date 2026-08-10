# How many distinct alphas does xAuby have, and what is worth building next?

- **Run date:** 2026-08-10
- **Data:** OKX `BTC-USDT-SWAP` native 4h, 14,572 bars, 80 calendar months
  (2019-12 → 2026-08). Every registered plugin, frozen default config,
  `enable_short=False`, identical windows.
- **Harness:** `scripts/alpha_correlation.py`
- **Raw:** `core/alpha_btc/alpha_correlation.json`
- **No production config was changed.**

## Verdict, and a correction

The premise this study set out to test — that the plugin registry is a
trend-following monoculture whose members are one bet held several ways — is
**wrong**, and the correlation matrix says so plainly. The highest pairwise
correlation of monthly returns is 0.73 (`xauby_donchian_trend` /
`xauby_smc_pro`). The incumbent pair everyone argues about,
`supertrend_ema200` and `xauby_donchian_trend`, correlate at **0.35**. Most
pairs sit below 0.4, and several are strongly negative.

That reframes the question. Adding an uncorrelated alpha is **not** the binding
constraint, because uncorrelated alphas are already here. Two things follow,
and the first is worth more than anything on the "new strategy" list.

## 1. The cheapest available improvement is combining what already exists

Equal-weight portfolios of existing plugins, monthly returns, 80 months:

| book | total | MDD | avg/mo | vol | **Sharpe** | +months |
|---|---:|---:|---:|---:|---:|---:|
| `supertrend_ema200` alone (**deployed**) | +4.26% | -3.73% | +0.055% | 0.805 | **0.24** | 18/80 |
| `xauby_donchian_trend` alone | +33.18% | -5.78% | +0.379% | 2.071 | 0.63 | 27/80 |
| `xauby_actionzone` alone | +132.24% | -16.59% | +1.240% | 6.152 | 0.70 | 30/80 |
| ST + Donchian | +18.27% | -3.81% | +0.217% | 1.235 | 0.61 | 28/80 |
| **ST + Donchian + ST_short** | +15.96% | **-3.00%** | +0.189% | 0.886 | **0.74** | 34/80 |
| ST + Donchian + SMC + ST_short | +15.39% | **-2.80%** | +0.183% | 0.902 | 0.70 | 38/80 |
| all 11 positive-expectancy | +16.95% | -3.60% | +0.201% | 0.977 | 0.71 | 40/80 |

A three-strategy book **triples the deployed configuration's Sharpe** (0.24 →
0.74) while *lowering* max drawdown (-3.73% → -3.00%) and nearly doubling the
share of positive months (18/80 → 34/80).

This is diversification, not leverage. Running the same combinations at full
size each — not achievable on one account, computed only as a control — leaves
Sharpe identical (0.74 for the three-strategy book either way) and simply
scales return and drawdown together. The ratio improvement is real.

Note what the totals do *not* say: equal weighting cuts each strategy's size,
so the combined book returns less in absolute terms than `xauby_actionzone`
alone. The case for it is risk-adjusted return and a smoother path, the same
case the XAU study made against buy-and-hold.

### Why this is not a shovel-ready recommendation

Three constraints stand between this table and a deployment, and none of them
is a detail:

- **The engine runs one strategy per pair.** `coin_whitelist.json` binds a
  single `strategy` per asset. A multi-strategy book needs either a signal
  aggregation layer or separate accounts, and neither exists.
- **`position_mode: one_way` forbids the best pair.** `supertrend_ema200` and
  `supertrend_short` held simultaneously is a long and a short on the same
  symbol; in one-way mode they net off rather than coexist. The r = -0.04
  between them is exactly why the combination helps, and exactly what the
  account mode cannot express.
- **Two of the four members are not live-eligible.** `supertrend_short` is
  tagged `research` and `xauby_smc_pro` is undeclared, so the maturity gate
  refuses both on a `mode: live` pair. That gate is doing its job; promoting a
  plugin means certifying it, not editing a whitelist.

So the honest framing is that the largest measured improvement available is
blocked by architecture rather than by a missing edge — which is a far better
problem to have than the one this study went looking for.

## 2. What a genuinely new alpha would have to be

Because the existing plugins are already fairly independent, "another
mean-reversion plugin" is weak: the repo has four price-pattern mean-reversion
and squeeze plugins already (`rsi2_meanrev`, `bbrsi_mean_reversion`,
`bbkc_squeeze`, `squeeze_momentum`), they are *already* uncorrelated with the
trend book, and three of the four **lose money** (-6.79%, -18.42%, +2.40%,
+12.66% over 80 months). Diversifying into negative expectancy does not help.

The families that are absent are absent *structurally*: every current plugin is
a function of one symbol's OHLCV. Anything driven by a different input is
orthogonal by construction rather than by measurement, which is a much stronger
guarantee than an observed r = 0.3.

Ranked by expected value per unit of work:

**(a) Funding-rate alpha — the strongest candidate.** The pairs trade OKX
perpetuals and the repo already ingests funding, but only as a *cost guard*
(`max_abs_funding_rate`); it has never been a signal. Funding is a carry
premium, not a price pattern, so it cannot correlate with the trend book by
construction. The classic delta-neutral spot/perp carry trade is not available
here — the bot is perp-only, one venue, one-way mode — but a *directional tilt*
by funding sign and magnitude is, and it reuses data already on hand. The
literature on the delta-neutral version is strong; note that research also
finds the funding component's contribution has diminished over time relative to
price convergence, so this needs measuring on OKX's own funding history rather
than assuming the published Sharpe transfers.

**(b) Session / time-of-day seasonality — the cheapest to test.** Alpha from
*when* rather than *what*, orthogonal to every price-derived plugin by
definition, and requiring no new data at all: the bar timestamps are already
there. Published work on BTC finds concentrated returns in the 21:00–23:00 UTC
window. A 4h series gives six buckets per day, enough to test the effect
coarsely; a real test wants 1h bars. Cheap enough that a negative result is
still worth having.

**(c) Cross-asset XAU↔BTC relative value.** Both pairs already trade and their
decisions are completely independent — neither strategy has ever seen the other
symbol. Relative value is a different bet from directional exposure, and the
data requires no new source.

**(d) Another price-pattern mean-reversion or squeeze plugin — deprioritised.**
This is the one the question started from, and the evidence is against it: the
category is represented, already uncorrelated, and mostly unprofitable here.
If it is pursued, the interesting variant is the *conditional* form the repo
lacks — mean-revert at local minima, trend-follow at local maxima, which
published work on BTC reports as an asymmetry with a short (10-day) lookback —
rather than a fifth symmetric oscillator.

## Method and limitations

- **Correlation is over calendar-month returns.** It is blind to intra-month
  path and treats a three-trade month and a no-trade month as equal
  observations.
- **Low correlation is not an inactivity artifact — this was checked.**
  Recomputing every pair over only the months where *both* strategies actually
  traded moves the numbers by at most 0.08 (e.g. donchian/smc_pro 0.73 → 0.73,
  bbrsi/supertrend_short -0.65 → -0.68). Plugins that traded in fewer than
  twelve months were excluded outright.
- **Monthly-window returns are not a continuous equity curve.** Each window
  starts flat, so the MDD column understates a real drawdown — `xauby_actionzone`
  shows -16.59% here against -23.68% in the continuous BTC replay. Use these
  for comparison between books, not as a risk figure.
- **One symbol, one timeframe, one venue, long-only defaults.** The short-only
  plugins still traded (their entry logic is not governed by `enable_short`),
  which is why `supertrend_short` appears with a positive total; that result
  deserves its own verification before being leaned on.
- **80 monthly observations spanning ~6 years are not 80 independent draws.**
  Regimes persist, so the effective sample behind every Sharpe here is smaller
  than the row count suggests.

## Suggested order of work

1. **Establish whether a multi-strategy book is reachable at all** — the
   one-way-mode and one-strategy-per-pair constraints decide whether the
   biggest measured win is available. This is an architecture question, not a
   research one, and it should be answered before more alpha hunting.
2. **Funding-rate signal study** on OKX funding history, as a new alpha with a
   structural orthogonality guarantee.
3. **Session seasonality probe** — cheap, and a clean negative result closes a
   question permanently.
4. Cross-asset XAU↔BTC, then conditional local-extreme mean reversion.

## Reproduction

```bash
PYTHONPATH=. python3 scripts/alpha_correlation.py --symbol BTCUSDT \
    --workers 4 --out-dir core/alpha_btc
```

Roughly 25 minutes on four cores.
