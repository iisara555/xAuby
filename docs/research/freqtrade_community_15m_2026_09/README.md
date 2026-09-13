# Community 15m BTC / XAU — frozen exploratory study

Registered 2026-09-13, before this study's first hosted execution. Four candidates,
all research-only, live weight **0**, no production registry admission or deploy.
See [protocol.json](protocol.json) for the exact rules and reserved future window.

| Candidate | Execution / context | Difference |
|---|---|---|
| BTC Volatility | 15m / 1h | ATR14 x 2 hourly impulse, bounded risk |
| BTC FReinforced | 15m / fixed 1h | EMA8/21, hourly SMA50, native ADX exit/ROI |
| XAU Volatility | 15m / 1h | Same rules as BTC Volatility |
| XAU Volatility session | 15m / 1h | Same XAU rules, fixed London/NY entry windows |

## Source and changes

Upstream [VolatilitySystem](https://github.com/freqtrade/freqtrade-strategies/blob/f3340ce11f5bdf62f598522e64d1f5638eaa13f5/user_data/strategies/futures/VolatilitySystem.py)
and [FReinforcedStrategy](https://github.com/freqtrade/freqtrade-strategies/blob/f3340ce11f5bdf62f598522e64d1f5638eaa13f5/user_data/strategies/futures/FReinforcedStrategy.py)
are pinned at the same commit as the earlier Community study. Derivatives are
GPL-3.0; the native artifact includes upstream LICENSE, pristine sources,
executed sources, every patch, and SHA-256 hashes.

This is not a CLI timeframe override alone: the harness changes exactly one
resampling expression in each upstream module to fixed 60 minutes. Subclasses
set 15m execution and the prior frozen risk model (1x, no adds, stake <= min of
2500 USDT and 25% realized wallet, 2% price stop, 48h timeout). FReinforced retains
three previously reviewed parameter-space compatibility annotations; no hyperopt.
Volatility retains the upstream base-row ATR shift and persistent signals, not
a newly invented crossover. Session filtering uses the next 15m open clock and
does not suppress opposite-signal exits.

For Volatility this changes both execution and informative timeframes. Previous
5m/1h results have different detail resolution and funding snapshots. They are
context, not a controlled measurement of the effect of execution timeframe alone.

## Evaluation and limitations

Native Freqtrade 2026.8 on GitHub-hosted Ubuntu only; OKX BTC/USDT:USDT and
XAU/USDT:USDT swaps, no spot/gold-token proxy. Eight base backtests (four variants
times two windows), all with native 1m detail. Conditional higher-cost tests for
base-screen passers; lookahead and recursive checks for every variant.

Historical windows March 12–July 10 and July 10–September 8, 2026 (exclusive ends)
were already inspected in other research: neither is a new holdout. Do not fit
parameters, direction, sessions, or portfolio weights to these results. Side and
prior-completed-hour ER20 regime breakdowns are descriptive only.

Costs: assumed 0.07% per side, stress 0.12% per side; friction is a fee proxy,
not market impact. Missing funding uses explicit zero fallback and blocks
certification. Native simulation cannot guarantee fills, liquidity or profit.
Independent 10,000 USDT wallets per run; returns are not an additive portfolio.

Fresh forward reservation: **2026-09-14 to 2026-11-14 exclusive**, unchanged
finalists only, after completion. No recurring job or live trading is scheduled.

## Execution

The dedicated `Freqtrade community 15m research (no live trading)` PR workflow
installs dependencies in an isolated hosted job, uses public market data and
blank credentials, and retains artifacts for 90 days even on failure.

Do not run the harness on the VPS. Local verification is restricted to focused
offline tests:

```bash
PYTHONPATH=. python3 -m pytest -q tests/test_freqtrade_community_15m_research.py tests/test_freqtrade_community_research.py
```

## Completed results — 2026-09-13

**All four candidates are rejected by the frozen base screen. All live weights
remain zero. No candidate advances to cost stress or the reserved forward stage.**

[Hosted run 34732740878](https://github.com/iisara555/xAuby/actions/runs/34732740878)
completed successfully at 02:49 UTC: 2 data downloads, 8 base backtests, 4
lookahead analyses and 4 recursive analyses. All 18 native commands succeeded.
Operational success is not strategy qualification.

Returns below are net simulated wallet returns after the assumed 0.07% per-side
cost and available funding. Early = Mar 12–Jul 10; late = Jul 10–Sep 8, 2026,
exclusive ends. Each run starts with its own 10,000 USDT wallet.

| Candidate | Early return | Early trades | Late return | Late trades | Late PF | Max DD early / late |
|---|---:|---:|---:|---:|---:|---:|
| BTC Volatility 15m / 1h | -4.5741% | 46 | +1.9489% | 16 | 1.5622 | 5.6705% / 1.7278% |
| BTC FReinforced 15m / 1h | +0.6840% | 11 | -0.2263% | 5 | 0.0714 | 0.1479% / 0.2263% |
| XAU Volatility 15m / 1h | -0.5395% | 56 | -1.0221% | 28 | 0.7587 | 3.0804% / 1.7850% |
| XAU Volatility 15m / 1h session | +0.1305% | 21 | -2.3874% | 15 | 0.4032 | 1.6284% / 2.7560% |

The gate requires positive returns in both windows, late PF >= 1.2 and at least
30 late trades, among other risk checks. BTC Volatility fails early return and
late sample size. Both other families fail late return, sample size and PF;
unfiltered XAU also loses in the early window. Low drawdown does not erase those
failures. The two XAU early runs each include one forced boundary exit; none of
the eight runs has unclosed trades.

### Bias and warmup: three additional failures

Native lookahead found no bias in the tested signals: 30 for BTC Volatility,
16 for BTC FReinforced, 30 for XAU Volatility and 30 for XAU session. FReinforced
exceeded the 10-trade minimum but did not reach the 30-signal target. These finite
checks do not prove that every possible signal is bias-free; the diagnostic also
overrides wallet/stake settings and is not a performance run.

The preregistered recursive tolerance is **0.001%** at the actual strategy
startup, not at a more favorable probe selected after results:

| Candidate | Actual startup | Signal indicator difference | Decision |
|---|---:|---|---|
| BTC Volatility | 499 | ATR and resampled ATR **-0.003%** | Fail |
| BTC FReinforced | 999 | No reported differences for signal indicators | Pass at printed precision only |
| XAU Volatility | 499 | ATR and resampled ATR **-0.007%** | Fail |
| XAU session | 499 | ATR and resampled ATR **-0.007%** | Fail |

The 999/1499 Volatility probes display +/-0.000%, but the actual 499 warmup is
unchanged. These are sensitivity observations, not permission to rerun with a
better-looking warmup. A technically revised variant would require a new frozen
protocol and untouched evaluation data. Full native table excerpts are in
[recursive_tables.json](recursive_tables.json); [registry.json](registry.json)
records manual decisions separately from the untouched native summary.

### Direction and regime observations, not new strategies

BTC Volatility's late longs made +339.29 USDT over 9 trades, while shorts lost
144.40 USDT over 7. Its early long and short groups both lost money. The late
range-tagged trades made +355.02 USDT over 8 trades, but early range-tagged trades
lost 227.00 USDT over 18. This is not evidence to switch to long-only or range-only
after seeing the result. Regimes are the fixed, prior-completed-hour ER20 labels,
not an assertion that this volatility model consistently profits in ranges.

The XAU session restriction improves early return slightly but worsens the late
window versus unfiltered XAU. BTC FReinforced has only 16 trades across both
independent windows. No long/short or regime subgroup is admitted, and no weights
are fitted. Full side-by-regime breakdowns remain in [results.json](results.json).

### Data and independent reconciliation

For each pair the audit covers 295,200 one-minute bars, 19,680 fifteen-minute
bars and 4,920 hourly bars from Feb 15 to Sep 8 exclusive: no missing bars,
duplicates or invalid OHLCV; no off-grid 15m dates. Every audited candle/funding
file retained its original audit hash after the full run.

Funding files contain 291 observations each, beginning **2026-06-08 08:00 UTC**
and ending Sep 13 00:00 UTC. Earlier required funding is missing and uses the
explicit zero fallback. First/last timestamps do not certify internal coverage.
Neither the recorded returns nor a positive subgroup is certification-ready.

The independent read-only audit reconciles **198 trades in eight native ZIP
reports**. Recomputed price PnL minus entry/exit fees plus signed funding matches
every native trade within 0.00000001 USDT. It also checks actual 15m/1m settings,
window bounds, wallet totals, no overlapping positions or additions, 1x leverage,
stake caps, 2% stop configuration, 48h duration and allowed session entry clocks.
All these accounting/execution-contract checks pass; this does not override the
profitability or recursive failures. See [native_audit.json](native_audit.json).

The raw [artifact](https://github.com/iisara555/xAuby/actions/runs/34732740878/artifacts/10310122754)
expires 2026-12-12. Durable evidence here includes the unmodified native summary,
198-row [trade ledger](trade_ledger.csv) (line endings normalized only), exact
hosted dependency freeze, source/run/data hashes in [provenance.json](provenance.json),
recursive excerpts and the research-only registry. No raw runtime/account files
are committed. The original and LONA studies remain unchanged.

## Decision and next action

Close this frozen round as **rejected**, with no new live allocation and no
automatic forward job. Do not retry parameter/session/direction combinations on
the same inspected windows. Any further 15m study should state a new hypothesis,
correct warmup requirements before viewing results, and reserve genuinely fresh
data. This result rejects these four variants, not every possible 15m strategy.

The harness PR passed 1,708 Python tests and 117 subtests on hosted CI. The
separate evidence PR adds independent audit tests without rerunning or modifying
the frozen backtest. No live config, weights, engine restart or deployment changed.
