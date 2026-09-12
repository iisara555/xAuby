# Freqtrade community BTC/XAU research — 11 September 2026

Status: protocol locked; results pending. All candidate live weights are zero.
This study does not change the trading engine, tenant settings, production
strategy pool, dependencies of the application, or deployment procedures.

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

Sources: [community repository](https://github.com/freqtrade/freqtrade-strategies),
[native backtesting](https://www.freqtrade.io/en/stable/backtesting/),
[lookahead analysis](https://www.freqtrade.io/en/stable/lookahead-analysis/),
[recursive analysis](https://www.freqtrade.io/en/stable/recursive-analysis/).
Upstream is pinned to `f3340ce11f5bdf62f598522e64d1f5638eaa13f5`.
The research strategy subclasses inherit GPL-3.0 upstream code and are provided
under GPL-3.0; they are isolated from xAuby's runtime strategy registry.
