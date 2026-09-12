# Freqtrade community BTC/XAU research — 11 September 2026

Status: completed on 12 September 2026: 14 backtests, four lookahead analyses
and four recursive analyses. **No candidate passed the frozen screen.** All
four are recorded in the research-only `registry.json`, with live weights zero;
none is admitted to the production Arena or certified for trading.
This study does not change the trading engine, tenant settings, production
strategy pool, dependencies of the application, or deployment procedures.

## Results and decision

Returns below include the modeled 0.07% cost per side and available funding,
with zero fallback for missing funding. They are historical simulations, not
expected future returns. Early = 12 March–10 July; late = 10 July–8 September
2026, UTC and end exclusive. Each run has its own 10,000 USDT starting wallet;
do not add these returns as a continuous or multi-strategy portfolio.

| Research candidate | Early return (trades) | Late return (trades) | Late PF | Late max DD | Decision |
| --- | ---: | ---: | ---: | ---: | --- |
| BTC Volatility 1h risk | +1.31% (17) | +3.49% (11) | 3.05 | 0.93% | Insufficient late trades: 11 < 30 |
| BTC FReinforced 5m risk | -1.97% (61) | -1.49% (29) | 0.04 | 1.49% | Negative returns, low PF, insufficient trades |
| XAU Volatility 1h risk | +0.83% (23) | -0.60% (12) | 0.75 | 1.37% | Negative late return, low PF, insufficient trades |
| XAU Volatility 1h session | +2.44% (12) | -0.36% (5) | 0.74 | 1.07% | Negative late return, low PF, insufficient trades |

PF is gross winning-trade PnL divided by absolute losing-trade PnL, using net
trade outcomes. Max DD is the native reported account drawdown, not a guarantee
about tick-level intratrade risk. XAU risk and session each include one early
boundary force exit; no derivative trade remains open. No stress runs were
triggered because no candidate passed the base screen. The original gates,
including at least 30 late trades, have not been weakened after seeing results.

BTC Volatility 1h is the priority for **further research**, not allocation.
Keep it unchanged for the reserved fresh window; no automated job is scheduled.
The other three variants are archived as failed screens. The session filter
improved XAU's early window but did not solve the negative late result.

Regime and side observations are descriptive, not fitted weights. BTC Volatility
risk's directional-entry bucket produced +74.03 USDT from 11 early trades and
+288.71 from six late trades. Entry regimes use the prior completed 1h candle's
20-bar efficiency ratio: at most 0.25 range, at least 0.30 directional, otherwise
mixed. Every label is available before entry. Small buckets cannot establish a
reliable regime allocation. Early SHORT trades made +204.60 while LONG lost
73.69; late LONG made +384.82 while SHORT lost 36.02. This reversal is a reason
not to select a direction or live weight from the same historical sample.

Native numeric-default controls are retained for comparison, not ranked as
deployable candidates: BTC Volatility returned -6.00% / +8.44% (9 / 8 trades),
XAU Volatility -2.99% / +7.24% (19 / 5), and the BTC FReinforced compatibility
control -2.00% / -1.51% (61 / 29). Different leverage, stops and holding periods
make these controls non-risk-matched; their few-trade late gains do not outweigh
their early losses or certify an edge.

## Evidence review

All 24 native commands succeeded without logged fatal errors. An independent
read-only audit reconciled all 301 exported trades across 14 reports to their
native net PnL and final wallets. Trade dates, fees and non-overlapping positions
were checked. All eight derivative runs obeyed 1x leverage, a single entry,
the 25%-of-realized-wallet / 2,500 USDT stake cap, initial -2% stop and at most
48 hours holding. This verifies the exported simulation, not exchange execution.

| Candidate | Lookahead signals tested | Recursive review at actual warmup |
| --- | ---: | --- |
| BTC Volatility risk | 28 | 499 candles: ATR displayed 0.000%; at 199 it was 0.338% |
| BTC FReinforced risk | 30 | 999: resampled SMA displayed -0.000%; it was NaN at 199/499 |
| XAU Volatility risk | 30 | 499: ATR displayed 0.000%; at 199 it was 0.598% |
| XAU Volatility session | 17 | Same 499-candle result as XAU risk |

Native lookahead found zero biased entries/exits in those tested signals. All
four recursive tables were manually reviewed; actual startup values converged
within the displayed 0.001% resolution, not necessarily mathematical zero.
This checks a final indicator endpoint and an early indicator-only lookahead
sample, not every path. Raw `results.json` preserves the harness's
`completed_requires_table_review`; `registry.json` records this subsequent
manual review, the observations and the log hashes without rewriting raw results.

OHLCV audits found no missing, duplicate or invalid candles within 15 February–
8 September: BTC 295,200 one-minute, 59,040 five-minute and 4,920 hourly bars;
XAU 59,040 five-minute and 4,920 hourly bars. Funding files each contain only
288 observations starting 8 June 2026 at 08:00 UTC. Earlier funding is missing,
and first/last timestamps alone do not certify later coverage. This remains a
certification blocker even if a candidate's metrics improve.

## Fixed first-stage comparison

| Pair | Candidate | Base / informative TF | Change from upstream |
| --- | --- | --- | --- |
| BTC/USDT:USDT | CommunityVolatilityRisk | 1h / 3h | Risk bounds, no additions, warmup |
| BTC/USDT:USDT | CommunityReinforcedRisk | 5m / 1h | Risk bounds, warmup; retain ADX exits / ROI |
| XAU/USDT:USDT | CommunityVolatilityRisk | 1h / 3h | Same rules as BTC |
| XAU/USDT:USDT | CommunityVolatilitySession | 1h / 3h | Same risk model, plus fixed London/NY entry sessions |

Three upstream controls (VolatilitySystem on both pairs, FReinforcedStrategy on
BTC) preserve their numeric strategy defaults. Common wallet, proposed stake and costs
do **not** make those controls risk-equivalent: the original volatility system
uses 2x leverage, a very wide stop and can add to a position. Risk derivatives
are separately named and are not represented as upstream performance.

Compatibility exception, documented after the first native attempt: Freqtrade
2026.8 refuses three upstream FReinforced `IntParameter` declarations that lack
an explicit parameter category. Both its control and derivative receive only
`space="buy"` annotations; no values, ranges or signal expressions change and
no hyperopt runs. Original source bytes, executed bytes, both hashes and exact
patches are retained in each artifact. This is a **compatibility control**, not
a claim that the byte-for-byte upstream file executed successfully. A unit test
checks AST equality after removing only those added annotations.

The native 5m and 1h timeframes are intentional. Changing FReinforced to 15m
would also change its higher-timeframe filter to 3h. A 15m adaptation is deferred
until there is evidence justifying a separate, locked comparison.

## Protocol and limitations

`protocol.json` freezes windows, assumptions, sources and gates before runs.
Historical windows are 12 March–10 July and 10 July–8 September (end exclusive).
Both overlap earlier inspected research; neither is described as a fresh
holdout. Each pair/strategy/window starts independently with 10,000 USDT.
No parameter optimization or data-dependent session selection is performed.
The downloader requests one additional day as a boundary buffer because the
native downloader can discard its final candle. Audits and backtests still stop
at the frozen end-exclusive date; no missing candles are fabricated.

Derivative risk: at most 25% of realized wallet (and proposed stake), 1x leverage,
2% stop, no position additions, 48-hour time exit. Gaps and execution costs can
exceed nominal stop risk. Native Freqtrade uses 5m detail for 1h strategies and
1m detail for 5m strategies; this improves intrabar modeling but is not a tick
or order-book simulation. Boundary force exits are reported, not omitted.

Base modeled cost is 0.07% per side, stress 0.12%. The extra cost is a fee proxy
for friction, not reconstructed slippage. Available native funding is used;
missing rates explicitly fall back to zero. Funding coverage is recorded and
incomplete funding blocks certification. Venue history, not gold-token or spot
proxy data, must pass timestamp and OHLCV checks before any screen is run.

Every derivative gets native lookahead and recursive analysis. A missing bias
report is unverified, never passed. Recursive tables require manual review;
these tools only test the observed signals/indicator endpoints, not every
possible market path. Historical metric passers get cost-stress runs but remain
research-only pending bias, funding, native xAuby parity and untouched evidence.
The requested 1999-candle recursive probe exceeds OKX's native 1499-candle
limit. Diagnostic probes are explicitly capped at 1499, with both requested and
effective values reported; strategy warmup remains 499/999. Diagnostics start
after available warmup. Logged native errors fail the command even if Freqtrade
returns process status zero; the failed earlier recursive run is not a pass.

12 September–12 November 2026 is reserved as a future untouched evaluation
window. It is not available now; no automated recurring job or live trade is
scheduled. A strategy change invalidates that pre-registration for the changed
variant. FreqAI is a conditional next phase, NFI a separate reference study and
orderflow requires verified raw-trade history. They are not claimed completed.

## Execution and evidence

`.github/workflows/freqtrade-community-research.yml` runs only on GitHub-hosted
Ubuntu with pinned `freqtrade==2026.8`, read-only repository permissions, no
account credentials and no deployment actions. The harness refuses a local or
self-hosted runner and permits only download/backtest/bias-analysis commands.

The workflow artifact contains `summary.json`, exact protocol, resolved
dependencies, pinned upstream source + hashes, native zipped reports/trades,
data coverage/hashes, cached public data and all command logs. It preserves
failures instead of substituting data or silently tuning a strategy.
Artifacts are retained for 90 days to cover the reserved forward window.

Final evidence: [successful hosted run 34664057485](https://github.com/iisara555/xAuby/actions/runs/34664057485)
and [full artifact](https://github.com/iisara555/xAuby/actions/runs/34664057485/artifacts/10288820301)
(expires 11 December 2026). Branch head was `cddf974`; GitHub executed the PR
merge commit `ea33ad6`. The earlier failed configuration, final-candle,
parameter-metadata and recursive-cap attempts are superseded by this complete
run; they were execution fixes, not signal/parameter optimization.

Durable records alongside this report:

- `results.json`: exact raw final summary, including controls, sides and regimes.
- `registry.json`: research dispositions, rejection reasons and manual bias review.
- `provenance.json`: run, source, protocol and data hashes; funding limitations.
- `trade_ledger.csv` / `native_audit.json`: all 301 selected trade records and
  independent reconciliation; full native order arrays remain in the artifact.
- `dependencies.txt`: exact hosted research environment, not runtime requirements.

The repository protocol byte hash differs from its pretty-printed artifact
copy; the parsed content is identical. Both hashes are recorded explicitly.
The report and registry are documentation only, not inputs to the production
strategy pool. Mapping this boundary with graphify informed that separation.

Sources: [community repository](https://github.com/freqtrade/freqtrade-strategies),
[native backtesting](https://www.freqtrade.io/en/stable/backtesting/),
[lookahead analysis](https://www.freqtrade.io/en/stable/lookahead-analysis/),
[recursive analysis](https://www.freqtrade.io/en/stable/recursive-analysis/).
Upstream is pinned to `f3340ce11f5bdf62f598522e64d1f5638eaa13f5`.
The research strategy subclasses inherit GPL-3.0 upstream code and are provided
under GPL-3.0; they are isolated from xAuby's runtime strategy registry.
