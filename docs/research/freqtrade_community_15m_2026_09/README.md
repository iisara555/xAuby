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

Native evidence will be recorded separately after the hosted run; pending is
not passed. This study does not change live config, weights or strategy code.
